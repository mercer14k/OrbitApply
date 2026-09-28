from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

import fitz
from docx import Document

from jobtailor.ai_tasks import apply_deterministic_guards
from jobtailor.exporter import create_application_package
from jobtailor.jobs import jobs_from_csv, keyword_relevance
from jobtailor.models import (
    CandidateProfile,
    ContactInfo,
    DraftValidation,
    EducationItem,
    ExperienceEdit,
    ExperienceItem,
    JobListing,
    TailoredBullet,
    TailoredResume,
)
from jobtailor.resume_parser import parse_resume_bytes


def fixture_profile() -> CandidateProfile:
    return CandidateProfile(
        contact=ContactInfo(
            name="Taylor Morgan",
            email="taylor@example.com",
            phone="555-0100",
            location="Chicago, IL",
            linkedin="https://linkedin.com/in/taylor",
        ),
        headline="Supply Chain Analyst",
        professional_summary="Supply chain analyst experienced in inventory reporting and process improvement.",
        experiences=[
            ExperienceItem(
                company="Example Manufacturing",
                title="Supply Chain Analyst",
                location="Chicago, IL",
                start_date="2022",
                end_date="Present",
                bullets=[
                    "Built Power BI inventory dashboards for operations leaders.",
                    "Reduced manual reporting time by 30% through SQL automation.",
                    "Supported SAP purchasing and inventory reconciliation.",
                ],
            )
        ],
        education=[
            EducationItem(
                institution="Example University", degree="MBA", graduation_date="2022"
            )
        ],
        skills=["Power BI", "SQL", "SAP", "Inventory Management"],
    )


def fixture_job() -> JobListing:
    return JobListing(
        listing_id="abc123",
        title="Senior Supply Chain Analyst",
        company="Acme Logistics",
        location="Chicago, IL",
        country="USA",
        date_posted="2026-09-08",
        description=(
            "Acme Logistics is seeking a supply chain analyst to build Power BI dashboards, "
            "analyze inventory, use SQL, and collaborate with purchasing stakeholders. "
            * 3
        ),
        job_url="https://example.com/careers/abc123",
        source="Fixture",
    )


class CoreTests(unittest.TestCase):
    def test_parse_docx_resume(self) -> None:
        document = Document()
        document.add_heading("Taylor Morgan", 0)
        document.add_paragraph("Supply Chain Analyst with Power BI and SQL experience.")
        document.add_paragraph("Reduced reporting time by 30%.")
        document.add_paragraph(
            "Supported inventory reconciliation, purchasing reports, and weekly operations reviews."
        )
        buffer = io.BytesIO()
        document.save(buffer)
        text = parse_resume_bytes("resume.docx", buffer.getvalue())
        self.assertIn("Taylor Morgan", text)
        self.assertIn("30%", text)

    def test_guard_removes_new_number(self) -> None:
        profile = fixture_profile()
        draft = TailoredResume(
            target_title="Senior Supply Chain Analyst",
            professional_summary="Supply chain analyst with 99 years of experience.",
            highlighted_skills=["SQL", "Python"],
            experience_edits=[
                ExperienceEdit(
                    experience_index=0,
                    bullets=[
                        TailoredBullet(
                            text="Reduced manual reporting time by 99% through SQL automation.",
                            source_bullet_indices=[1],
                        )
                    ],
                )
            ],
        )
        guarded = apply_deterministic_guards(profile, draft)
        self.assertEqual(guarded.professional_summary, profile.professional_summary)
        self.assertEqual(guarded.highlighted_skills, ["SQL"])
        self.assertEqual(guarded.experience_edits, [])

    def test_csv_import_and_relevance(self) -> None:
        payload = (
            b"title,company,job_url,description,date_posted\n"
            b'Supply Chain Analyst,Acme,https://example.com/1,"Power BI inventory role",2026-09-08\n'
        )
        jobs = jobs_from_csv(payload, "USA")
        self.assertEqual(len(jobs), 1)
        self.assertGreater(
            keyword_relevance(jobs[0], ["Supply Chain Analyst", "Power BI"]), 50
        )

    def test_export_application_package(self) -> None:
        profile = fixture_profile()
        job = fixture_job()
        tailored = TailoredResume(
            target_title=job.title,
            professional_summary=profile.professional_summary,
            highlighted_skills=["Power BI", "SQL", "Inventory Management"],
            experience_edits=[
                ExperienceEdit(
                    experience_index=0,
                    bullets=[
                        TailoredBullet(
                            text="Built Power BI inventory dashboards for operations leaders.",
                            source_bullet_indices=[0],
                        )
                    ],
                )
            ],
            match_score=82,
            match_rationale="Strong overlap in inventory analytics and reporting.",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = create_application_package(
                directory,
                profile,
                job,
                tailored,
                DraftValidation(passed=True),
                ["Supply Chain Analyst", "Power BI"],
            )
            folder = Path(result.folder)
            self.assertTrue(Path(result.resume_docx).exists())
            self.assertTrue(Path(result.resume_pdf).exists())
            self.assertTrue(Path(result.job_description_docx).exists())
            self.assertTrue(Path(result.job_description_pdf).exists())
            self.assertTrue(Path(result.apply_link).exists())
            self.assertTrue((folder / "Tailoring_Report.html").is_file())
            self.assertTrue((folder / "Tailoring_Report.json").is_file())
            with fitz.open(result.resume_pdf) as pdf:
                resume_text = "\n".join(page.get_text() for page in pdf)
            self.assertIn("Taylor Morgan", resume_text)
            self.assertIn("Power BI", resume_text)
            self.assertIn("30%", resume_text)  # Unedited bullets must not disappear.
            report = json.loads(
                (folder / "Match_Report.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(report["match_score"])
            self.assertTrue(report["validation"]["passed"])


if __name__ == "__main__":
    unittest.main()
