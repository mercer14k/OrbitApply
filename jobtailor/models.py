from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ContactInfo(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""


class ExperienceItem(BaseModel):
    company: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    location: str = ""
    start_date: str = ""
    graduation_date: str = ""
    details: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    contact: ContactInfo = Field(default_factory=ContactInfo)
    headline: str = ""
    professional_summary: str = ""
    experiences: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    additional_information: list[str] = Field(default_factory=list)

    def evidence_text(self) -> str:
        parts: list[str] = [
            self.contact.model_dump_json(),
            self.headline,
            self.professional_summary,
            "\n".join(self.skills),
            "\n".join(self.certifications),
            "\n".join(self.projects),
            "\n".join(self.additional_information),
        ]
        for item in self.experiences:
            parts.extend(
                [
                    item.company,
                    item.title,
                    item.location,
                    item.start_date,
                    item.end_date,
                    "\n".join(item.bullets),
                ]
            )
        for item in self.education:
            parts.extend(
                [
                    item.institution,
                    item.degree,
                    item.field,
                    item.location,
                    item.start_date,
                    item.graduation_date,
                    "\n".join(item.details),
                ]
            )
        return "\n".join(part for part in parts if part)


KeywordCategory = Literal["Job title", "Functional skill", "Technology", "Industry"]


class KeywordSuggestion(BaseModel):
    keyword: str
    category: KeywordCategory
    reason: str = ""


class KeywordSet(BaseModel):
    keywords: list[KeywordSuggestion]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def validate_keyword_count(
        cls, value: list[KeywordSuggestion]
    ) -> list[KeywordSuggestion]:
        if not value:
            raise ValueError("Return at least one supported search keyword.")
        normalized: set[str] = set()
        result = []
        for item in value:
            key = item.keyword.strip().casefold()
            if not key or key in normalized:
                continue
            normalized.add(key)
            result.append(item.model_copy(update={"keyword": item.keyword.strip()}))
        if not result:
            raise ValueError("Return at least one supported search keyword.")
        return result


class JobListing(BaseModel):
    listing_id: str = ""
    title: str
    company: str
    location: str = ""
    country: str = ""
    date_posted: str = ""
    description: str = ""
    source_urls: list[str] = Field(default_factory=list)
    job_url: str
    source: str = ""
    is_remote: bool | None = None
    search_keyword: str = ""
    description_override: str = ""
    description_source: str = ""
    description_checked_at: str = ""
    description_status: str = "Not checked"
    retrieval_notes: list[str] = Field(default_factory=list)
    sponsorship_status: str = "Not checked"
    sponsorship_evidence: list[str] = Field(default_factory=list)
    sponsorship_checked_at: str = ""
    sponsorship_source: str = ""
    sponsor_history: list[dict] = Field(default_factory=list)


class TailoredBullet(BaseModel):
    text: str
    source_bullet_indices: list[int] = Field(default_factory=list)


class ExperienceEdit(BaseModel):
    experience_index: int
    bullets: list[TailoredBullet] = Field(default_factory=list)


class TailoredResume(BaseModel):
    target_title: str = ""
    professional_summary: str = ""
    highlighted_skills: list[str] = Field(default_factory=list)
    experience_edits: list[ExperienceEdit] = Field(default_factory=list)
    ats_keywords_used: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    match_score: int = Field(default=0, ge=0, le=100)
    match_rationale: str = ""
    bullet_order: dict[int, list[int]] = Field(default_factory=dict)
    tailoring_report: dict = Field(default_factory=dict)


class DraftValidation(BaseModel):
    passed: bool = True
    unsupported_claims: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SearchSettings(BaseModel):
    keywords: list[str]
    location: str = ""
    country: str = "USA"
    sites: list[str] = Field(default_factory=lambda: ["indeed", "google"])
    days_old: int = Field(default=14, ge=1, le=60)
    results_per_keyword: int = Field(default=20, ge=5, le=100)
    remote_only: bool = False
    include_unknown_dates: bool = False


class ExportResult(BaseModel):
    folder: str
    resume_docx: str
    resume_pdf: str
    job_description_docx: str
    job_description_pdf: str
    apply_link: str
    tailoring_status: str = ""
    created_on: date = Field(default_factory=date.today)
