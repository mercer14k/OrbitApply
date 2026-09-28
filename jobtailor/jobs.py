from __future__ import annotations

import io
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import pandas as pd
from bs4 import BeautifulSoup

from .models import JobListing, SearchSettings
from .utils import canonical_url, stable_listing_id


class JobSearchError(RuntimeError):
    pass


ProgressCallback = Callable[[int, int, str], None]


def _clean(value: object) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _clean_description(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _as_bool(value: object) -> bool | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "remote"}:
        return True
    if normalized in {"false", "0", "no", "onsite", "on-site"}:
        return False
    return None


def _date_string(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return text
    return parsed.date().isoformat()


def _is_recent(value: str, days_old: int, include_unknown: bool) -> bool:
    if not value:
        return include_unknown
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return include_unknown
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days_old)).date()
    return parsed.date() >= cutoff_date


def _row_to_job(row: pd.Series, keyword: str, country: str) -> JobListing | None:
    url = (
        _clean(row.get("job_url_direct"))
        or _clean(row.get("job_url"))
        or _clean(row.get("url"))
    )
    title = _clean(row.get("title"))
    company = _clean(row.get("company"))
    if not url or not title or not company:
        return None
    location = _clean(row.get("location"))
    source = _clean(row.get("site")) or _clean(row.get("source"))
    date_value = row.get("date_posted")
    if not _clean(date_value):
        date_value = row.get("posted_at")
    return JobListing(
        listing_id=stable_listing_id(url, company, title, location),
        title=title,
        company=company,
        location=location,
        country=country,
        date_posted=_date_string(date_value),
        description=_clean_description(row.get("description")),
        source_urls=list(
            dict.fromkeys(
                value
                for value in (
                    _clean(row.get("job_url_direct")),
                    _clean(row.get("job_url")),
                    _clean(row.get("url")),
                )
                if value
            )
        ),
        job_url=canonical_url(url),
        source=source,
        is_remote=_as_bool(row.get("is_remote")),
        search_keyword=keyword,
    )


