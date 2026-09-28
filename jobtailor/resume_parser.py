from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import fitz
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .models import CandidateProfile, ContactInfo, EducationItem, ExperienceItem


class ResumeParseError(ValueError):
    pass


def _include_links(text: str, links: list[str]) -> str:
    lines = text.splitlines()
    remaining = []
    for uri in dict.fromkeys(links):
        if not uri.startswith(("https://", "http://")) or uri in text:
            continue
        host = urlsplit(uri).hostname or ""
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            index = next(
                (i for i, line in enumerate(lines) if "linkedin" in line.casefold()),
                None,
            )
            if index is not None:
                lines[index] += " (" + uri + ")"
                continue
        remaining.append(uri)
    if remaining:
        lines.extend(["ADDITIONAL INFORMATION:", *remaining])
    return "\n".join(lines)


def _extract_docx(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    lines: list[str] = []

    # Include contact details stored in headers and preserve paragraph/table order.
    def read_container(container) -> None:
        for element in container._element:
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(element, container)
                text = paragraph.text.strip()
                if text:
                    numbered = (
                        paragraph._p.pPr is not None
                        and paragraph._p.pPr.numPr is not None
                    )
                    if numbered or paragraph.style.name.startswith("List Bullet"):
                        text = "• " + text
                    lines.append(text)
            elif tag == "tbl":
                table = Table(element, container)
                for row in table.rows:
                    seen = set()
                    for cell in row.cells:
                        if cell._tc not in seen:
                            seen.add(cell._tc)
                            read_container(cell)

    for section in document.sections:
        if not section.header.is_linked_to_previous:
            read_container(section.header)
    # Document's body, unlike cells and headers, is one level below its root.
    from docx.blkcntnr import BlockItemContainer

    read_container(BlockItemContainer(document.element.body, document))
    for section in document.sections:
        if not section.footer.is_linked_to_previous:
            read_container(section.footer)
    links = []
    for part in [
        document.part,
        *(s.header.part for s in document.sections),
        *(s.footer.part for s in document.sections),
    ]:
        links.extend(
            str(rel.target_ref)
            for rel in part.rels.values()
            if rel.is_external and rel.reltype.endswith("/hyperlink")
        )
    return _include_links("\n".join(lines), links)


def _extract_pdf(data: bytes) -> str:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        if document.needs_pass:
            raise ResumeParseError(
                "This PDF is password protected. Upload an unlocked copy."
            )
        pages = []
        links = []
        for number, page in enumerate(document, 1):
            text = page.get_text("text", sort=True)
            if len(text.strip()) < 40:
                raise ResumeParseError(
                    f"Page {number} has little or no readable text. This may be a scanned page. "
                    "Use an OCR/searchable PDF or DOCX; partial resumes are not accepted."
                )
            pages.append(text)
            links.extend(link.get("uri", "") for link in page.get_links())
        return _include_links("\n".join(pages), links)
    finally:
        document.close()


def parse_resume_bytes(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".docx":
        text = _extract_docx(data)
    elif suffix == ".pdf":
        text = _extract_pdf(data)
    elif suffix in {".txt", ".md"}:
        text = data.decode("utf-8", errors="replace")
    else:
        raise ResumeParseError("Use a DOCX, PDF, TXT, or Markdown resume.")

    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if len(text) < 100:
        raise ResumeParseError(
            "Very little text was detected. If this is a scanned PDF, convert it to a searchable PDF or DOCX first."
        )
    return text


SECTION_NAMES = {
    "summary": "summary",
    "professional summary": "summary",
    "profile": "summary",
    "experience": "experiences",
    "professional experience": "experiences",
    "work experience": "experiences",
    "employment history": "experiences",
    "education": "education",
    "academic background": "education",
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
    "core competencies": "skills",
    "certifications": "certifications",
    "certificates": "certifications",
    "projects": "projects",
    "selected projects": "projects",
    "additional information": "additional_information",
    "awards": "additional_information",
    "languages": "additional_information",
    "volunteer experience": "additional_information",
    "publications": "additional_information",
}
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = rf"(?:{MONTH}\.?\s+\d{{4}}|\d{{1,2}}[/.-]\d{{4}}|(?:19|20)\d{{2}})"
DATE_RANGE = re.compile(
    rf"(?P<start>{DATE})\s*(?:[-–—]|to)\s*(?P<end>{DATE}|Present|Current|Now)\b",
    re.IGNORECASE,
)
BULLET = re.compile(r"^\s*[•●▪◦\uf0b7*-]\s+")


def resume_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    name, lines = "contact", []
    for line in text.splitlines():
        key = line.strip().rstrip(":").casefold()
        if key in SECTION_NAMES:
            if lines:
                sections.append((name, "\n".join(lines)))
            name, lines = SECTION_NAMES[key], []
        elif line.strip():
            lines.append(line.strip())
    if lines:
        sections.append((name, "\n".join(lines)))
    return sections


def bullet_items(text: str) -> list[str]:
    """Join wrapped lines without asking a model to reproduce accomplishments."""
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if BULLET.match(line):
            items.append(BULLET.sub("", line))
        elif items:
            separator = "" if items[-1].endswith("-") else " "
            items[-1] += separator + line
        else:
            items.append(line)
    return items


def dated_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if DATE_RANGE.search(line) and not BULLET.match(line)
    ]
    if not starts or starts[0] != 0:
        return []  # Do not guess how to attach an orphan header to a job.
    return [
        "\n".join(lines[start:end])
        for start, end in zip(starts, starts[1:] + [len(lines)])
    ]


def structured_profile(text: str) -> CandidateProfile | None:
    """Lossless fast path for standard company/date, title, bullet resumes.

    Unsupported layouts return None for bounded AI extraction plus coverage review.
    This is format-based, never tied to a candidate or employer name.
    """
    profile = CandidateProfile()
    sections = resume_sections(text)
    if not any(name == "experiences" for name, _ in sections):
        return None
    for name, body in sections:
        lines = body.splitlines()
        if name == "contact":
            if len(lines) < 2 or "|" not in lines[1]:
                return None
            profile.contact = ContactInfo(name=lines[0])
            for value in " | ".join(lines[1:]).split("|"):
                value = value.strip()
                if re.search(r"[\w.+-]+@[\w.-]+\.\w+", value):
                    profile.contact.email = value
                elif "linkedin" in value.casefold():
                    profile.contact.linkedin = value
                elif re.fullmatch(r"[+()\d .-]{7,}", value):
                    profile.contact.phone = value
                elif value.startswith(("https://", "http://", "www.")):
                    profile.contact.website = value
                elif not profile.contact.location:
                    profile.contact.location = value
                else:
                    profile.additional_information.append(value)
        elif name == "summary":
            profile.professional_summary = " ".join(lines)
        elif name in {"experiences", "education"}:
            blocks = dated_blocks(body)
            if not blocks:
                return None
            for block in blocks:
                block_lines = block.splitlines()
                match = DATE_RANGE.search(block_lines[0])
                assert match is not None
                organization = block_lines[0][: match.start()].strip(" |\t")
                if (
                    not organization
                    or len(block_lines) < 2
                    or block_lines[0][match.end() :].strip()
                ):
                    return None
                if name == "experiences":
                    if len(block_lines) < 3 or not BULLET.match(block_lines[2]):
                        return None
                    company_parts = organization.split(", ")
                    # Split location only in the common company, region, country pattern.
                    company = (
                        company_parts[0] if len(company_parts) >= 3 else organization
                    )
                    location = (
                        ", ".join(company_parts[1:]) if len(company_parts) >= 3 else ""
                    )
                    profile.experiences.append(
                        ExperienceItem(
                            company=company,
                            location=location,
                            title=block_lines[1],
                            start_date=match.group("start"),
                            end_date=match.group("end"),
                            bullets=bullet_items("\n".join(block_lines[2:])),
                        )
                    )
                else:
                    degree, _, subject = block_lines[1].partition(",")
                    profile.education.append(
                        EducationItem(
                            institution=organization,
                            degree=degree.strip(),
                            field=subject.strip(" ,"),
                            start_date=match.group("start"),
                            graduation_date=match.group("end"),
                            details=bullet_items("\n".join(block_lines[2:])),
                        )
                    )
        elif name == "skills":
            for item in bullet_items(body):
                if re.search(r"\bcertified\b|\bcertification\b", item, re.IGNORECASE):
                    profile.certifications.append(item)
                else:
                    item = re.sub(
                        r"^(?:Proficient in|Application/Databases:|Tools:)\s*",
                        "",
                        item,
                        flags=re.IGNORECASE,
                    )
                    profile.skills.extend(
                        value.strip()
                        for value in re.split(r"[,;]", item)
                        if value.strip()
                    )
        else:
            getattr(profile, name).extend(bullet_items(body))
    return profile


def bounded_chunks(text: str, max_chars: int = 2400) -> list[str]:
    """Every character is passed along; a long section is never silently sliced off."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_chars and current:
            chunks.append(current)
            current = ""
        while len(line) > max_chars:
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        current += line
    if current:
        chunks.append(current)
    return chunks


def normalize_evidence(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


@dataclass
class CoverageReport:
    source_bullets: int
    profile_bullets: int
    missing_lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def profile_coverage(text: str, profile: CandidateProfile) -> CoverageReport:
    sections = resume_sections(text)
    experience_text = "\n".join(
        body for name, body in sections if name == "experiences"
    )
    count = sum(bool(BULLET.match(line)) for line in experience_text.splitlines())
    report = CoverageReport(
        count, sum(len(item.bullets) for item in profile.experiences)
    )
    evidence = normalize_evidence(profile.evidence_text())
    # A lexical diagnostic, not a semantic accuracy or ATS score. Header lines may
    # have reordered fields, so compare their components, not their whole order.
    for name, body in sections:
        for line in body.splitlines():
            clean = BULLET.sub("", line).strip()
            if DATE_RANGE.search(clean):
                match = DATE_RANGE.search(clean)
                parts = [
                    *clean[: match.start()].split(", "),
                    match.group("start"),
                    match.group("end"),
                ]
            elif name == "contact":
                parts = clean.split("|")
            elif name == "skills":
                clean = re.sub(
                    r"^(?:Proficient in|Application/Databases:|Tools:)\s*",
                    "",
                    clean,
                    flags=re.IGNORECASE,
                )
                parts = re.split(r"[,;]", clean)
            else:
                parts = [clean]
            if any(
                normalize_evidence(part) and normalize_evidence(part) not in evidence
                for part in parts
            ):
                report.missing_lines.append(clean)
    if report.missing_lines:
        report.warnings.append(
            f"{len(report.missing_lines)} source lines could not be matched verbatim. Review for omitted or changed facts."
        )
    if count and report.profile_bullets < count:
        report.warnings.append(
            f"Only {report.profile_bullets} of {count} detected experience bullets are in the profile."
        )
    if not profile.contact.name or not profile.experiences:
        report.warnings.append(
            "Name or employment history is empty. Correct the profile before continuing."
        )
    return report
