import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import fixture_job
from test_selection_ui import prepared_fixture

from jobtailor.models import DraftValidation, SearchSettings, TailoredResume
from jobtailor.requirements_analysis import Requirement, RequirementAnalysis
from jobtailor.workspace import create_role_folder, discover


class WorkspaceTests(unittest.TestCase):
    def test_only_selected_titles_are_queried_and_search_writes_no_folders(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "not_created"
            with patch(
                "jobtailor.workspace.search_jobs", return_value=([fixture_job()], [])
            ) as search:
                result = discover(
                    prepared_fixture(),
                    ["Inventory Analyst"],
                    SearchSettings(keywords=[]),
                    str(output),
                )
            queries = search.call_args.args[0].keywords
            self.assertEqual(queries, ["Inventory Analyst", "Inventory Analyst SQL"])
            self.assertFalse(output.exists())
            self.assertEqual(result["roles"][0]["folder"], "")

    def test_requested_role_only_has_actual_tailored_files(self):
        with tempfile.TemporaryDirectory() as output:
            first = fixture_job()
            other = first.model_copy(
                update={"listing_id": "other", "job_url": "https://example.com/other"}
            )
            with patch(
                "jobtailor.workspace.search_jobs", return_value=([first, other], [])
            ):
                workspace = discover(
                    prepared_fixture(),
                    ["Inventory Analyst"],
                    SearchSettings(keywords=[]),
                    output,
                )
            client = Mock()
            client.is_ready.return_value = (True, "Ready")
            draft = TailoredResume(
                target_title=first.title,
                tailoring_report={"status": "Draft ready; review before applying"},
                professional_summary="Supply chain analyst experienced in SQL automation and Power BI inventory dashboards.",
            )

            def fetch(job, **kwargs):
                job.description = "Build Power BI inventory dashboards. No visa sponsorship available."
                job.description_source = job.job_url
                job.description_checked_at = "2026-09-09"
                return job

            requirements = RequirementAnalysis(
                requirements=[
                    Requirement(
                        kind="responsibility",
                        quote="Build Power BI inventory dashboards.",
                    )
                ]
            )
            with (
                patch("jobtailor.automatic.enrich_job_description", side_effect=fetch),
                patch(
                    "jobtailor.automatic.analyze_requirements",
                    return_value=requirements,
                ),
                patch(
                    "jobtailor.automatic.tailor_resume", return_value=draft
                ) as tailor,
                patch(
                    "jobtailor.automatic.audit_and_repair_draft",
                    return_value=(draft, DraftValidation(passed=True)),
                ),
            ):
                row = create_role_folder(workspace, first.listing_id, client)
                self.assertEqual(
                    row["sponsorship_evidence"], ["No visa sponsorship available."]
                )
                self.assertEqual(row["sponsorship_source"], first.job_url)
                self.assertTrue(Path(row["resume_pdf"]).is_file())
                folder = Path(row["folder"])
                for filename in [
                    "Tailored_Resume.docx",
                    "Job_Description.pdf",
                    "APPLY_HERE.url",
                    "Tailoring_Changes.json",
                    "JD_Requirements.json",
                ]:
                    self.assertTrue((folder / filename).is_file(), filename)
                other_row = next(
                    r for r in workspace["roles"] if r["listing_id"] == "other"
                )
                self.assertEqual(other_row["folder"], "")
                self.assertEqual(other_row["status"], "Not prepared")
                same = create_role_folder(workspace, first.listing_id, client)
                self.assertEqual(same["folder"], row["folder"])
                tailor.assert_called_once()
            self.assertTrue((Path(workspace["folder"]) / "Applications.html").is_file())


if __name__ == "__main__":
    unittest.main()
