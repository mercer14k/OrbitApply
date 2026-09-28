"""Conservative role-level screening; employer history never overrides a JD."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .job_details import read_public
from .models import JobListing

H1B_DIRECTORY = "https://h1btrends.com/h1b/companies"
INCLUDE_H1B = "Include H1B only"
INCLUDE_ALL = "Include All (irrespective of H1B or not)"


def included_by_sponsorship_filter(job: JobListing, mode: str) -> bool:
    """One decision shared by the results table and package generation."""
    if mode == INCLUDE_ALL:
        return True
    if mode == INCLUDE_H1B:
        return sponsorship_allowed(
            job, h1b_only=False, allow_transfers=True, allow_conditional=True
        )
    raise ValueError("Choose Include H1B only or Include All.")


def company_key(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.casefold())
    while words and words[-1] in {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
    }:
        words.pop()
    return " ".join(words)


def classify_sponsorship(text: str) -> tuple[str, list[str]]:
    """Read employment sponsorship statements, without requiring the word H1B.

    Questions, historical policies and non-employment sponsorship are not offers.
    Eligibility and conditional offers remain visibly conditional in the table.
    """
    evidence, positive, negative, conditional, transfer = [], [], [], [], []
    for sentence in re.split(r"(?<=[.!?])\s+|\n\s*\n", text):
        sentence = " ".join(sentence.split())
        low = (
            unicodedata.normalize("NFKC", sentence)
            .casefold()
            .translate(
                str.maketrans({"‑": "-", "–": "-", "—": "-", "’": "'", "‘": "'"})
            )
        )
        if not re.search(r"sponsor|h[- ]?1b|work authori[sz]ation", low):
            continue
        evidence.append(sentence)
        if "?" in sentence or re.search(
            r"\b(?:will|do|would) you\b.*(?:require|need).*sponsor", low
        ):
            continue
        # Sponsoring a conference, project or charity says nothing about visas.
        if re.search(
            r"conferences?|events?|charit|sports?|scholarships?|fundrais|"
            r"executive sponsor|project sponsor|sponsor.*senior (?:leaders|management)",
            low,
        ) and not re.search(
            r"visa|h[- ]?1b|immigration|employment sponsor|work sponsor", low
        ):
            continue
        denial = re.search(
            r"sponsorship\s*[:\-]\s*no\b|"
            r"sponsorship\b[^.!?;]{0,50}\b(?:cannot|can't|won't|isn't|aren't)\s+(?:be\s+)?(?:considered|accepted|supported|provided|offered|available)|"
            r"\b(?:no|without)\s+(?:(?:any|a|need|for|requiring|future|additional|visa|work|employment|employer|company|immigration|h[- ]?1b)\s+){0,5}sponsor|"
            r"\b(?:not|never|cannot|can't|unable|doesn't|don't|won't|isn't|aren't|wasn't|weren't)\s+"
            r"(?:(?:be|able|to|have|the|ability|authority|resources|a|position|currently|presently|now|or|and|in|future|at|this|time|will)\s+){0,9}"
            r"(?:sponsor|(?:offer(?:ing)?|provid(?:e|ing)|support(?:ing)?|accept(?:ing)?|consider(?:ing)?|require|need)\b[^.!?;]{0,60}(?:sponsor|visa|h[- ]?1b))|"
            r"\b(?:ineligible|not eligible)\b[^.!?;]{0,50}sponsor|"
            r"sponsor(?:ship)?\b[^.!?;]{0,65}(?:\bnot\s+(?:(?:be|currently|presently|being)\s+){0,3}"
            r"(?:available|offered|provided|supported|possible|permitted|considered|accepted|eligible|an option)|unavailable|ineligible)|"
            r"\b(?:not|except|excluding|no)\s+(?:for\s+)?h[- ]?1b\b",
            low,
        )
        if denial:
            negative.append(sentence)
            continue
        if re.search(
            r"\b(?:previously|historically|formerly)\b|in the past|other (?:roles|positions)|"
            r"(?:used to|no longer)\s+(?:offer|provide|sponsor)|"
            r"sponsorship\s+(?:was|had been)\s+(?:available|offered|provided)",
            low,
        ):
            continue
        # An explicit offer restricted to a different visa class is not H-1B evidence.
        if (
            re.search(r"\b(?:tn|l-?1|o-?1|j-?1|f-?1)\b", low)
            and re.search(r"\bonly\b", low)
            and not re.search(r"h[- ]?1b", low)
        ):
            continue
        uncertain = re.search(
            r"\b(?:may|might|consider|considered|depending|eligible|eligibility|potentially|case.by.case)\b|subject to|only (?:for|if|when|to)",
            low,
        )
        offer = re.search(
            r"\b(?:offers?|provides?|supports?|accepts?|welcomes?|consider(?:s|ed)?|eligible|eligibility)\b"
            r"[^.!?;]{0,80}(?:sponsorship|visa|h[- ]?1b)|"
            r"\bsponsor(?:ship)?\b[^.!?;]{0,65}\b(?:available|provided|offered|supported|possible|eligible|considered)\b|"
            r"\bsponsor(?:s|ing)?\s+(?:(?:qualified|eligible|international|foreign)\s+){0,3}(?:candidates|applicants|employees|workers|visa|h[- ]?1b)|"
            r"\b(?:visa|h[- ]?1b)?\s*sponsorship\s*:\s*yes\b",
            low,
        )
        transfer_offer = re.search(r"h[- ]?1b\b[^.!?;]{0,45}transfer", low) and (
            offer
            or re.search(r"\b(?:only|accepted|supported|available|welcome)\b", low)
        )
        if transfer_offer:
            (conditional if uncertain else transfer).append(sentence)
        elif offer:
            (conditional if uncertain else positive).append(sentence)
    if negative and (positive or transfer or conditional):
        return "Conflicting statements; review", evidence
    if negative:
        return "Not offered", negative
    if conditional:
        return "Conditional; confirm with employer", evidence
    if transfer:
        return "H-1B transfers stated", transfer
    if positive:
        if any(re.search(r"h[- ‑–]?1b", p, re.IGNORECASE) for p in positive):
            return "H-1B sponsorship stated", positive
        return "Visa sponsorship stated; H-1B unspecified", positive
    return (
        "Mentioned; offer not established" if evidence else "Not mentioned"
    ), evidence


def screen_sponsorship(
    job: JobListing, history: list[dict] | None = None
) -> JobListing:
    job.sponsor_history = [
        entry
        for entry in (history or [])
        if company_key(entry["company"]) == company_key(job.company)
    ]
    job.sponsorship_checked_at = datetime.now(timezone.utc).isoformat()
    job.sponsorship_source = job.description_source
    if (
        not job.description_checked_at
        or job.description_source == "Unverified listing snippet"
    ):
        job.sponsorship_status, job.sponsorship_evidence = "JD unavailable", []
    else:
        job.sponsorship_status, job.sponsorship_evidence = classify_sponsorship(
            job.description
        )
    return job


def sponsorship_allowed(
    job: JobListing,
    *,
    h1b_only: bool = True,
    allow_transfers: bool = False,
    allow_conditional: bool = False,
) -> bool:
    allowed = {"H-1B sponsorship stated"}
    if not h1b_only:
        allowed.add("Visa sponsorship stated; H-1B unspecified")
    if allow_transfers:
        allowed.add("H-1B transfers stated")
    if allow_conditional:
        allowed.add("Conditional; confirm with employer")
    return job.sponsorship_status in allowed


def import_sponsor_history(data: bytes) -> list[dict]:
    rows = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if not {"company", "source_url"} <= set(rows.fieldnames or []):
        raise ValueError(
            "Sponsor CSV needs company and source_url columns; year and evidence are optional."
        )
    result = []
    for row in rows:
        company, source = (
            (row.get("company") or "").strip(),
            (row.get("source_url") or "").strip(),
        )
        if (
            not company
            or urlsplit(source).scheme not in {"http", "https"}
            or not urlsplit(source).hostname
        ):
            raise ValueError(
                "Every sponsor-history row needs a company name and an HTTP(S) source URL."
            )
        result.append(
            {
                "company": company,
                "source_url": source,
                "year": row.get("year", ""),
                "evidence": row.get("evidence", ""),
                "status": "User-imported history; not independently verified",
            }
        )
    return result


def load_h1b_directory() -> list[dict]:
    try:
        html, final_url = read_public(H1B_DIRECTORY)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"H1BTrends lookup unavailable ({type(exc).__name__}). Company history remains unknown. Open the source normally or import a sponsor CSV."
        ) from exc
    soup = BeautifulSoup(html, "html.parser")
    result, seen = [], set()
    for link in soup.select("a[href]"):
        href = urljoin(final_url, link.get("href", ""))
        path = urlsplit(href).path.rstrip("/")
        company = link.get_text(" ", strip=True)
        if urlsplit(href).hostname not in {
            "h1btrends.com",
            "www.h1btrends.com",
        } or not re.match(r"^/h1b/compan(?:y|ies)/[^/]+$", path):
            continue
        if not company or company_key(company) in seen:
            continue
        seen.add(company_key(company))
        row = link.find_parent("tr")
        result.append(
            {
                "company": company,
                "source_url": href,
                "year": "",
                "evidence": row.get_text(" ", strip=True) if row else company,
                "status": "Listed in H1BTrends directory; verify employer history",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    if not result:
        raise ValueError(
            "No identifiable company records were found on the directory page. Its layout may have changed or require access. Import a sponsor CSV; no negative conclusion was recorded."
        )
    return result
