"""Single-column, text-based resume output with an extraction round-trip gate."""

from __future__ import annotations

import html
import re
import unicodedata
from pathlib import Path

import fitz
import reportlab
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

from .models import CandidateProfile, TailoredResume


def experience_bullets(
    profile: CandidateProfile,
    draft: TailoredResume,
    index: int,
    *,
    ordered: bool = True,
) -> list[str]:
    source = profile.experiences[index].bullets
    replacements = {}
    for edit in draft.experience_edits:
        if edit.experience_index != index:
            continue
        for bullet in edit.bullets:
            # Many-to-one rewrites can erase accomplishments. Preserve originals
            # unless there is one explicitly cited replacement for a source item.
            refs = bullet.source_bullet_indices
            if len(refs) == 1 and 0 <= refs[0] < len(source) and bullet.text.strip():
                replacements.setdefault(refs[0], bullet.text)
    values = [replacements.get(i, value) for i, value in enumerate(source)]
    order = draft.bullet_order.get(index, [])
    if (
        ordered
        and len(order) == len(source)
        and sorted(order) == list(range(len(source)))
    ):
        return [values[i] for i in order]
    return values


def resume_blocks(
    profile: CandidateProfile, draft: TailoredResume
) -> list[tuple[str, str]]:
    blocks = [("name", profile.contact.name or "Candidate")]
    for values in (
        [profile.contact.location, profile.contact.phone, profile.contact.email],
        [profile.contact.linkedin, profile.contact.website],
    ):
        if any(values):
            blocks.append(("contact", " | ".join(filter(None, values))))
    if draft.target_title:
        blocks.append(("contact", "Target role: " + draft.target_title))
    summary = draft.professional_summary or profile.professional_summary
    if summary:
        blocks.extend([("heading", "PROFESSIONAL SUMMARY"), ("body", summary)])
    # Highlighting orders supported skills first; it does not delete other skills.
    skills = list(dict.fromkeys([*draft.highlighted_skills, *profile.skills]))
    if skills:
        blocks.extend([("heading", "SKILLS"), ("body", "; ".join(skills))])
    if profile.experiences:
        blocks.append(("heading", "PROFESSIONAL EXPERIENCE"))
        for index, role in enumerate(profile.experiences):
            blocks.append(
                ("job", " | ".join(filter(None, [role.company, role.location])))
            )
            blocks.append(
                (
                    "role",
                    " | ".join(
                        filter(
                            None,
                            [
                                role.title,
                                " - ".join(
                                    filter(None, [role.start_date, role.end_date])
                                ),
                            ],
                        )
                    ),
                )
            )
            blocks.extend(
                ("bullet", text) for text in experience_bullets(profile, draft, index)
            )
    if profile.education:
        blocks.append(("heading", "EDUCATION"))
        for item in profile.education:
            blocks.append(
                ("job", " | ".join(filter(None, [item.institution, item.location])))
            )
            degree = ", ".join(filter(None, [item.degree, item.field]))
            dates = " - ".join(filter(None, [item.start_date, item.graduation_date]))
            blocks.append(("body", " | ".join(filter(None, [degree, dates]))))
            blocks.extend(("bullet", text) for text in item.details)
    for title, values in [
        ("CERTIFICATIONS", profile.certifications),
        ("PROJECTS", profile.projects),
        ("ADDITIONAL INFORMATION", profile.additional_information),
    ]:
        if values:
            blocks.append(("heading", title))
            blocks.extend(("bullet", text) for text in values)
    return [(kind, printable(text)) for kind, text in blocks if text.strip()]


def printable(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    for char in "–—‑−":
        value = value.replace(char, "-")
    return value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def _fonts() -> None:
    # Bitstream Vera ships with ReportLab, so Windows needs no extra font install.
    folder = Path(reportlab.__file__).parent / "fonts"
    for name, file in [("ATSRegular", "Vera.ttf"), ("ATSBold", "VeraBd.ttf")]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(folder / file)))


def render_ats_pdf(
    profile: CandidateProfile, draft: TailoredResume, path: Path
) -> dict:
    _fonts()
    styles = {
        "name": ParagraphStyle(
            "ATSName",
            fontName="ATSBold",
            fontSize=17,
            leading=20,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "contact": ParagraphStyle(
            "ATSContact",
            fontName="ATSRegular",
            fontSize=9,
            leading=11.5,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "heading": ParagraphStyle(
            "ATSHeading",
            fontName="ATSBold",
            fontSize=10.5,
            leading=13,
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ATSBody", fontName="ATSRegular", fontSize=10, leading=12.2, spaceAfter=4
        ),
        "job": ParagraphStyle(
            "ATSJob",
            fontName="ATSBold",
            fontSize=10,
            leading=12.2,
            spaceBefore=6,
            spaceAfter=2,
            keepWithNext=True,
        ),
        "role": ParagraphStyle(
            "ATSRole",
            fontName="ATSRegular",
            fontSize=10,
            leading=12.2,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "bullet": ParagraphStyle(
            "ATSBullet",
            fontName="ATSRegular",
            fontSize=10,
            leading=12.2,
            leftIndent=10,
            firstLineIndent=-8,
            spaceAfter=3,
        ),
    }
    blocks = resume_blocks(profile, draft)
    for kind, text in blocks:
        font = pdfmetrics.getFont(styles[kind].fontName)
        unsupported = sorted(
            {c for c in text if not c.isspace() and ord(c) not in font.face.charToGlyph}
        )
        if unsupported:
            raise ValueError(
                f"PDF font cannot render these characters: {''.join(unsupported)}. Use DOCX or replace unsupported characters; no broken PDF was accepted."
            )
    story = [
        Paragraph(html.escape(("- " if kind == "bullet" else "") + text), styles[kind])
        for kind, text in blocks
    ]
    document = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=43.2,
        rightMargin=43.2,
        topMargin=36,
        bottomMargin=36,
        title=f"{profile.contact.name} Resume",
        author=profile.contact.name,
    )
    document.build(story)
    return verify_pdf(path, blocks)


def verify_pdf(path: Path, blocks: list[tuple[str, str]]) -> dict:
    def normalized(value: str) -> str:
        return re.sub(r"\s+", "", printable(value))

    with fitz.open(path) as doc:
        extracted = "\n".join(page.get_text("text") for page in doc)
        full_text = normalized(extracted)
        offset = 0
        for _, block in blocks:
            expected = normalized(block)
            position = full_text.find(expected, offset)
            if position < 0:
                raise ValueError(
                    "PDF text verification failed: a resume block is missing or out of order. No successful package was reported."
                )
            offset = position + len(expected)
        if any(page.get_images() for page in doc):
            raise ValueError("Unexpected image found in the ATS resume PDF.")
        return {
            "passed": True,
            "page_count": len(doc),
            "text_blocks_verified": len(blocks),
            "single_column": True,
            "embedded_fonts": True,
            "selectable_text": True,
            "notice": "Text/layout checks only. Not an employer ATS score or a guarantee of parsing or selection.",
        }
