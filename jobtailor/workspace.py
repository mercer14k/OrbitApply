"""Resume suggestions, discovery, and explicitly requested application folders."""

from datetime import datetime, timezone
from pathlib import Path

from .ai_tasks import extract_candidate_profile, suggest_keywords
from .application_history import posting_keys
from .automatic import automatic_keywords, prepare_job, save_index, search_queries
from .evidence_tailoring import COMPLETED
from .jobs import (
    deduplicate_jobs,
    enrich_job_description,
    keyword_relevance,
    search_jobs,
)
from .models import CandidateProfile, JobListing, KeywordSuggestion
from .resume_parser import parse_resume_bytes, profile_coverage
from .sponsorship import INCLUDE_ALL, included_by_sponsorship_filter, screen_sponsorship
from .utils import ensure_unique_folder, stable_listing_id, write_json


def require_model(client):
    ready, message = client.is_ready()
    if not ready:
        raise ValueError(message)


def prepare_resume(filename, data, client):
    require_model(client)
    source = parse_resume_bytes(filename, data)
    profile = extract_candidate_profile(client, source)
    if not (profile.experiences or profile.skills or profile.education):
        raise ValueError(
            "No usable experience, skills, or education could be extracted."
        )
    coverage = profile_coverage(source, profile)
    warnings = list(coverage.warnings)
    suggestions = []
    try:
        expanded = suggest_keywords(client, profile)
        suggestions = expanded.keywords
        warnings.extend(expanded.warnings)
    except Exception as exc:  # noqa: BLE001 - retain source titles when model expansion fails
        warnings.append(f"AI role expansion unavailable; using resume titles: {exc}")
    keywords = automatic_keywords(profile, suggestions)
    result = {
        "profile": profile.model_dump(),
        "source": source,
        "keywords": [k.model_dump() for k in keywords],
        "role_titles": [k.keyword for k in keywords if k.category == "Job title"],
        "warnings": warnings,
        "coverage": vars(coverage),
    }
    reporter = getattr(client, "context_headroom_report", None)
    if callable(reporter):
        try:
            report = reporter()
            if isinstance(report, dict):
                result["context_headroom"] = report
        except Exception as exc:  # noqa: BLE001 - optional diagnostics must fail open
            warnings.append(
                f"Context diagnostics unavailable; resume analysis continued: {type(exc).__name__}."
            )
    return result


