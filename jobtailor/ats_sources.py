"""Documented public Greenhouse and Lever job-description endpoints."""

from __future__ import annotations

import html
import json
from urllib.parse import parse_qs, quote, urlsplit


def ats_endpoint(url: str) -> tuple[str, str, str] | None:
    parts = urlsplit(url)
    path = [p for p in parts.path.split("/") if p]
    host = parts.hostname or ""
    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    }:
        token = path[0] if path else ""
        job_id = (
            path[path.index("jobs") + 1]
            if "jobs" in path and len(path) > path.index("jobs") + 1
            else parse_qs(parts.query).get("gh_jid", [""])[0]
        )
        if token and job_id.isdigit():
            api = (
                "boards-api.eu.greenhouse.io"
                if ".eu." in host
                else "boards-api.greenhouse.io"
            )
            return (
                "greenhouse",
                f"https://{api}/v1/boards/{quote(token, safe='')}/jobs/{job_id}",
                job_id,
            )
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and len(path) >= 2:
        api = "api.eu.lever.co" if ".eu." in host else "api.lever.co"
        return (
            "lever",
            f"https://{api}/v0/postings/{quote(path[0], safe='')}/{quote(path[1], safe='')}?mode=json",
            path[1],
        )
    return None


def fetch_ats(url: str, expected_title: str, reader) -> tuple[str, str] | None:
    from .job_details import JobDescriptionError, clean_html, title_matches

    endpoint = ats_endpoint(url)
    if not endpoint:
        return None
    kind, api, job_id = endpoint
    body, _ = reader(api)
    data = json.loads(body)
    if str(data.get("id", "")) != job_id or not title_matches(
        expected_title, data.get("title", data.get("text", ""))
    ):
        raise JobDescriptionError(
            "The ATS response does not match this job ID and title."
        )
    if kind == "greenhouse":
        text = clean_html(html.unescape(html.unescape(data.get("content", ""))))
    else:
        sections = [data.get("descriptionPlain") or data.get("description", "")]
        for item in data.get("lists", []):
            sections.extend([item.get("text", ""), item.get("content", "")])
        sections.extend(
            [
                data.get("additionalPlain") or data.get("additional", ""),
                data.get("closingPlain") or data.get("closing", ""),
            ]
        )
        text = clean_html("\n\n".join(str(value) for value in sections if value))
    return text, api
