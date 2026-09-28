from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from .models import (
    CandidateProfile,
    DraftValidation,
    ExperienceEdit,
    JobListing,
    KeywordSet,
    KeywordSuggestion,
    TailoredResume,
)
from .ollama_client import OllamaClient, OllamaError, OllamaOutputLimitError
from .requirements_analysis import (
    RequirementAnalysis,
    analyze_requirements,
)
from .resume_parser import bounded_chunks, resume_sections, structured_profile
from .utils import extract_numbers
from .content_compression import compact_json

PROFILE_SYSTEM = """You extract resumes into structured data. The resume is the only source of truth.
Never infer or invent an employer, title, date, degree, certification, skill, metric, or contact detail.
Preserve the meaning and wording of accomplishments. Use empty strings or empty arrays when information is absent.
Return data that exactly follows the supplied JSON schema."""


KEYWORD_SYSTEM = """You suggest job search titles supported by a resume fragment.
Return only concise, distinct, plausible equivalent job titles at the supported seniority and in the same field.
Do not invent qualifications. Return no explanations, skills, reasons, or commentary.
Use an empty titles array if this fragment supports no job titles. Follow the JSON schema exactly."""


class RoleTitleBatch(BaseModel):
    titles: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        max_length=6
    )


class SmallRoleTitleBatch(BaseModel):
    titles: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        max_length=3
    )


TAILOR_SYSTEM = """You tailor resumes for applicant tracking systems while preserving complete factual accuracy.
The candidate profile is the sole source of truth. The job description supplies emphasis, never candidate facts.
Do not invent skills, duties, employers, titles, education, certifications, dates, seniority, or numbers.
Every rewritten experience bullet must cite one or more zero-based source bullet indices from the same experience.
Use only defensible paraphrases of those cited bullets. Keep language natural, concise, and specific.
Return structured data matching the JSON schema exactly."""


VALIDATE_SYSTEM = """You are a strict factual resume auditor.
Compare the tailored resume with the source candidate profile. Job-description requirements are not evidence.
Flag every claim that is not directly supported by the candidate profile, including inflated scope, new tools,
new numbers, causal claims, or combined achievements that change meaning. Audit only candidate claims in
summary, highlighted skills and experience bullets. Do not audit missing_requirements, target_title, scores,
or blank fields as candidate facts. Follow the JSON schema exactly."""


REPAIR_SYSTEM = """You repair a tailored resume after a factual audit.
Remove or rewrite every unsupported claim identified by the auditor. Use only the source candidate profile.
Keep valid tailoring when it remains directly supported. Preserve all schema fields and return exact JSON."""


def extract_candidate_profile(
    client: OllamaClient, resume_text: str
) -> CandidateProfile:
    direct = structured_profile(resume_text)
    if direct is not None:
        return direct
    # Unknown layouts use small, exhaustive chunks, not one truncated response.
    profile = CandidateProfile()
    for section, body in resume_sections(resume_text):
        for chunk in bounded_chunks(body, 2200):
            part = client.chat_json(
                CandidateProfile,
                PROFILE_SYSTEM,
                f"Extract ONLY this source fragment from section {section}. "
                "Preserve all facts and bullets verbatim. Do not summarize. "
                "For a continuation with no header leave employer/title/dates empty.\n\n"
                + chunk,
                temperature=0.0,
            )
            for field in type(profile.contact).model_fields:
                value = getattr(part.contact, field)
                if value and not getattr(profile.contact, field):
                    setattr(profile.contact, field, value)
            for field in ("headline", "professional_summary"):
                value = getattr(part, field)
                if value:
                    setattr(
                        profile,
                        field,
                        " ".join(filter(None, [getattr(profile, field), value])),
                    )
            for experience in part.experiences:
                if (
                    not experience.company
                    and not experience.title
                    and profile.experiences
                    or profile.experiences
                    and all(
                        getattr(profile.experiences[-1], key)
                        == getattr(experience, key)
                        for key in ("company", "title", "start_date", "end_date")
                    )
                ):
                    profile.experiences[-1].bullets.extend(experience.bullets)
                else:
                    profile.experiences.append(experience)
            profile.education.extend(part.education)
            for field in (
                "skills",
                "certifications",
                "projects",
                "additional_information",
            ):
                setattr(
                    profile,
                    field,
                    list(dict.fromkeys(getattr(profile, field) + getattr(part, field))),
                )
    return profile


def _overview(profile: CandidateProfile) -> CandidateProfile:
    """Role headers plus skills, without the large bullet arrays."""
    compact = profile.model_copy(deep=True)
    for experience in compact.experiences:
        experience.bullets = []
    return compact


