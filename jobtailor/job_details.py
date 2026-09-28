"""Retrieve the original listing on every run, with bounded, public-page adapters."""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .models import JobListing


class JobDescriptionError(ValueError):
    pass


def clean_html(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for node in soup.select("script, style, nav, footer, header, form"):
        node.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True)).strip()


def read_public(url: str, timeout: int = 25) -> tuple[str, str]:
    if urlsplit(url).scheme not in {"https", "http"}:
        raise JobDescriptionError("Use an HTTP(S) application URL.")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise JobDescriptionError(
                "The response exceeded 4 MB; a truncated page was not accepted."
            )
        return raw.decode(
            response.headers.get_content_charset() or "utf-8", errors="replace"
        ), response.url


def workday_endpoint(url: str) -> str | None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host.endswith(".myworkdayjobs.com"):
        return None
    path = parts.path.split("/")
    if "job" not in path:
        return None
    index = path.index("job")
    if index < 2:
        return None
    tenant, site = host.split(".")[0], path[index - 1]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            f"/wday/cxs/{tenant}/{site}/" + "/".join(path[index:]),
            "",
            "",
        )
    )


def jobpostings(value):
    if isinstance(value, dict):
        kind = value.get("@type", [])
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            yield value
        else:
            for child in value.values():
                yield from jobpostings(child)
    elif isinstance(value, list):
        for child in value:
            yield from jobpostings(child)


def title_matches(expected: str, actual: str) -> bool:
    def tokens(value):
        value = re.sub(r"\bsr\.?\b", "senior", value.casefold())
        value = re.sub(r"\bjr\.?\b", "junior", value)
        return set(re.findall(r"\w+", value))

    wanted, found = tokens(expected), tokens(actual)
    return not expected or bool(found) and wanted <= found


