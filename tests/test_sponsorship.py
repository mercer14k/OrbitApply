import json
import unittest
from unittest.mock import Mock, patch

from test_core import fixture_job

from jobtailor.ats_sources import ats_endpoint, fetch_ats
from jobtailor.job_details import JobDescriptionError, refresh_description
from jobtailor.sponsorship import (
    INCLUDE_ALL,
    INCLUDE_H1B,
    classify_sponsorship,
    import_sponsor_history,
    included_by_sponsorship_filter,
    load_h1b_directory,
    screen_sponsorship,
    sponsorship_allowed,
)


class SponsorshipTests(unittest.TestCase):
    def test_h1b_mode_includes_general_and_conditional_employment_sponsorship(self):
        for text, status in [
            ("Sponsorship is available.", "Visa sponsorship stated; H-1B unspecified"),
            (
                "We offer sponsorship for qualified candidates.",
                "Visa sponsorship stated; H-1B unspecified",
            ),
            (
                "This position is eligible for sponsorship.",
                "Conditional; confirm with employer",
            ),
            (
                "Visa sponsorship may be considered on a case-by-case basis.",
                "Conditional; confirm with employer",
            ),
            (
                "We may provide immigration sponsorship.",
                "Conditional; confirm with employer",
            ),
            (
                "We do not discriminate and offer visa sponsorship.",
                "Visa sponsorship stated; H-1B unspecified",
            ),
        ]:
            with self.subTest(text=text):
                job = fixture_job()
                job.sponsorship_status, job.sponsorship_evidence = classify_sponsorship(
                    text
                )
                self.assertEqual(job.sponsorship_status, status)
                self.assertTrue(included_by_sponsorship_filter(job, INCLUDE_H1B))
                self.assertEqual(job.sponsorship_evidence, [text])

    def test_generic_exclusions_without_h1b_are_never_included(self):
        for text in [
            "Sponsorship is not provided.",
            "Sponsorship isn’t available for this role.",
            "Sponsorship won’t be provided.",
            "We do not have the ability to provide visa sponsorship.",
            "Visa sponsorship will not be provided for this position.",
            "Applicants must work without requiring employer sponsorship.",
            "We do not offer sponsorship now or in the future.",
            "We are not providing sponsorship at this time.",
            "We aren’t offering sponsorship for this role.",
            "This position is not eligible for sponsorship.",
            "Visa sponsorship: No",
            "Eligible for sponsorship: No",
            "Applicants requiring sponsorship will not be considered.",
            "Candidates requiring sponsorship cannot be considered.",
            "Sponsorship is not an option for this role.",
        ]:
            with self.subTest(text=text):
                job = fixture_job()
                job.sponsorship_status, _ = classify_sponsorship(text)
                self.assertEqual(job.sponsorship_status, "Not offered")
                self.assertFalse(included_by_sponsorship_filter(job, INCLUDE_H1B))
                self.assertTrue(included_by_sponsorship_filter(job, INCLUDE_ALL))

    def test_question_history_unrelated_and_conflicting_text_do_not_qualify(self):
        for text in [
            "Will you require sponsorship now or in the future?",
            "We previously offered visa sponsorship.",
            "Sponsorship available for conferences.",
            "We offer sponsorship only for other positions.",
            "We support project sponsorship and executive sponsorship.",
            "We only sponsor TN visas.",
            "This role is eligible for sponsorship. Sponsorship is not provided.",
        ]:
            with self.subTest(text=text):
                job = fixture_job()
                job.sponsorship_status, _ = classify_sponsorship(text)
                self.assertFalse(included_by_sponsorship_filter(job, INCLUDE_H1B))

    def test_final_generic_denial_overrides_offer_in_long_jd(self):
        status, quotes = classify_sponsorship(
            "We offer sponsorship.\n"
            + "Manage inventory.\n" * 8000
            + "Sponsorship is not provided for this position."
        )
        self.assertEqual(status, "Conflicting statements; review")
        self.assertIn("Sponsorship is not provided for this position.", quotes)

    def test_include_all_allows_every_sponsorship_status(self):
        for status in [
            "Not offered",
            "Not mentioned",
            "Not checked",
            "JD unavailable",
            "Conditional; confirm with employer",
            "Conflicting statements; review",
            "H-1B sponsorship stated",
            "H-1B transfers stated",
        ]:
            with self.subTest(status=status):
                job = fixture_job().model_copy(update={"sponsorship_status": status})
                self.assertTrue(included_by_sponsorship_filter(job, INCLUDE_ALL))

    def test_h1b_filter_includes_transfers_and_rejects_unknown(self):
        for status, expected in [
            ("H-1B sponsorship stated", True),
            ("H-1B transfers stated", True),
            ("Not checked", False),
            ("Not offered", False),
        ]:
            with self.subTest(status=status):
                job = fixture_job().model_copy(update={"sponsorship_status": status})
                self.assertEqual(
                    included_by_sponsorship_filter(job, INCLUDE_H1B), expected
                )

    def test_explicit_offers(self):
        for text in [
            "We offer H-1B sponsorship for this role.",
            "Acme provides H1B visa sponsorship.",
            "H‑1B sponsorship is available.",
            "H1B sponsorship: Yes",
        ]:
            with self.subTest(text=text):
                status, quotes = classify_sponsorship(text)
                self.assertEqual(status, "H-1B sponsorship stated")
                self.assertEqual(quotes, [text])

    def test_negations_questions_and_conditions_never_pass(self):
        for text in [
            "We do not offer H1B sponsorship.",
            "We are unable to support H1B candidates.",
            "We offer H1B sponsorship only for other roles.",
            "Candidates must work without employer sponsorship.",
            "Visa sponsorship is not available.",
            "Will you now or in the future require H1B sponsorship?",
            "We may offer H1B sponsorship.",
            "We offer H1B sponsorship to eligible applicants.",
            "We offer visa sponsorship, but not H1B.",
            "We offer H1B sponsorship. Sponsorship is not available for this role.",
            "H1B sponsorship policy",
            "Sponsorship available for conferences.",
        ]:
            with self.subTest(text=text):
                job = fixture_job()
                job.sponsorship_status, _ = classify_sponsorship(text)
                self.assertFalse(sponsorship_allowed(job))
                self.assertFalse(sponsorship_allowed(job, h1b_only=False))

    def test_generic_visa_not_h1b(self):
        job = fixture_job()
        job.sponsorship_status, _ = classify_sponsorship("We offer visa sponsorship.")
        self.assertFalse(sponsorship_allowed(job))
        self.assertTrue(sponsorship_allowed(job, h1b_only=False))

    def test_transfers_are_separate(self):
        for text in [
            "We sponsor H1B transfers.",
            "H-1B transfers only.",
            "We accept H1B transfers.",
        ]:
            with self.subTest(text=text):
                job = fixture_job()
                job.sponsorship_status, _ = classify_sponsorship(text)
                self.assertEqual(job.sponsorship_status, "H-1B transfers stated")
                self.assertFalse(sponsorship_allowed(job))
                self.assertTrue(sponsorship_allowed(job, allow_transfers=True))

    def test_history_cannot_override_jd_and_exact_company_required(self):
        job = fixture_job()
        job.description_checked_at = "2026-09-08"
        job.description_source = job.job_url
        job.description = "No visa sponsorship available."
        history = [
            {
                "company": "Acme Logistics Inc",
                "source_url": "https://example.com/history",
            },
            {
                "company": "Acme Logistics Partners",
                "source_url": "https://example.com/other",
            },
        ]
        screen_sponsorship(job, history)
        self.assertEqual(len(job.sponsor_history), 1)
        self.assertFalse(sponsorship_allowed(job))

    def test_unverified_snippet_cannot_qualify(self):
        job = fixture_job()
        job.description = "We offer H1B sponsorship."
        screen_sponsorship(job)
        self.assertEqual(job.sponsorship_status, "JD unavailable")

    def test_last_line_of_long_jd_is_screened(self):
        status, quotes = classify_sponsorship(
            "Build inventory reports.\n" * 8000 + "We offer H1B sponsorship."
        )
        self.assertEqual(status, "H-1B sponsorship stated")
        self.assertEqual(quotes, ["We offer H1B sponsorship."])

    def test_csv_import_labels_unverified(self):
        rows = import_sponsor_history(
            b"company,source_url,year\nAcme,https://example.com/history,2025\n"
        )
        self.assertIn("not independently verified", rows[0]["status"])
        with self.assertRaises(ValueError):
            import_sponsor_history(b"company,source_url\nAcme,file:///tmp/fake\n")

    def test_blocked_directory_is_unknown(self):
        with (
            patch(
                "jobtailor.sponsorship.read_public",
                side_effect=OSError("403 Forbidden"),
            ),
            self.assertRaisesRegex(ValueError, "remains unknown"),
        ):
            load_h1b_directory()


