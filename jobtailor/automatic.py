"""One-run resume -> discovery -> application folders workflow."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .ai_tasks import (
    audit_and_repair_draft,
    extract_candidate_profile,
    suggest_keywords,
    tailor_resume,
)
from .exporter import (
    _write_apply_files,
    application_folder_name,
    create_application_package,
)
from .generation_guard import one_job_at_a_time
from .job_details import JobDescriptionError
from .jobs import (
    deduplicate_jobs,
    enrich_job_description,
    keyword_relevance,
    search_jobs,
)
from .models import CandidateProfile, KeywordSuggestion, SearchSettings
from .requirements_analysis import analyze_requirements
from .resume_parser import parse_resume_bytes, profile_coverage
from .sponsorship import INCLUDE_ALL, included_by_sponsorship_filter, screen_sponsorship
from .utils import ensure_unique_folder, write_json


def _context_headroom_report(client) -> dict | None:
    """Read optional diagnostics without making instrumentation a dependency."""
    reporter = getattr(client, "context_headroom_report", None)
    if not callable(reporter):
        return None
    try:
        report = reporter()
    except Exception:  # noqa: BLE001 - diagnostics must never break an application folder
        return None
    return report if isinstance(report, dict) else None


def automatic_keywords(
    profile: CandidateProfile, suggestions: list[KeywordSuggestion]
) -> list[KeywordSuggestion]:
    """Retain every distinct source title and skill as well as AI suggestions."""
    source = [
        KeywordSuggestion(keyword=e.title, category="Job title")
        for e in profile.experiences
        if e.title.strip()
    ]
    source += [
        KeywordSuggestion(keyword=s, category="Functional skill")
        for s in profile.skills
        if s.strip()
    ]
    result, seen = [], set()
    for item in [*source, *suggestions]:
        key = " ".join(item.keyword.split()).casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(
                item.model_copy(update={"keyword": " ".join(item.keyword.split())})
            )
    return result


def search_queries(keywords: list[KeywordSuggestion]) -> list[str]:
    titles = [k.keyword for k in keywords if k.category == "Job title"]
    # A standalone generic skill (e.g. Excel) attracts unrelated occupations.
    # Keep every keyword, but anchor skill queries to the candidate's first role.
    queries = list(titles)
    for item in keywords:
        if item.category != "Job title":
            queries.append(f"{titles[0]} {item.keyword}" if titles else item.keyword)
    return list(dict.fromkeys(queries))


def save_index(root: Path, report: dict) -> None:
    write_json(root / "Run_Report.json", report)
    rows = []
    for row in report["roles"]:
        folder = quote(Path(row["folder"]).name) + "/" if row.get("folder") else ""
        folder_link = f'<a href="{folder}">Open folder</a>' if folder else ""
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(row.get(k, '')))}</td>"
                for k in ["company", "role", "status"]
            )
            + f'<td><a href="{html.escape(row["url"], quote=True)}">Apply</a></td><td>{folder_link}</td></tr>'
        )
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Applications</title>"
        "<style>body{font:15px system-ui;background:#080d19;color:#e8f0ff;margin:3rem}"
        "table{border-collapse:collapse;width:100%;background:#131d30}td,th{padding:12px;text-align:left;border-bottom:1px solid #33405a}"
        "a{color:#69e8dc}th{background:#1c2642}</style></head><body><h1>Your application folders</h1>"
        "<p>Review each resume before applying. Needs review means the app could not complete a tailored resume.</p>"
        "<table><thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Application</th><th>Files</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )
    (root / "Applications.html").write_text(page, encoding="utf-8")


@one_job_at_a_time
def prepare_job(
    root,
    profile,
    job,
    keywords,
    client,
    sponsorship_filter=INCLUDE_ALL,
    use_browser=True,
    history=None,
    row=None,
):
    """Prepare exactly one requested role, with the shared JD/tailoring/audit path."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if row is None:
        row = {
            "company": job.company,
            "role": job.title,
            "url": job.job_url,
            "status": "Not prepared",
            "folder": "",
            "sponsorship": job.sponsorship_status,
        }
    row.pop("reason", None)
    row.pop("resume_pdf", None)
    name = application_folder_name(profile, job)
    folder = ensure_unique_folder(root, name)
    folder.mkdir()
    row["folder"] = str(folder)
    row["status"] = "In progress"
    (folder / "STATUS.txt").write_text(
        "In progress. A completed tailored resume is not available yet.",
        encoding="utf-8",
    )
    try:
        _write_apply_files(job, folder)
        try:
            enrich_job_description(job, use_browser=use_browser)
        finally:
            screen_sponsorship(job, history)
            row["sponsorship"] = job.sponsorship_status
            write_json(folder / "Job_Record.json", job.model_dump())
            verified = (
                job.description_source
                and job.description_source != "Unverified listing snippet"
            )
            text_name = (
                "Job_Description.txt" if verified else "Listing_Text_UNVERIFIED.txt"
            )
            (folder / text_name).write_text(job.description, encoding="utf-8")
        if not included_by_sponsorship_filter(job, sponsorship_filter):
            row["status"] = "Excluded by H1B filter"
            row["reason"] = job.sponsorship_status
        else:
            try:
                requirements = analyze_requirements(client, job, profile)
            except JobDescriptionError:
                if not use_browser or job.description_override:
                    raise
                enrich_job_description(job, use_browser=True, force_browser=True)
                requirements = analyze_requirements(client, job, profile)
            screen_sponsorship(job, history)
            row["sponsorship"] = job.sponsorship_status
            if not included_by_sponsorship_filter(job, sponsorship_filter):
                row["status"] = "Excluded by H1B filter"
                row["reason"] = job.sponsorship_status
            else:
                write_json(folder / "JD_Requirements.json", requirements.model_dump())
                write_json(folder / "Source_Profile.json", profile.model_dump())
                draft = tailor_resume(
                    client,
                    profile,
                    job,
                    keywords,
                    requirements=requirements,
                )
                original_draft = draft.model_copy(deep=True)
                draft, validation = audit_and_repair_draft(client, profile, draft)
                if not validation.passed:
                    raise ValueError(
                        "The resume could not pass factual validation: "
                        + "; ".join(validation.unsupported_claims)
                    )
                context_report = _context_headroom_report(client)
                if context_report and draft.tailoring_report:
                    draft.tailoring_report["context_headroom"] = context_report
                exported = create_application_package(
                    root,
                    profile,
                    job,
                    draft,
                    validation,
                    keywords,
                    requirements,
                    original_draft,
                    destination_folder=folder,
                )
                row["resume_pdf"] = exported.resume_pdf
                row["status"] = draft.tailoring_report.get(
                    "status", "Needs review: JD alignment not checked"
                )
                if draft.tailoring_report.get("issues"):
                    row["reason"] = "; ".join(draft.tailoring_report["issues"])
    except Exception as exc:  # noqa: BLE001 - isolate one failed role and continue the batch
        row["status"] = "Needs review"
        row["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Keep the final source and status in sync even after a browser retry.
        write_json(folder / "Job_Record.json", job.model_dump())
        context_report = _context_headroom_report(client)
        if context_report:
            try:
                write_json(folder / "Context_Headroom.json", context_report)
                if context_report.get("compression"):
                    write_json(folder / "AI_Usage.json", context_report["compression"])
                    row["ai_usage"] = context_report["compression"]
            except Exception as exc:  # noqa: BLE001 - optional diagnostic cannot invalidate output
                row["context_diagnostic"] = (
                    f"Context audit could not be saved: {type(exc).__name__}."
                )
        if (
            job.description_source
            and job.description_source != "Unverified listing snippet"
        ):
            (folder / "Job_Description.txt").write_text(
                job.description, encoding="utf-8"
            )
        (folder / "STATUS.txt").write_text(
            row["status"]
            + "\n"
            + row.get("reason", "")
            + "\nReview documents before applying.",
            encoding="utf-8",
        )
    return row


def run_automatic(
    filename: str,
    data: bytes,
    output_root: str,
    client,
    settings: SearchSettings,
    sponsorship_filter: str = INCLUDE_ALL,
    use_browser: bool = True,
    history: list[dict] | None = None,
    progress=None,
) -> dict:
    def update(message):
        if progress:
            progress(message)

    # Fail early on an unwritable destination or unavailable model.
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    ready, message = client.is_ready()
    if not ready:
        raise ValueError(message)
    root = ensure_unique_folder(
        root, datetime.now(timezone.utc).strftime("Job_Search_%Y%m%d_%H%M%S")
    )
    root.mkdir()
    report = {
        "folder": str(root),
        "keywords": [],
        "queries": [],
        "warnings": [],
        "roles": [],
        "status": "Reading resume",
    }
    save_index(root, report)
    try:
        update("Reading every page of your resume...")
        source = parse_resume_bytes(filename, data)
        profile = extract_candidate_profile(client, source)
        if not (profile.experiences or profile.skills or profile.education):
            raise ValueError(
                "No usable resume experience, skills, or education could be extracted."
            )
        (root / "Extracted_Resume.txt").write_text(source, encoding="utf-8")
        write_json(root / "Source_Profile.json", profile.model_dump())
        coverage = profile_coverage(source, profile)
        report["warnings"].extend(coverage.warnings)
        if coverage.missing_lines:
            report["warnings"].append(
                "Some source lines need review; see Resume_Coverage.json."
            )
        write_json(root / "Resume_Coverage.json", vars(coverage))
        update("Finding supported titles, skills, and search keywords...")
        suggestions = []
        try:
            expanded = suggest_keywords(client, profile)
            suggestions = expanded.keywords
            report["warnings"].extend(expanded.warnings)
        except Exception as exc:  # noqa: BLE001 - keyword expansion has a deterministic fallback
            report["warnings"].append(
                f"AI keyword expansion unavailable; using source resume titles and skills: {exc}"
            )
        keywords = automatic_keywords(profile, suggestions)
        if not keywords:
            raise ValueError(
                "No search keywords could be extracted. Check the saved source profile."
            )
        report["keywords"] = [k.keyword for k in keywords]
        report["queries"] = search_queries(keywords)
        report["status"] = "Searching"
        save_index(root, report)
        update(
            f"Searching with {len(report['keywords'])} keywords across {len(report['queries'])} queries..."
        )
        jobs, warnings = search_jobs(
            settings.model_copy(update={"keywords": report["queries"]}),
            progress=lambda done, total, msg: update(f"Search {done}/{total}: {msg}"),
        )
        report["warnings"].extend(warnings)
        jobs = sorted(
            deduplicate_jobs(jobs),
            key=lambda j: keyword_relevance(j, report["keywords"]),
            reverse=True,
        )
        report["roles"] = [
            {
                "company": j.company,
                "role": j.title,
                "url": j.job_url,
                "status": "Queued",
                "folder": "",
                "sponsorship": "Not checked",
            }
            for j in jobs
        ]
        # Every discovered application link is saved before slow tailoring begins.
        report["status"] = "Preparing folders"
        save_index(root, report)
        for index, (job, row) in enumerate(
            zip(jobs, report["roles"], strict=True), start=1
        ):
            update(f"Preparing {index}/{len(jobs)}: {job.company} | {job.title}")
            prepare_job(
                root,
                profile,
                job,
                report["keywords"],
                client,
                sponsorship_filter,
                use_browser,
                history,
                row,
            )
            save_index(root, report)
        report["status"] = "Complete"
        update(
            f"Finished. {len(jobs)} roles processed. Your application links and files are saved."
        )
    except Exception as exc:  # noqa: BLE001 - preserve progress and a readable report on failure
        report["status"] = "Stopped with error"
        report["warnings"].append(f"{type(exc).__name__}: {exc}")
        update(str(exc))
    finally:
        save_index(root, report)
    return report