def suggest_keywords(client: OllamaClient, profile: CandidateProfile) -> KeywordSet:
    # Preserve source keywords without asking the model to repeat a large list.
    keywords = [
        KeywordSuggestion(keyword=e.title, category="Job title")
        for e in profile.experiences
        if e.title.strip()
    ]
    keywords.extend(
        KeywordSuggestion(keyword=s, category="Technology")
        for s in profile.skills
        if s.strip()
    )
    evidence = compact_json(
        {
            "titles": [e.title for e in profile.experiences],
            "headline": profile.headline,
            "summary": profile.professional_summary,
            "skills": profile.skills,
        },
    )
    warnings = []
    # The per-call bound is not a limit on the merged source keywords or titles.
    for index, fragment in enumerate(bounded_chunks(evidence, 1800), 1):
        prompt = (
            "Suggest up to 6 supported job titles from this resume fragment:\n"
            + fragment
        )
        try:
            try:
                batch = client.chat_json(
                    RoleTitleBatch,
                    KEYWORD_SYSTEM,
                    prompt,
                    temperature=0.1,
                    max_output_tokens=768,
                )
            except OllamaOutputLimitError:
                batch = client.chat_json(
                    SmallRoleTitleBatch,
                    KEYWORD_SYSTEM,
                    "Suggest at most 3 short job titles. No explanations.\n" + fragment,
                    temperature=0.0,
                    max_output_tokens=768,
                )
            keywords.extend(
                KeywordSuggestion(keyword=t, category="Job title")
                for t in batch.titles
                if t.strip()
            )
        except OllamaError as exc:
            warnings.append(
                f"Role suggestion batch {index} could not finish. "
                f"Source titles, skills, and other completed suggestions were kept. {exc}"
            )
    return KeywordSet(keywords=keywords, warnings=warnings)


def tailor_resume(
    client: OllamaClient,
    profile: CandidateProfile,
    job: JobListing,
    selected_keywords: list[str],
    requirements: RequirementAnalysis | None = None,
) -> TailoredResume:
    from .evidence_tailoring import build_tailored_resume

    requirements = requirements or analyze_requirements(client, job, profile)
    return build_tailored_resume(client, profile, job, requirements)


def validate_draft(
    client: OllamaClient,
    profile: CandidateProfile,
    tailored: TailoredResume,
) -> DraftValidation:
    prompt = (
        "SOURCE PROFILE:\n"
        + profile.model_dump_json()
        + "\n\nTAILORED DRAFT:\n"
        + tailored.model_dump_json()
    )
    return client.chat_json(DraftValidation, VALIDATE_SYSTEM, prompt, temperature=0.0)


def _audit_and_repair_piece(
    client: OllamaClient,
    profile: CandidateProfile,
    tailored: TailoredResume,
) -> tuple[TailoredResume, DraftValidation]:
    validation = validate_draft(client, profile, tailored)
    if validation.passed and not validation.unsupported_claims:
        return tailored, validation

    repair_prompt = (
        "SOURCE PROFILE:\n"
        + profile.model_dump_json()
        + "\n\nDRAFT TO REPAIR:\n"
        + tailored.model_dump_json()
        + "\n\nAUDIT FINDINGS:\n"
        + validation.model_dump_json()
    )
    repaired = client.chat_json(
        TailoredResume, REPAIR_SYSTEM, repair_prompt, temperature=0.05
    )
    repaired = apply_deterministic_guards(profile, repaired)
    repaired_validation = validate_draft(client, profile, repaired)
    if repaired_validation.passed and not repaired_validation.unsupported_claims:
        repaired_validation.warnings.append(
            "The first draft was automatically repaired after factual review."
        )
        return repaired, repaired_validation

    safe_fallback = TailoredResume(
        target_title=tailored.target_title,
        professional_summary=profile.professional_summary,
        highlighted_skills=profile.skills,
        experience_edits=[],
        ats_keywords_used=[],
        missing_requirements=tailored.missing_requirements,
        match_score=tailored.match_score,
        match_rationale=tailored.match_rationale,
    )
    fallback_validation = DraftValidation(
        passed=True,
        warnings=[
            (
                "Tailored language did not pass factual validation after one repair attempt. "
                "The exported resume uses the original summary, skills, and experience bullets."
            )
        ],
    )
    return safe_fallback, fallback_validation