def retrieve(job: JobListing, *, rendered: bool = False) -> tuple[str, str, list[str]]:
    """Read fresh HTML plus Workday JSON. Never return a search snippet as verified."""
    notes, candidates, links = [], [], []
    sponsorship_statements = []
    final_url = job.job_url
    try:
        if rendered:
            from .browser_fetch import render_public

            body, final_url = render_public(job.job_url)
        else:
            body, final_url = read_public(job.job_url)
        soup = BeautifulSoup(body, "html.parser")
        postings = []
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                postings.extend(jobpostings(json.loads(node.get_text())))
            except (ValueError, TypeError):
                continue
        for posting in postings:
            if title_matches(job.title, str(posting.get("title", ""))) and posting.get(
                "description"
            ):
                candidates.append(
                    (
                        3,
                        clean_html(str(posting["description"])),
                        final_url + " [JobPosting]",
                    )
                )
            else:
                notes.append("Skipped structured data for a different or empty job.")
        for selector in [
            "#job-description",
            "#jobDescriptionText",
            ".job-description",
            ".jobDescription",
            "[data-testid='job-description']",
            "[data-automation-id='jobPostingDescription']",
            ".posting-page",
            ".job-details",
            "[itemprop='description']",
        ]:
            nodes = soup.select(selector)
            if nodes:
                candidates.append(
                    (
                        2,
                        "\n\n".join(clean_html(str(n)) for n in nodes),
                        final_url + " [job description]",
                    )
                )
        heading = soup.find("h1")
        matching_page = bool(
            heading and title_matches(job.title, heading.get_text(" ", strip=True))
        )
        if matching_page or (
            len(postings) == 1
            and title_matches(job.title, str(postings[0].get("title", "")))
        ):
            # Some employers put eligibility in a footer outside their JD node.
            # Keep those statements from this role page, excluding related-job cards.
            for text_node in soup.find_all(
                string=re.compile(r"sponsor|h[- ‑]?1b", re.IGNORECASE)
            ):
                if any(
                    parent.name in {"script", "style", "nav", "aside"}
                    or re.search(
                        r"related|recommend|similar|job-card|other-jobs",
                        " ".join(parent.get("class", []))
                        + " "
                        + str(parent.get("id", "")),
                        re.IGNORECASE,
                    )
                    for parent in text_node.parents
                ):
                    continue
                block = text_node.find_parent(
                    ["p", "li", "label", "div", "section", "footer"]
                )
                statement = (
                    block.get_text(" ", strip=True) if block else str(text_node).strip()
                )
                if statement and statement not in sponsorship_statements:
                    sponsorship_statements.append(statement)
        if heading and title_matches(job.title, heading.get_text(" ", strip=True)):
            main = soup.find("main") or soup.find("article")
            if main:
                candidates.append(
                    (1, clean_html(str(main)), final_url + " [role page]")
                )
        # Canonical links are followed only on the same host. Public Workday apply
        # links are followed only when their exact requisition ID is in this URL.
        for node in soup.select('link[rel="canonical"][href], a[href], iframe[src]'):
            href = urljoin(final_url, node.get("href", node.get("src", "")))
            same_host = urlsplit(href).netloc == urlsplit(final_url).netloc
            req_id = re.search(r"JR\d+", job.job_url, re.IGNORECASE)
            same_workday_job = (
                workday_endpoint(href)
                and req_id
                and req_id.group().casefold() in href.casefold()
            )
            from .ats_sources import ats_endpoint

            trusted_ats = ats_endpoint(href) and (
                node.name == "iframe"
                or "apply" in node.get_text(" ", strip=True).casefold()
            )
            if (
                node.name == "link" and same_host or same_workday_job or trusted_ats
            ) and href not in {
                job.job_url,
                final_url,
            }:
                links.append(href)
        if not candidates:
            notes.append(
                "No role-specific description found in the HTML. Generic page text was not treated as a JD."
            )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        notes.append(f"Application-page retrieval failed: {type(exc).__name__}: {exc}")

    endpoint = workday_endpoint(final_url) or workday_endpoint(job.job_url)
    from .ats_sources import ats_endpoint, fetch_ats

    for ats_url in dict.fromkeys([final_url, job.job_url, *links[:2]]):
        if ats_endpoint(ats_url):
            try:
                fetched = fetch_ats(ats_url, job.title, read_public)
                if fetched and fetched[0]:
                    candidates.append((4, fetched[0], fetched[1]))
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                notes.append(
                    f"ATS detail retrieval failed: {type(exc).__name__}: {exc}"
                )
    if endpoint:
        try:
            body, _ = read_public(endpoint)
            posting = json.loads(body).get("jobPostingInfo", {})
            if not title_matches(job.title, posting.get("title", "")):
                raise JobDescriptionError("Workday returned a different job title.")
            expected_req = re.search(r"JR\d+", job.job_url, re.IGNORECASE)
            if (
                expected_req
                and posting.get("jobReqId")
                and expected_req.group().casefold() != posting["jobReqId"].casefold()
            ):
                raise JobDescriptionError("Workday returned a different requisition.")
            if posting.get("jobDescription"):
                candidates.append((4, clean_html(posting["jobDescription"]), endpoint))
            else:
                notes.append("Workday did not provide a jobDescription field.")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            notes.append(
                f"Workday detail retrieval failed: {type(exc).__name__}: {exc}"
            )
    # One verified canonical hop, no recursive crawl or similar-job substitution.
    if not candidates and links:
        linked = job.model_copy(update={"job_url": links[0]})
        # Only extract directly from this single additional response.
        try:
            body, url = read_public(workday_endpoint(linked.job_url) or linked.job_url)
            try:
                value = json.loads(body).get("jobPostingInfo", {})
                if title_matches(job.title, value.get("title", "")) and value.get(
                    "jobDescription"
                ):
                    candidates.append((4, clean_html(value["jobDescription"]), url))
            except ValueError:
                soup = BeautifulSoup(body, "html.parser")
                for node in soup.select('script[type="application/ld+json"]'):
                    try:
                        for value in jobpostings(json.loads(node.get_text())):
                            if title_matches(
                                job.title, value.get("title", "")
                            ) and value.get("description"):
                                candidates.append(
                                    (3, clean_html(value["description"]), url)
                                )
                    except ValueError:
                        continue
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            notes.append(
                f"Linked employer page unavailable: {type(exc).__name__}: {exc}"
            )
    candidates = [item for item in candidates if item[1].strip()]
    if candidates:
        # A short JSON-LD summary must not hide a longer role-specific description.
        # Prefer explicit description nodes over page-wide fallbacks, then the
        # fullest available candidate, using source type only to break ties.
        explicit = [item for item in candidates if item[0] > 1]
        _, text, url = max(
            explicit or candidates, key=lambda item: (len(item[1]), item[0])
        )
        for statement in sponsorship_statements:
            if " ".join(statement.split()) not in " ".join(text.split()):
                text += "\n\n" + statement
        if sponsorship_statements:
            notes.append(
                f"Sponsorship statements outside the description were also checked on {final_url}."
            )
        return text, url, notes
    return "", "", notes


def refresh_description(
    job: JobListing, *, use_browser: bool = False, force_browser: bool = False
) -> JobListing:
    description, source, notes = retrieve(job)  # Always attempt, even for manual JDs.
    has_details = bool(
        re.search(
            r"responsibilit|qualification|required|\b(?:design|develop|manage|analy[sz]e|perform|experience)\b",
            description,
            re.IGNORECASE,
        )
    )
    if (
        use_browser
        and (force_browser or not has_details)
        and not job.description_override.strip()
    ):
        try:
            rendered, rendered_source, rendered_notes = retrieve(job, rendered=True)
            notes.extend(rendered_notes)
            if rendered and (force_browser or len(rendered) > len(description)):
                description, source = rendered, rendered_source + " [browser rendered]"
        except Exception as exc:  # noqa: BLE001 - Playwright exceptions vary by runtime
            notes.append(f"Browser fallback failed: {type(exc).__name__}: {exc}")
    job.description_checked_at = datetime.now(timezone.utc).isoformat()
    job.retrieval_notes = notes
    if job.description_override.strip():
        job.description = job.description_override.strip()
        job.description_source = "User-provided full JD"
        job.description_status = "Pasted JD; requirements review needed"
        if description:
            job.retrieval_notes.append(
                "Employer page checked; explicit user-provided JD retained."
            )
    elif description:
        job.description = description
        job.description_source = source
        job.description_status = "Fetched; requirements review needed"
    else:
        job.description_status = "Retrieval failed; paste full JD"
        job.description_source = "Unverified listing snippet"
        raise JobDescriptionError(
            "Could not retrieve a role-specific employer description. Paste the complete JD in Review job description; the listing snippet was not accepted."
        )
    return job
