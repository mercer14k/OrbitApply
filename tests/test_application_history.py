import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_selection_ui as selection_ui
from streamlit.testing.v1 import AppTest
from test_core import fixture_job
from test_selection_ui import APP, prepared_fixture

from jobtailor.application_history import (
    ApplicationHistory,
    candidate_key,
    posting_keys,
)
from jobtailor.jobs import deduplicate_jobs
from jobtailor.models import SearchSettings
from jobtailor.sponsorship import INCLUDE_H1B
from jobtailor.workspace import discover


def row(url, **kwargs):
    return {"role": "Analyst", "company": "Acme", "url": url, **kwargs}


class ApplicationHistoryTests(unittest.TestCase):
    def test_yes_survives_new_instance_and_undo_restores_only_this_candidate(self):
        with tempfile.TemporaryDirectory() as output:
            path = Path(output) / "history.sqlite3"
            first = ApplicationHistory(path)
            job = row("https://www.linkedin.com/jobs/view/4417174133/?trk=search")
            record = first.mark_applied("person-a", job)
            self.assertEqual(record, first.mark_applied("person-a", job))
            again = ApplicationHistory(path)
            self.assertEqual(len(again.list_applied("person-a")), 1)
            self.assertTrue(
                posting_keys(
                    row(
                        "https://linkedin.com/jobs/view/supply-chain-manager-4417174133?refId=new"
                    )
                )
                & again.keys("person-a")
            )
            self.assertFalse(again.keys("person-b"))
            again.undo("person-b", record)
            self.assertTrue(again.keys("person-a"))
            again.undo("person-a", record)
            self.assertEqual(first.list_applied("person-a"), [])
            self.assertEqual(first.keys("person-a"), set())

    def test_resume_edits_keep_same_candidate_and_different_people_are_separate(self):
        before = prepared_fixture()
        after = prepared_fixture()
        after["profile"]["headline"] = "Updated resume headline"
        self.assertEqual(candidate_key(before), candidate_key(after))
        after["profile"]["contact"]["email"] = "another-person@example.com"
        self.assertNotEqual(candidate_key(before), candidate_key(after))

    def test_known_posting_ids_ignore_tracking_but_keep_new_requisitions(self):
        pairs = [
            (
                "https://www.indeed.com/viewjob?jk=abcd1234&from=search",
                "https://www.indeed.com/jobs?vjk=abcd1234&q=analyst",
            ),
            (
                "https://boards.greenhouse.io/acme/jobs/123?gh_src=one",
                "https://job-boards.greenhouse.io/acme/jobs/123?gh_src=two",
            ),
            (
                "https://jobs.lever.co/acme/abc-def",
                "https://jobs.lever.co/acme/abc-def/apply?lever-source=search",
            ),
            (
                "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/A/Analyst_JR123",
                "https://acme.wd1.myworkdayjobs.com/Careers/job/B/Senior-Analyst_JR123/apply",
            ),
            (
                "https://example.com/job?id=123&country=US&utm_source=foo",
                "https://example.com/job?country=US&id=123&utm_source=bar",
            ),
        ]
        for original, variant in pairs:
            with self.subTest(original=original):
                self.assertTrue(
                    posting_keys(row(original)) & posting_keys(row(variant))
                )
                self.assertFalse(
                    posting_keys(row(original))
                    & posting_keys(
                        row(variant.replace("123", "999").replace("abc-def", "new-id"))
                    )
                )
        self.assertFalse(
            posting_keys(row("https://linkedin.com/jobs/view/123"))
            & posting_keys(row("https://linkedin.com.evil.example/jobs/view/123"))
        )

    def test_board_and_employer_aliases_survive_deduplication(self):
        first = fixture_job().model_copy(
            update={"source_urls": ["https://indeed.com/viewjob?jk=abcd1234"]}
        )
        second = first.model_copy(
            update={
                "source_urls": ["https://linkedin.com/jobs/view/12345"],
                "description": "Longer description" * 100,
            }
        )
        merged = deduplicate_jobs([first, second])[0]
        self.assertEqual(len(merged.source_urls), 2)
        keys = posting_keys(merged.model_dump())
        self.assertIn("indeed:abcd1234", keys)
        self.assertIn("linkedin:12345", keys)

    def test_future_search_excludes_confirmed_role_before_jd_checks(self):
        job = fixture_job()
        another = job.model_copy(
            update={
                "listing_id": "different",
                "job_url": "https://example.com/different-job",
            }
        )
        with (
            patch("jobtailor.workspace.search_jobs", return_value=([job, another], [])),
            patch("jobtailor.workspace.enrich_job_description") as fetch,
        ):
            result = discover(
                prepared_fixture(),
                ["Analyst"],
                SearchSettings(keywords=[]),
                "unused",
                INCLUDE_H1B,
                excluded_posting_keys=posting_keys(job.model_dump()),
            )
        self.assertEqual(result["applied_hidden"], 1)
        self.assertEqual(
            [item["listing_id"] for item in result["roles"]], ["different"]
        )
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0].listing_id, "different")


