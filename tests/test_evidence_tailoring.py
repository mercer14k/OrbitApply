import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from test_core import fixture_job, fixture_profile

from jobtailor.ai_tasks import audit_and_repair_draft, tailor_resume
from jobtailor.ats_resume import experience_bullets
from jobtailor.evidence_tailoring import READY, fact_issues
from jobtailor.exporter import create_application_package
from jobtailor.generation_guard import one_job_at_a_time
from jobtailor.models import DraftValidation
from jobtailor.ollama_client import OllamaClient
from jobtailor.requirements_analysis import (
    Requirement,
    RequirementAnalysis,
    tailoring_changes,
)


class FixtureClient(OllamaClient):
    """Use real context/schema validation with controlled model responses."""

    def __init__(
        self, malicious_summary=False, invalid_quote=False, reject_alignment=False
    ):
        super().__init__(context_window=4096)
        self.requests = []
        self.malicious_summary = malicious_summary
        self.invalid_quote = invalid_quote
        self.reject_alignment = reject_alignment

    def _request(self, path, payload=None):
        self.requests.append(payload)
        system = payload["messages"][0]["content"]
        prompt = payload["messages"][1]["content"].removesuffix("\n/no_think")
        props = payload["format"]["properties"]
        if "support" in props:
            requirement, source = prompt.split("\nSOURCE:\n", 1)
            supported = (
                "SQL" in requirement and "SQL" in source and "aging" not in requirement
            )
            reply = {
                "support": "supported" if supported else "not_established",
                "source_quote": ("invented quote" if self.invalid_quote else source)
                if supported
                else "",
                "reason": "Explicit SQL evidence." if supported else "Not established.",
            }
        elif "faithful" in props:
            # Deliberately permissive: deterministic checks must still catch inventions.
            reply = {
                "faithful": True,
                "preserves_meaning": True,
                "preserves_metrics": True,
                "addresses_requirement": not self.reject_alignment,
                "improves_emphasis": True,
                "already_aligned": not self.reject_alignment,
                "reason": "Fixture review.",
            }
        elif "text" in props:
            if "summary sentence" in system:
                reply = {
                    "text": "Managed inventory aging methodologies."
                    if self.malicious_summary
                    else "SQL automation reduced manual reporting time by 30%."
                }
            else:
                source = (
                    prompt.split("SOURCE:\n", 1)[1]
                    .split("\nCURRENT WORDING:", 1)[0]
                    .split("\nREQUIREMENT:", 1)[0]
                )
                reply = {
                    "text": "Automated reporting with SQL, reducing manual reporting time by 30%."
                    if "Reduced manual reporting time" in source
                    else source
                }
        else:
            raise AssertionError(props)
        return {"done_reason": "stop", "message": {"content": json.dumps(reply)}}


def requirements(*quotes):
    return RequirementAnalysis(
        requirements=[Requirement(kind="responsibility", quote=q) for q in quotes]
    )