class RetrievalFallbackTests(unittest.TestCase):
    def test_greenhouse_decodes_complete_description(self):
        reader = Mock(
            return_value=(
                json.dumps(
                    {
                        "id": 123,
                        "title": "Analyst",
                        "content": "&lt;p&gt;Responsibilities&lt;/p&gt;&lt;p&gt;We offer H1B sponsorship.&lt;/p&gt;",
                    }
                ),
                "api",
            )
        )
        text, source = fetch_ats(
            "https://job-boards.greenhouse.io/acme/jobs/123", "Analyst", reader
        )
        self.assertIn("Responsibilities", text)
        self.assertIn("We offer H1B sponsorship.", text)
        self.assertIn("boards-api.greenhouse.io", source)

    def test_lever_keeps_final_sections(self):
        reader = Mock(
            return_value=(
                json.dumps(
                    {
                        "id": "abc",
                        "text": "Analyst",
                        "descriptionPlain": "Responsibilities",
                        "lists": [
                            {
                                "text": "Qualifications",
                                "content": "<li>SQL required</li>",
                            }
                        ],
                        "additionalPlain": "We offer H1B sponsorship.",
                        "closingPlain": "Final information",
                    }
                ),
                "api",
            )
        )
        text, _ = fetch_ats("https://jobs.lever.co/acme/abc", "Analyst", reader)
        for section in [
            "Responsibilities",
            "Qualifications",
            "SQL required",
            "We offer H1B sponsorship.",
            "Final information",
        ]:
            self.assertIn(section, text)

    def test_wrong_ats_role_is_rejected(self):
        reader = Mock(
            return_value=(json.dumps({"id": "wrong", "text": "Analyst"}), "api")
        )
        with self.assertRaises(JobDescriptionError):
            fetch_ats("https://jobs.lever.co/acme/abc", "Analyst", reader)
        self.assertIsNone(ats_endpoint("https://jobs.lever.co.evil.example/acme/abc"))

    def test_empty_static_page_uses_browser(self):
        job = fixture_job()
        with patch(
            "jobtailor.job_details.retrieve",
            side_effect=[
                ("", "", []),
                ("SQL experience required. We offer H1B sponsorship.", "employer", []),
            ],
        ) as retrieve:
            refresh_description(job, use_browser=True)
        self.assertEqual(retrieve.call_count, 2)
        self.assertTrue(retrieve.call_args.kwargs["rendered"])
        self.assertIn("browser rendered", job.description_source)

    def test_failed_browser_does_not_accept_snippet(self):
        with (
            patch(
                "jobtailor.job_details.retrieve",
                side_effect=[("", "", []), RuntimeError("browser unavailable")],
            ),
            self.assertRaises(JobDescriptionError),
        ):
            refresh_description(fixture_job(), use_browser=True)


if __name__ == "__main__":
    unittest.main()
