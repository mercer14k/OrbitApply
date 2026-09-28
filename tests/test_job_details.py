import json
import unittest
from unittest.mock import Mock, patch

from test_core import fixture_job, fixture_profile

from jobtailor.job_details import (
    JobDescriptionError,
    refresh_description,
    retrieve,
    workday_endpoint,
)
from jobtailor.models import ExperienceEdit, TailoredBullet, TailoredResume
from jobtailor.requirements_analysis import (
    Requirement,
    RequirementBatch,
    analyze_requirements,
    tailoring_changes,
)

WORKDAY = "https://tenant.wd1.myworkdayjobs.com/en-US/Careers/job/Office/Senior-Supply-Chain-Analyst_JR123"
GENERIC = "Join our team for opportunities to grow. We are a leading provider of logistics solutions. Apply today! IE"
JD = "Responsibilities\nBuild Power BI dashboards for inventory planning.\nQualifications\nThree years of SQL experience required."


class JobDetailTests(unittest.TestCase):
    def test_always_fetches_even_long_description_and_repeat_run(self):
        job = fixture_job()
        job.description = GENERIC * 500
        with patch(
            "jobtailor.job_details.retrieve", return_value=(JD, "employer", [])
        ) as fetch:
            refresh_description(job)
            refresh_description(job)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(job.description, JD)
        self.assertTrue(job.description_checked_at)

    def test_manual_override_still_checks_employer(self):
        job = fixture_job()
        job.description_override = JD
        with patch(
            "jobtailor.job_details.retrieve", return_value=(GENERIC, "employer", [])
        ) as fetch:
            refresh_description(job)
        fetch.assert_called_once()
        self.assertEqual(job.description, JD)
        self.assertEqual(job.description_source, "User-provided full JD")

    def test_failed_fetch_never_accepts_long_snippet(self):
        job = fixture_job()
        job.description = JD * 500
        with (
            patch("jobtailor.job_details.retrieve", return_value=("", "", ["Blocked"])),
            self.assertRaises(JobDescriptionError),
        ):
            refresh_description(job)
        self.assertIn("Retrieval failed", job.description_status)

    def test_workday_endpoint_with_and_without_locale(self):
        expected = "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/Careers/job/Office/Senior-Supply-Chain-Analyst_JR123"
        self.assertEqual(workday_endpoint(WORKDAY), expected)
        self.assertEqual(workday_endpoint(WORKDAY.replace("/en-US", "")), expected)
        self.assertIsNone(
            workday_endpoint("https://myworkdayjobs.com.evil.example/Careers/job/123")
        )

    def test_workday_reads_dynamic_detail_after_html_shell(self):
        job = fixture_job().model_copy(update={"job_url": WORKDAY})
        responses = [
            ("<html>Loading</html>", WORKDAY),
            (
                json.dumps(
                    {
                        "jobPostingInfo": {
                            "title": job.title,
                            "jobReqId": "JR123",
                            "jobDescription": "<p>" + JD + "</p>",
                        }
                    }
                ),
                workday_endpoint(WORKDAY),
            ),
        ]
        with patch("jobtailor.job_details.read_public", side_effect=responses) as read:
            text, source, _notes = retrieve(job)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(text, JD)
        self.assertIn("/wday/cxs/", source)

    def test_different_workday_requisition_is_rejected(self):
        job = fixture_job().model_copy(update={"job_url": WORKDAY})
        responses = [
            ("<html/>", WORKDAY),
            (
                json.dumps(
                    {
                        "jobPostingInfo": {
                            "title": job.title,
                            "jobReqId": "JR999",
                            "jobDescription": JD,
                        }
                    }
                ),
                WORKDAY,
            ),
        ]
        with patch("jobtailor.job_details.read_public", side_effect=responses):
            text, _, notes = retrieve(job)
        self.assertEqual(text, "")
        self.assertTrue(any("different requisition" in n for n in notes))

    def test_multiple_jsonld_jobs_selects_matching_role(self):
        job = fixture_job()
        html = (
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@graph": [
                        {
                            "@type": "JobPosting",
                            "title": "Nurse",
                            "description": "Wrong role",
                        },
                        {"@type": "JobPosting", "title": job.title, "description": JD},
                    ]
                }
            )
            + "</script>"
        )
        with patch(
            "jobtailor.job_details.read_public", return_value=(html, job.job_url)
        ):
            text, _, _ = retrieve(job)
        self.assertEqual(text, JD)

    def test_no_generic_main_page_accepted(self):
        job = fixture_job()
        with patch(
            "jobtailor.job_details.read_public",
            return_value=("<main>" + GENERIC * 100 + "</main>", job.job_url),
        ):
            text, _, _ = retrieve(job)
        self.assertEqual(text, "")

    def test_full_html_description_beats_short_structured_summary(self):
        job = fixture_job()
        html = (
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "JobPosting",
                    "title": job.title,
                    "description": "Short summary",
                }
            )
            + '</script><div id="job-description">'
            + JD
            + "</div>"
        )
        with patch(
            "jobtailor.job_details.read_public", return_value=(html, job.job_url)
        ):
            text, _, _ = retrieve(job)
        self.assertEqual(text, JD)

    def test_boilerplate_requirement_invention_rejected(self):
        client = Mock()
        job = fixture_job().model_copy(update={"description": GENERIC})
        client.chat_json.return_value = RequirementBatch(
            requirements=[
                Requirement(
                    kind="required",
                    quote="Five years industrial engineering experience",
                ),
                Requirement(
                    kind="responsibility",
                    quote="Join our team for opportunities to grow.",
                ),
            ]
        )
        with self.assertRaisesRegex(JobDescriptionError, "no grounded role"):
            analyze_requirements(client, job)

    def test_all_jd_chunks_are_analyzed_including_last_requirement(self):
        client = Mock()
        job = fixture_job().model_copy(
            update={
                "description": ("Company background only.\n" * 1700)
                + "Final requirement: Manage inventory planning.\n"
            }
        )

        def response(model, system, prompt, **kwargs):
            quotes = (
                [Requirement(kind="responsibility", quote="Manage inventory planning.")]
                if "Manage inventory planning." in prompt
                else []
            )
            return RequirementBatch(requirements=quotes)

        client.chat_json.side_effect = response
        result = analyze_requirements(client, job, fixture_profile())
        self.assertGreater(result.chunks_reviewed, 10)
        self.assertEqual(result.source_characters, len(job.description))
        self.assertEqual(result.requirements[0].quote, "Manage inventory planning.")
        self.assertEqual(len(result.evidence_candidates), 1)

    def test_noop_is_not_successful_tailoring_even_with_target_title(self):
        profile = fixture_profile()
        changes = tailoring_changes(
            profile, TailoredResume(target_title="New target title")
        )
        self.assertEqual(changes["status"], "No substantive tailoring")
        self.assertEqual(changes["experience_bullets_changed"], 0)

    def test_final_changes_record_exact_before_after(self):
        profile = fixture_profile()
        draft = TailoredResume(
            experience_edits=[
                ExperienceEdit(
                    experience_index=0,
                    bullets=[
                        TailoredBullet(
                            text="Created Power BI inventory reporting dashboards for operations leaders.",
                            source_bullet_indices=[0],
                        )
                    ],
                )
            ]
        )
        changes = tailoring_changes(profile, draft)
        self.assertEqual(changes["experience_bullets_changed"], 1)
        self.assertEqual(
            changes["bullet_changes"][0]["before"], profile.experiences[0].bullets[0]
        )
        self.assertEqual(changes["status"], "Text changes made; review JD alignment")


if __name__ == "__main__":
    unittest.main()
