import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from test_core import fixture_job, fixture_profile

from jobtailor.automatic import automatic_keywords, run_automatic, search_queries
from jobtailor.job_details import JobDescriptionError
from jobtailor.jobs import search_jobs
from jobtailor.models import (
    DraftValidation,
    KeywordSet,
    KeywordSuggestion,
    SearchSettings,
    TailoredResume,
)
from jobtailor.requirements_analysis import Requirement, RequirementAnalysis
from jobtailor.sponsorship import INCLUDE_H1B


class AutomaticTests(unittest.TestCase):
    def test_keyword_count_is_not_fixed_and_source_skills_are_kept(self):
        profile = fixture_profile()
        profile.skills += [f"Tool {i}" for i in range(25)]
        suggestions = KeywordSet(
            keywords=[
                KeywordSuggestion(keyword=f"Skill {i}", category="Functional skill")
                for i in range(20)
            ]
        )
        keywords = automatic_keywords(profile, suggestions.keywords)
        self.assertTrue(set(profile.skills) <= {k.keyword for k in keywords})
        self.assertGreater(len(keywords), 40)
        queries = search_queries(keywords)
        self.assertEqual(len(queries), len(keywords))
        self.assertTrue(all(profile.experiences[0].title in query for query in queries))

    def test_search_runs_beyond_ten_queries(self):
        scrape = Mock(return_value=pd.DataFrame())
        with (
            patch.dict(sys.modules, {"jobspy": SimpleNamespace(scrape_jobs=scrape)}),
            patch("jobtailor.jobs.time.sleep"),
        ):
            search_jobs(SearchSettings(keywords=[f"Role {i}" for i in range(13)]))
        self.assertEqual(scrape.call_count, 13)

    def run_case(
        self,
        count=2,
        blocked=False,
        no_changes=False,
        failed_audit=False,
        h1b=False,
        keyword_error=False,
    ):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        profile = fixture_profile()
        jobs = [
            fixture_job().model_copy(
                update={
                    "listing_id": str(i),
                    "job_url": f"https://example.com/jobs/{i}",
                    "title": f"Analyst {i}",
                }
            )
            for i in range(count)
        ]
        client = Mock()
        client.is_ready.return_value = (True, "Ready")

        def fetch(job, **kwargs):
            if blocked and job.listing_id == "0":
                job.description_source = "Unverified listing snippet"
                raise JobDescriptionError("Employer JD unavailable")
            job.description = (
                "Build Power BI dashboards for inventory."
                if int(job.listing_id) % 2
                else "Use SQL to automate purchasing reports."
            ) + " No visa sponsorship available."
            job.description_source = job.job_url
            job.description_checked_at = "2026-09-09"
            return job

        def analyze(client, job, profile):
            return RequirementAnalysis(
                requirements=[
                    Requirement(
                        kind="responsibility",
                        quote=job.description.split(". ")[0] + ".",
                    )
                ],
                source_characters=len(job.description),
                chunks_reviewed=1,
            )

        def tailor(client, profile, job, keywords, requirements):
            self.assertIn(requirements.requirements[0].quote, job.description)
            summary = (
                "Supply chain analyst experienced in SQL purchasing report automation."
                if "SQL" in requirements.requirements[0].quote
                else "Supply chain analyst experienced in Power BI inventory dashboards."
            )
            return TailoredResume(
                target_title=job.title,
                professional_summary=profile.professional_summary
                if no_changes
                else summary,
                tailoring_report={}
                if no_changes
                else {"status": "Draft ready; review before applying"},
            )

        with ExitStack() as stack:

            def patch_engine(name, **kwargs):
                return stack.enter_context(
                    patch(f"jobtailor.automatic.{name}", **kwargs)
                )

            patch_engine("parse_resume_bytes", return_value=profile.evidence_text())
            patch_engine("extract_candidate_profile", return_value=profile)
            patch_engine(
                "suggest_keywords",
                **(
                    {"side_effect": RuntimeError("Model expansion failed")}
                    if keyword_error
                    else {
                        "return_value": KeywordSet(
                            keywords=[
                                KeywordSuggestion(
                                    keyword="Supply Chain Analyst", category="Job title"
                                )
                            ]
                        )
                    }
                ),
            )
            patch_engine("search_jobs", return_value=(jobs, []))
            patch_engine("enrich_job_description", side_effect=fetch)
            patch_engine("analyze_requirements", side_effect=analyze)
            tailor_mock = patch_engine("tailor_resume", side_effect=tailor)
            patch_engine(
                "audit_and_repair_draft",
                side_effect=lambda c, p, d: (
                    d,
                    DraftValidation(passed=not failed_audit),
                ),
            )
            kwargs = {"sponsorship_filter": INCLUDE_H1B} if h1b else {}
            report = run_automatic(
                "resume.txt",
                b"fixture",
                temp.name,
                client,
                SearchSettings(keywords=[]),
                use_browser=False,
                **kwargs,
            )
        return report, tailor_mock.call_count

    def test_all_roles_processed_and_actual_jd_tailored_files_created(self):
        report, count = self.run_case(count=12)
        self.assertEqual(report["status"], "Complete", report["warnings"])
        self.assertEqual(count, 12)
        self.assertEqual(len(report["roles"]), 12)
        for row in report["roles"]:
            self.assertEqual(row["status"], "Draft ready; review before applying", row)
            folder = Path(row["folder"])
            self.assertTrue(Path(row["resume_pdf"]).is_file())
            self.assertEqual(Path(row["resume_pdf"]).name, "Tailored_Resume.pdf")
            self.assertTrue((folder / "Job_Description.pdf").is_file())
            self.assertTrue((folder / "APPLY_HERE.url").is_file())
            self.assertTrue((folder / "Tailoring_Changes.json").is_file())
        drafts = [
            json.loads((Path(row["folder"]) / "Final_Tailoring.json").read_text())
            for row in report["roles"][:2]
        ]
        self.assertNotEqual(
            drafts[0]["professional_summary"], drafts[1]["professional_summary"]
        )

    def test_blocked_jd_keeps_link_and_next_role_completes(self):
        report, count = self.run_case(blocked=True)
        by_url = {row["url"]: row for row in report["roles"]}
        failed = by_url["https://example.com/jobs/0"]
        self.assertEqual(failed["status"], "Needs review")
        self.assertTrue((Path(failed["folder"]) / "APPLY_HERE.url").is_file())
        self.assertFalse(list(Path(failed["folder"]).glob("*Resume.pdf")))
        self.assertEqual(count, 1)
        self.assertEqual(
            by_url["https://example.com/jobs/1"]["status"],
            "Draft ready; review before applying",
        )

    def test_unchanged_resume_is_never_labelled_prepared(self):
        report, _ = self.run_case(count=1, no_changes=True)
        self.assertEqual(
            report["roles"][0]["status"], "Needs review: JD alignment not checked"
        )
        self.assertEqual(
            Path(report["roles"][0]["resume_pdf"]).name, "Resume_Needs_Review.pdf"
        )

    def test_failed_factual_audit_does_not_export_resume(self):
        report, _ = self.run_case(count=1, failed_audit=True)
        row = report["roles"][0]
        self.assertEqual(row["status"], "Needs review")
        self.assertFalse(list(Path(row["folder"]).glob("*Resume.pdf")))

    def test_h1b_filter_skips_tailoring_for_no_sponsorship(self):
        report, count = self.run_case(count=1, h1b=True)
        self.assertEqual(count, 0)
        self.assertEqual(report["roles"][0]["status"], "Excluded by H1B filter")

    def test_keyword_expansion_failure_still_processes_source_keywords(self):
        report, count = self.run_case(count=1, keyword_error=True)
        self.assertEqual(count, 1)
        self.assertIn("SQL", report["keywords"])
        self.assertEqual(report["status"], "Complete")


if __name__ == "__main__":
    unittest.main()