def discover(
    prepared,
    selected_roles,
    settings,
    output_root,
    sponsorship=INCLUDE_ALL,
    use_browser=True,
    history=None,
    progress=None,
    cancel_event=None,
    partial=None,
    excluded_posting_keys=None,
):
    titles = list(dict.fromkeys(r.strip() for r in selected_roles if r.strip()))
    if not titles:
        raise ValueError("Select at least one role before starting the search.")
    skills = [
        KeywordSuggestion.model_validate(k)
        for k in prepared["keywords"]
        if k["category"] != "Job title"
    ]
    selected = [
        KeywordSuggestion(keyword=t, category="Job title") for t in titles
    ] + skills
    queries = search_queries(selected)
    workspace = {
        "folder": "",
        "output_root": output_root,
        "roles": [],
        "jobs": [],
        "prepared": prepared,
        "selected_roles": titles,
        "queries": queries,
        "keywords": [k.keyword for k in selected],
        "warnings": [],
        "sponsorship_filter": sponsorship,
        "use_browser": use_browser,
        "history": history or [],
        "status": "Searching",
        "applied_hidden": 0,
    }

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    def publish():
        if partial:
            partial(workspace)

    def role_row(job):
        job.listing_id = job.listing_id or stable_listing_id(
            job.job_url, job.company, job.title, job.location
        )
        return {
            "listing_id": job.listing_id,
            "company": job.company,
            "role": job.title,
            "url": job.job_url,
            "source_urls": list(job.source_urls),
            "status": "Not prepared",
            "folder": "",
            "sponsorship": job.sponsorship_status,
            "sponsorship_evidence": list(job.sponsorship_evidence),
            "sponsorship_source": job.sponsorship_source,
            "included": included_by_sponsorship_filter(job, sponsorship),
        }

    def found_so_far(jobs, warnings):
        if cancelled():
            return
        ordered = sorted(
            deduplicate_jobs(jobs),
            key=lambda j: keyword_relevance(j, titles),
            reverse=True,
        )
        hidden = set(excluded_posting_keys or ())
        remaining = [
            job for job in ordered if not posting_keys(job.model_dump()) & hidden
        ]
        workspace["applied_hidden"] = len(ordered) - len(remaining)
        ordered = remaining
        workspace["roles"] = [role_row(job) for job in ordered]
        workspace["jobs"] = [job.model_dump() for job in ordered]
        workspace["warnings"] = list(warnings)
        publish()

    publish()
    jobs, warnings = search_jobs(
        settings.model_copy(update={"keywords": queries}),
        progress=progress,
        cancel_event=cancel_event,
        partial=found_so_far,
    )
    found_so_far(jobs, warnings)
    if not cancelled() and sponsorship != INCLUDE_ALL:
        workspace["status"] = "Checking sponsorship"
        for index, raw_job in enumerate(workspace["jobs"]):
            if cancelled():
                break
            job = JobListing.model_validate(raw_job)
            if progress:
                progress(
                    index,
                    len(workspace["jobs"]),
                    f"Checking sponsorship wording: {job.company}",
                )
            if cancelled():
                break
            try:
                enrich_job_description(job, use_browser=use_browser)
                if cancelled():
                    break
                screen_sponsorship(job, history)
            except Exception as exc:  # noqa: BLE001 - retrieval failure stays explicitly unknown
                if cancelled():
                    break
                job.sponsorship_status = "JD unavailable"
                workspace["warnings"].append(f"{job.company}: {exc}")
            workspace["jobs"][index] = job.model_dump()
            workspace["roles"][index] = role_row(job)
            publish()
    workspace["status"] = "Search cancelled" if cancelled() else "Search complete"
    if cancelled():
        workspace["warnings"].append(
            "Search cancelled. Partial results are retained; unchecked sponsorship is not treated as an offer."
        )
    publish()
    # Search is read-only: no directory or per-role files are created here.
    return workspace


def create_role_folder(workspace, listing_id, client):
    row = next(r for r in workspace["roles"] if r["listing_id"] == listing_id)
    if not row["included"]:
        raise ValueError("This role is excluded by the search's sponsorship filter.")
    if row["status"] in COMPLETED and row["folder"] and Path(row["folder"]).is_dir():
        return row  # A repeated click cannot produce a second completed package.
    require_model(client)
    if not workspace["folder"]:
        base = Path(workspace["output_root"]).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        root = ensure_unique_folder(
            base, datetime.now(timezone.utc).strftime("Job_Search_%Y%m%d_%H%M%S")
        )
        root.mkdir()
        workspace["folder"] = str(root)
        write_json(root / "Source_Profile.json", workspace["prepared"]["profile"])
        write_json(root / "Resume_Coverage.json", workspace["prepared"]["coverage"])
        (root / "Extracted_Resume.txt").write_text(
            workspace["prepared"]["source"], encoding="utf-8"
        )
    root = Path(workspace["folder"])
    save_index(root, workspace)
    job = next(
        JobListing.model_validate(j)
        for j in workspace["jobs"]
        if j["listing_id"] == listing_id
    )
    try:
        prepare_job(
            root,
            CandidateProfile.model_validate(workspace["prepared"]["profile"]),
            job,
            workspace["keywords"],
            client,
            workspace["sponsorship_filter"],
            workspace["use_browser"],
            workspace["history"],
            row,
        )
        row["sponsorship"] = job.sponsorship_status
        row["sponsorship_evidence"] = list(job.sponsorship_evidence)
        row["sponsorship_source"] = job.sponsorship_source
        return row
    finally:
        save_index(root, workspace)
