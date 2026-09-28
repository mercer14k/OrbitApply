import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from test_core import fixture_job
from test_selection_ui import prepared_fixture, workspace_fixture

from jobtailor.job_details import refresh_description
from jobtailor.models import SearchSettings
from jobtailor.search_task import SearchTask
from jobtailor.sponsorship import INCLUDE_ALL, INCLUDE_H1B, screen_sponsorship


def payload(output, mode=INCLUDE_ALL):
    return {
        "prepared": prepared_fixture(),
        "roles": ["Inventory Analyst", "Planner"],
        "settings": SearchSettings(keywords=[], include_unknown_dates=True),
        "output": output,
        "sponsorship": mode,
        "browser": False,
    }


class SearchCancellationTests(unittest.TestCase):
    def test_cancel_retains_first_query_skips_late_response_and_remaining_queries(self):
        entered, release = Event(), Event()
        queries = []

        def scrape(**kwargs):
            queries.append(kwargs["search_term"])
            if len(queries) == 2:
                entered.set()
                release.wait(5)
            return pd.DataFrame(
                [
                    {
                        "title": f"Role {len(queries)}",
                        "company": "Acme",
                        "job_url": f"https://example.com/{len(queries)}",
                    }
                ]
            )

        with (
            tempfile.TemporaryDirectory() as output,
            patch.dict(sys.modules, {"jobspy": SimpleNamespace(scrape_jobs=scrape)}),
        ):
            task = SearchTask(payload(output))
            try:
                task.start()
                self.assertTrue(entered.wait(3))
                self.assertTrue(task.cancel())
                cancelled = task.snapshot()
                self.assertFalse(task.busy)
                self.assertEqual(cancelled["state"], "cancelled")
                self.assertEqual(len(cancelled["workspace"]["roles"]), 1)
                self.assertEqual(cancelled["workspace"]["roles"][0]["role"], "Role 1")
                self.assertEqual(list(Path(output).iterdir()), [])
                # A new search finishes independently while the old call is blocked.
                fresh = SearchTask(
                    payload(output),
                    discoverer=Mock(return_value=workspace_fixture(output)),
                )
                fresh.start()
                fresh.wait()
                self.assertEqual(fresh.snapshot()["state"], "done")
            finally:
                release.set()
                task.wait()
            self.assertEqual(len(queries), 2)
            self.assertEqual(task.snapshot(), cancelled)

    def test_cancel_during_sponsorship_preserves_screened_results_only(self):
        entered, release = Event(), Event()
        jobs = [
            fixture_job().model_copy(
                update={"listing_id": str(i), "job_url": f"https://example.com/{i}"}
            )
            for i in range(3)
        ]
        calls = []

        def enrich(job, **kwargs):
            calls.append(job.listing_id)
            if len(calls) == 2:
                entered.set()
                release.wait(5)
            job.description = "This role is eligible for sponsorship."
            job.description_checked_at = "2026-09-09"
            job.description_source = job.job_url
            return job

        with (
            tempfile.TemporaryDirectory() as output,
            patch("jobtailor.workspace.search_jobs", return_value=(jobs, [])),
            patch("jobtailor.workspace.enrich_job_description", side_effect=enrich),
        ):
            task = SearchTask(payload(output, INCLUDE_H1B))
            try:
                task.start()
                self.assertTrue(entered.wait(2))
                task.cancel()
                rows = task.snapshot()["workspace"]["roles"]
                self.assertEqual(sum(row["included"] for row in rows), 1)
                self.assertEqual(
                    rows[0]["sponsorship"], "Conditional; confirm with employer"
                )
                self.assertEqual(
                    rows[0]["sponsorship_evidence"],
                    ["This role is eligible for sponsorship."],
                )
            finally:
                release.set()
                task.wait()
            self.assertEqual(len(calls), 2)

    def test_cancel_before_start_never_searches(self):
        discoverer = Mock()
        task = SearchTask(payload("unused"), discoverer=discoverer)
        self.assertTrue(task.cancel())
        task.start()
        discoverer.assert_not_called()
        self.assertFalse(task.busy)

    def test_failure_preserves_partial_snapshot_and_releases_busy(self):
        def discover(*args, **kwargs):
            kwargs["partial"](workspace_fixture("unused"))
            raise OSError("Search service unavailable")

        task = SearchTask(payload("unused"), discoverer=discover)
        task.start()
        task.wait()
        result = task.snapshot()
        self.assertFalse(result["busy"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(len(result["workspace"]["roles"]), 1)
        result["workspace"]["roles"].clear()
        self.assertEqual(len(task.snapshot()["workspace"]["roles"]), 1)


class SponsorshipPageTests(unittest.TestCase):
    def test_footer_denial_is_retained_even_outside_full_jd_node(self):
        job = fixture_job()
        html = (
            f"<h1>{job.title}</h1><div id='job-description'>"
            + "Manage inventory planning. " * 300
            + "We offer sponsorship.</div><footer><p>Sponsorship is not provided for this position.</p></footer>"
        )
        with patch(
            "jobtailor.job_details.read_public", return_value=(html, job.job_url)
        ):
            refresh_description(job)
        screen_sponsorship(job)
        self.assertIn("Sponsorship is not provided", job.description)
        self.assertEqual(job.sponsorship_status, "Conflicting statements; review")

    def test_other_role_cards_do_not_supply_sponsorship_evidence(self):
        job = fixture_job()
        html = (
            f"<h1>{job.title}</h1><div id='job-description'>Manage inventory.</div>"
            "<aside><p>We offer sponsorship.</p></aside>"
            "<div class='related-jobs'><p>This role is eligible for sponsorship.</p></div>"
        )
        with patch(
            "jobtailor.job_details.read_public", return_value=(html, job.job_url)
        ):
            refresh_description(job)
        screen_sponsorship(job)
        self.assertEqual(job.sponsorship_status, "Not mentioned")