class AppliedUITests(unittest.TestCase):
    setup_app = selection_ui.InteractiveUITests.setup_app

    def start_search(self, app):
        app.button(key="start_v09").click().run()
        app.session_state.search_task_v013.wait()
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def click_apply(self, app):
        # Link buttons do not yet have an AppTest helper; dispatch their real
        # registered widget trigger, exactly as the browser does on a click.
        link = next(
            element
            for element in app.get("link_button")
            if element.proto.label == "Apply"
        )
        state = app._tree.get_widget_states()
        trigger = state.widgets.add()
        trigger.id = link.proto.id
        trigger.trigger_value = True
        app._run(state)
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_apply_prompts_yes_hides_and_new_session_remembers_and_undo_restores(self):
        app = self.setup_app()
        self.start_search(app)
        self.click_apply(app)
        history = ApplicationHistory()
        who = candidate_key(prepared_fixture())
        self.assertEqual(history.list_applied(who), [])
        self.assertEqual(app.button(key="confirm_applied_v014").label, "Yes, I applied")
        app.button(key="confirm_applied_v014").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(len(history.list_applied(who)), 1)
        self.assertFalse(
            [link for link in app.get("link_button") if link.proto.label == "Apply"]
        )
        restarted = AppTest.from_file(str(APP), default_timeout=20).run()
        self.start_search(restarted)
        self.assertTrue(self.search.call_args.kwargs["excluded_posting_keys"])
        self.assertFalse(
            [
                link
                for link in restarted.get("link_button")
                if link.proto.label == "Apply"
            ]
        )
        record = history.list_applied(who)[0]
        restarted.button(key=f"undo_applied_{record['id']}").click().run()
        self.assertFalse(restarted.exception)
        self.assertEqual(history.list_applied(who), [])
        self.assertTrue(
            [
                link
                for link in restarted.get("link_button")
                if link.proto.label == "Apply"
            ]
        )

    def test_no_does_not_persist_or_hide_and_save_failure_leaves_prompt(self):
        app = self.setup_app()
        self.start_search(app)
        self.click_apply(app)
        app.button(key="not_applied_v014").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(
            ApplicationHistory().list_applied(candidate_key(prepared_fixture())), []
        )
        self.assertTrue(
            [link for link in app.get("link_button") if link.proto.label == "Apply"]
        )
        self.click_apply(app)
        with patch(
            "jobtailor.application_ui.ApplicationHistory.mark_applied",
            side_effect=OSError("Disk full"),
        ):
            app.button(key="confirm_applied_v014").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state.pending_applications_v014), 1)
        self.assertTrue(
            any("could not be saved" in message.value for message in app.error)
        )
        self.assertEqual(
            ApplicationHistory().list_applied(candidate_key(prepared_fixture())), []
        )