class EvidenceTailoringTests(unittest.TestCase):
    def test_meaningful_rewrite_order_summary_and_actual_folder_report(self):
        profile, job, client = fixture_profile(), fixture_job(), FixtureClient()
        analysis = requirements(
            "Use SQL to automate reporting.", "Maintain inventory aging methodologies."
        )
        draft = tailor_resume(client, profile, job, [], requirements=analysis)
        draft, validation = audit_and_repair_draft(client, profile, draft)
        self.assertEqual(draft.tailoring_report["status"], READY)
        self.assertEqual(
            draft.missing_requirements, ["Maintain inventory aging methodologies."]
        )
        self.assertNotIn("aging", draft.professional_summary)
        self.assertEqual(draft.bullet_order[0][0], 1)
        changes = tailoring_changes(profile, draft)
        self.assertEqual(changes["experience_bullets_changed"], 1)
        self.assertEqual(changes["bullet_changes"][0]["source_bullet_index"], 1)
        self.assertEqual(len(experience_bullets(profile, draft, 0)), 3)
        self.assertTrue(
            experience_bullets(profile, draft, 0)[0].startswith("Automated")
        )
        with tempfile.TemporaryDirectory() as output:
            exported = create_application_package(
                output, profile, job, draft, validation, [], analysis
            )
            folder = Path(exported.resume_pdf).parent
            self.assertEqual(Path(exported.resume_pdf).name, "Tailored_Resume.pdf")
            self.assertTrue((folder / "Tailoring_Report.html").is_file())
            self.assertIn(
                "Maintain inventory aging",
                (folder / "Tailoring_Report.html").read_text(),
            )
            self.assertIn(
                "Not established", (folder / "Tailoring_Report.html").read_text()
            )
            self.assertTrue((folder / "Tailoring_Report.json").is_file())

    def test_unsupported_summary_is_rejected_even_with_permissive_model_audit(self):
        profile, job = fixture_profile(), fixture_job()
        draft = tailor_resume(
            FixtureClient(malicious_summary=True),
            profile,
            job,
            [],
            requirements=requirements("Use SQL to automate reporting."),
        )
        self.assertNotIn("aging", draft.professional_summary)
        self.assertTrue(draft.tailoring_report["status"].startswith("Needs review"))
        with tempfile.TemporaryDirectory() as output:
            exported = create_application_package(
                output, profile, job, draft, DraftValidation(), []
            )
            self.assertEqual(Path(exported.resume_pdf).name, "Resume_Needs_Review.pdf")
            self.assertFalse(
                (Path(exported.resume_pdf).parent / "Tailored_Resume.pdf").exists()
            )

    def test_invalid_source_quote_is_not_accepted(self):
        draft = tailor_resume(
            FixtureClient(invalid_quote=True),
            fixture_profile(),
            fixture_job(),
            [],
            requirements=requirements("Use SQL to automate reporting."),
        )
        self.assertFalse(draft.experience_edits)
        self.assertTrue(draft.tailoring_report["issues"])
        self.assertTrue(draft.tailoring_report["status"].startswith("Needs review"))

    def test_writing_changes_without_alignment_do_not_pass(self):
        profile = fixture_profile()
        draft = tailor_resume(
            FixtureClient(reject_alignment=True),
            profile,
            fixture_job(),
            [],
            requirements=requirements("Use SQL to automate reporting."),
        )
        self.assertTrue(draft.tailoring_report["status"].startswith("Needs review"))
        self.assertFalse(draft.experience_edits)
        self.assertEqual(
            experience_bullets(profile, draft, 0, ordered=False),
            profile.experiences[0].bullets,
        )

    def test_large_combined_jd_and_profile_do_not_enter_one_prompt(self):
        profile, client = fixture_profile(), FixtureClient()
        profile.professional_summary = "Original resume summary. " * 1500
        analysis = requirements(
            *[f"Use SQL to automate reporting for team {i}." for i in range(90)]
        )
        draft = tailor_resume(client, profile, fixture_job(), [], requirements=analysis)
        self.assertEqual(len(draft.tailoring_report["requirements"]), 90)
        self.assertGreater(len(client.requests), 90)
        # Every request passed the real client's 4K context guard.
        self.assertTrue(all(p["options"]["num_ctx"] == 4096 for p in client.requests))
        self.assertTrue(
            all(len(p["messages"][1]["content"]) < 7000 for p in client.requests)
        )

    def test_known_meaning_errors_and_changed_metrics_are_rejected(self):
        self.assertTrue(
            fact_issues(
                "Owned procurement for 14 markets.",
                "Programmed procurement for 14 markets.",
            )
        )
        self.assertTrue(
            fact_issues(
                "Reduced excess stock by 42.2M.", "Reduced E&O inventory by 42.2M."
            )
        )
        self.assertTrue(
            fact_issues("Reduced variance by 20%.", "Reduced variance by 30%.")
        )
        self.assertFalse(
            fact_issues(
                "Reduced variance by 20%.", "Achieved a 20% reduction in variance."
            )
        )

    def test_only_one_job_can_run_and_lock_recovers_after_error(self):
        entered, release = Event(), Event()

        @one_job_at_a_time
        def job():
            entered.set()
            release.wait(timeout=5)
            raise ValueError("test failure")

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(job)
            self.assertTrue(entered.wait(timeout=2))
            try:
                with self.assertRaisesRegex(RuntimeError, "Another job"):
                    job()
            finally:
                release.set()
            with self.assertRaises(ValueError):
                first.result(timeout=2)

        @one_job_at_a_time
        def next_job():
            return "ready"

        self.assertEqual(next_job(), "ready")