def search_jobs(
    settings: SearchSettings,
    progress: ProgressCallback | None = None,
    cancel_event=None,
    partial=None,
) -> tuple[list[JobListing], list[str]]:
    """Search supported boards through python-jobspy and normalize the results."""
    try:
        from jobspy import scrape_jobs
    except ImportError as exc:
        raise JobSearchError(
            "python-jobspy is not installed. Activate the app environment and run pip install -r requirements.txt."
        ) from exc

    jobs_by_key: dict[str, JobListing] = {}
    warnings: list[str] = []
    keywords = list(
        dict.fromkeys(item.strip() for item in settings.keywords if item.strip())
    )
    if not keywords:
        raise JobSearchError("Select at least one search keyword.")
    if not settings.sites:
        raise JobSearchError("Select at least one job source.")

    for index, keyword in enumerate(keywords, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress:
            progress(index - 1, len(keywords), f"Searching for {keyword}")
        if cancel_event is not None and cancel_event.is_set():
            break
        kwargs: dict[str, object] = {
            "site_name": settings.sites,
            "search_term": keyword,
            "location": settings.location,
            "results_wanted": settings.results_per_keyword,
            "hours_old": settings.days_old * 24,
            "country_indeed": settings.country,
            "description_format": "markdown",
            "verbose": 0,
        }
        if settings.remote_only:
            kwargs["is_remote"] = True
        if "linkedin" in settings.sites:
            kwargs["linkedin_fetch_description"] = True

        try:
            frame = scrape_jobs(**kwargs)
        except Exception as exc:  # noqa: BLE001 - third-party scrapers raise inconsistent exceptions
            warnings.append(f"{keyword}: {type(exc).__name__}: {str(exc)[:240]}")
            if partial:
                partial(list(jobs_by_key.values()), warnings)
            continue
        if cancel_event is not None and cancel_event.is_set():
            break  # A response arriving after cancellation does not change results.
        if frame is None or frame.empty:
            warnings.append(f"{keyword}: no results returned")
            if partial:
                partial(list(jobs_by_key.values()), warnings)
            continue
        for _, row in frame.iterrows():
            if cancel_event is not None and cancel_event.is_set():
                break
            job = _row_to_job(row, keyword, settings.country)
            if job is None:
                continue
            if not _is_recent(
                job.date_posted, settings.days_old, settings.include_unknown_dates
            ):
                continue
            key = canonical_url(job.job_url) or job.listing_id
            existing = jobs_by_key.get(key)
            if existing is not None:
                combined = list(
                    dict.fromkeys([*existing.source_urls, *job.source_urls])
                )
                existing.source_urls = job.source_urls = combined
            if existing is None or len(job.description) > len(existing.description):
                jobs_by_key[key] = job
        if partial:
            partial(list(jobs_by_key.values()), warnings)
        if index < len(keywords):
            if cancel_event is not None:
                cancel_event.wait(0.5)
            else:
                time.sleep(0.5)

    if progress:
        cancelled = cancel_event is not None and cancel_event.is_set()
        progress(
            len(keywords),
            len(keywords),
            "Search cancelled" if cancelled else "Search complete",
        )
    jobs = sorted(
        jobs_by_key.values(),
        key=lambda item: (
            item.date_posted or "0000-00-00",
            item.company.casefold(),
            item.title.casefold(),
        ),
        reverse=True,
    )
    return jobs, warnings


def jobs_from_csv(data: bytes, default_country: str = "") -> list[JobListing]:
    frame = pd.read_csv(io.BytesIO(data))
    normalized = {str(column).strip().casefold(): column for column in frame.columns}
    aliases = {
        "title": ["title", "job_title", "role"],
        "company": ["company", "company_name", "employer"],
        "job_url": ["job_url", "url", "apply_url", "link"],
        "location": ["location", "job_location"],
        "date_posted": ["date_posted", "posted_at", "posted_date", "date"],
        "description": ["description", "job_description", "jd"],
        "source": ["source", "site"],
        "country": ["country"],
        "is_remote": ["is_remote", "remote"],
    }

    def resolve(name: str) -> str | None:
        for alias in aliases[name]:
            if alias in normalized:
                return normalized[alias]
        return None

    required = {name: resolve(name) for name in ("title", "company", "job_url")}
    missing = [name for name, column in required.items() if column is None]
    if missing:
        raise JobSearchError("CSV is missing required columns: " + ", ".join(missing))

    jobs: list[JobListing] = []
    for _, row in frame.iterrows():
        mapped: dict[str, object] = {}
        for name in aliases:
            column = resolve(name)
            mapped[name] = row[column] if column is not None else ""
        if not _clean(mapped["country"]):
            mapped["country"] = default_country
        series = pd.Series(
            {
                "title": mapped["title"],
                "company": mapped["company"],
                "job_url": mapped["job_url"],
                "location": mapped["location"],
                "date_posted": mapped["date_posted"],
                "description": mapped["description"],
                "site": mapped["source"] or "CSV import",
                "is_remote": mapped["is_remote"],
            }
        )
        job = _row_to_job(series, "CSV import", _clean(mapped["country"]))
        if job:
            jobs.append(job)
    return deduplicate_jobs(jobs)


def deduplicate_jobs(jobs: Iterable[JobListing]) -> list[JobListing]:
    result: dict[str, JobListing] = {}
    for job in jobs:
        key = canonical_url(job.job_url) or job.listing_id
        existing = result.get(key)
        if existing is not None:
            combined = list(dict.fromkeys([*existing.source_urls, *job.source_urls]))
            existing.source_urls = job.source_urls = combined
        if existing is None or len(job.description) > len(existing.description):
            result[key] = job
    return list(result.values())


def keyword_relevance(job: JobListing, keywords: list[str]) -> int:
    """Return a transparent discovery score, not a claim about candidate qualification."""
    title = job.title.casefold()
    description = job.description.casefold()
    location = job.location.casefold()
    if not keywords:
        return 0
    points = 0.0
    possible = 0.0
    for keyword in keywords:
        normalized = keyword.strip().casefold()
        if not normalized:
            continue
        possible += 5.0
        if normalized in title:
            points += 4.0
        elif normalized in description:
            points += 2.0
        else:
            tokens = [
                token
                for token in re.findall(r"[a-z0-9+#.]+", normalized)
                if len(token) > 1
            ]
            if tokens:
                overlap = sum(
                    token in title or token in description for token in tokens
                ) / len(tokens)
                points += min(2.0, overlap * 2.0)
        if normalized in location:
            points += 1.0
    if possible == 0:
        return 0
    return min(100, round(points / possible * 100))


def fetch_job_description(url: str, timeout_seconds: int = 25) -> str:
    from .job_details import retrieve

    description, _, _ = retrieve(JobListing(title="", company="", job_url=url))
    return description


def enrich_job_description(
    job: JobListing, *, use_browser: bool = False, force_browser: bool = False
) -> JobListing:
    from .job_details import refresh_description

    return refresh_description(
        job, use_browser=use_browser, force_browser=force_browser
    )