def audit_and_repair_draft(
    client: OllamaClient,
    profile: CandidateProfile,
    tailored: TailoredResume,
) -> tuple[TailoredResume, DraftValidation]:
    """Audit bounded pieces so a complete resume does not overflow local context."""
    if tailored.tailoring_report.get("workflow") == "evidence_v1":
        # The evidence workflow audits every candidate statement individually.
        # Sending the complete source back here would undo the context bound.
        return tailored, DraftValidation(
            passed=True,
            warnings=tailored.tailoring_report.get("issues", [])
            + [
                "Each accepted rewrite was audited against its cited source. Review the evidence report before applying."
            ],
        )
    overview = tailored.model_copy(deep=True)
    overview.experience_edits = []
    result, report = _audit_and_repair_piece(client, profile, overview)
    result.target_title = tailored.target_title
    result.match_score = tailored.match_score
    result.match_rationale = tailored.match_rationale
    result.missing_requirements = tailored.missing_requirements
    result.experience_edits = []
    for edit in tailored.experience_edits:
        source = profile.experiences[edit.experience_index]
        for start in range(0, len(edit.bullets), 3):
            bullets = edit.bullets[start : start + 3]
            source_indices = sorted(
                {i for b in bullets for i in b.source_bullet_indices}
            )
            # Keep only the cited source bullets and remap them for the small audit.
            local_source = source.model_copy(
                update={"bullets": [source.bullets[i] for i in source_indices]}
            )
            local_profile = CandidateProfile(experiences=[local_source])
            local_bullets = [
                b.model_copy(
                    update={
                        "source_bullet_indices": [
                            source_indices.index(i) for i in b.source_bullet_indices
                        ]
                    }
                )
                for b in bullets
            ]
            piece = TailoredResume(
                experience_edits=[
                    ExperienceEdit(experience_index=0, bullets=local_bullets)
                ]
            )
            checked, validation = _audit_and_repair_piece(client, local_profile, piece)
            report.warnings.extend(validation.warnings)
            for checked_edit in checked.experience_edits:
                for bullet in checked_edit.bullets:
                    bullet.source_bullet_indices = [
                        source_indices[i] for i in bullet.source_bullet_indices
                    ]
                result.experience_edits.append(
                    ExperienceEdit(
                        experience_index=edit.experience_index,
                        bullets=checked_edit.bullets,
                    )
                )
    # Coalesce audited batches before deterministic guards deduplicate roles.
    combined: dict[int, ExperienceEdit] = {}
    for edit in result.experience_edits:
        combined.setdefault(
            edit.experience_index,
            ExperienceEdit(experience_index=edit.experience_index),
        ).bullets.extend(edit.bullets)
    result.experience_edits = list(combined.values())
    report.warnings = list(dict.fromkeys(report.warnings))
    return apply_deterministic_guards(profile, result), report


def _skill_is_grounded(skill: str, evidence: str) -> bool:
    normalized_skill = " ".join(
        skill.casefold().replace("/", " ").replace("-", " ").split()
    )
    normalized_evidence = " ".join(
        evidence.casefold().replace("/", " ").replace("-", " ").split()
    )
    if normalized_skill in normalized_evidence:
        return True
    tokens = [token for token in normalized_skill.split() if len(token) > 2]
    return bool(tokens) and all(token in normalized_evidence for token in tokens)


def apply_deterministic_guards(
    profile: CandidateProfile, draft: TailoredResume
) -> TailoredResume:
    """Remove structurally invalid or clearly unsupported model output before rendering."""
    evidence = profile.evidence_text()
    evidence_numbers = extract_numbers(evidence)

    if extract_numbers(draft.professional_summary) - evidence_numbers:
        draft.professional_summary = profile.professional_summary

    draft.highlighted_skills = [
        skill
        for skill in draft.highlighted_skills
        if _skill_is_grounded(skill, evidence)
    ]
    if not draft.highlighted_skills:
        draft.highlighted_skills = profile.skills

    guarded_edits = []
    used_experiences: set[int] = set()
    for edit in draft.experience_edits:
        index = edit.experience_index
        if index in used_experiences or index < 0 or index >= len(profile.experiences):
            continue
        source_bullets = profile.experiences[index].bullets
        guarded_bullets = []
        for bullet in edit.bullets:
            valid_indices = sorted(
                {
                    source_index
                    for source_index in bullet.source_bullet_indices
                    if 0 <= source_index < len(source_bullets)
                }
            )
            if not valid_indices or not bullet.text.strip():
                continue
            source_text = " ".join(
                source_bullets[source_index] for source_index in valid_indices
            )
            if extract_numbers(bullet.text) - extract_numbers(source_text):
                continue
            bullet.source_bullet_indices = valid_indices
            guarded_bullets.append(bullet)
        if guarded_bullets:
            edit.bullets = guarded_bullets
            guarded_edits.append(edit)
            used_experiences.add(index)
    draft.experience_edits = guarded_edits
    draft.ats_keywords_used = [
        keyword
        for keyword in draft.ats_keywords_used
        if _skill_is_grounded(keyword, evidence)
    ]
    return draft
