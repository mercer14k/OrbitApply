from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import BaseModel
from test_core import fixture_job, fixture_profile

from jobtailor.automatic import prepare_job
from jobtailor.context_headroom import (
    deduplicate_exact_fragments,
    estimate_prompt_budget,
)
from jobtailor.evidence_tailoring import READY
from jobtailor.models import DraftValidation, TailoredResume
from jobtailor.ollama_client import OllamaClient, OllamaError
from jobtailor.requirements_analysis import (
    Requirement,
    RequirementAnalysis,
    RequirementBatch,
    analyze_requirements,
)


class SmallAnswer(BaseModel):
    value: str


class ContextHeadroomTests(unittest.TestCase):
    def test_budget_reserves_output_and_never_silently_truncates(self):
        budget = estimate_prompt_budget(
            "system",
            "source" * 300,
            "{}",
            context_window=1024,
            max_output_tokens=256,
        )
        self.assertEqual(
            budget.reserved_total_tokens,
            budget.estimated_input_tokens + 256 + budget.reserve_tokens,
        )
        self.assertFalse(budget.fits)

        client = OllamaClient(context_window=1024)
        client._request = Mock()
        with self.assertRaisesRegex(OllamaError, "too large"):
            client.chat_json(SmallAnswer, "system", "source" * 300)
        client._request.assert_not_called()
        self.assertEqual(
            client.context_headroom_report()["oversize_requests_blocked"], 1
        )

    def test_only_normalized_exact_fragments_are_removed(self):
        result = deduplicate_exact_fragments(
            [
                "Requires SQL and Power BI.\n",
                "  requires   sql and power bi. ",
                "Requires SQL and Power BI experience.",
            ]
        )
        self.assertEqual(result.original_fragments, 3)
        self.assertEqual(result.duplicate_fragments_removed, 1)
        self.assertEqual(
            result.fragments,
            [
                "Requires SQL and Power BI.\n",
                "Requires SQL and Power BI experience.",
            ],
        )

    def test_identical_deterministic_call_is_reused_only_in_memory(self):
        client = OllamaClient(context_window=4096)
        client._request = Mock(
            return_value={
                "done_reason": "stop",
                "message": {"content": json.dumps({"value": "kept exactly"})},
            }
        )
        first = client.chat_json(
            SmallAnswer, "system", "protected source", temperature=0.0
        )
        second = client.chat_json(
            SmallAnswer, "system", "protected source", temperature=0.0
        )
        self.assertEqual(first, second)
        self.assertEqual(client._request.call_count, 1)
        report = client.context_headroom_report()
        self.assertEqual(report["logical_calls"], 2)
        self.assertEqual(report["model_requests"], 1)
        self.assertEqual(report["cache"]["hits"], 1)
        self.assertGreater(report["cache"]["estimated_tokens_avoided"], 0)
        self.assertFalse(
            report["source_protection"]["resume_or_jd_semantic_compression"]
        )

    def test_non_deterministic_calls_are_not_cached(self):
        client = OllamaClient(context_window=4096)
        client._request = Mock(
            return_value={
                "done_reason": "stop",
                "message": {"content": json.dumps({"value": "answer"})},
            }
        )
        for _ in range(2):
            client.chat_json(
                SmallAnswer, "system", "source", temperature=0.1
            )
        self.assertEqual(client._request.call_count, 2)
        self.assertEqual(client.context_headroom_report()["cache"]["hits"], 0)

    def test_full_jd_coverage_is_kept_while_identical_call_is_reused(self):
        fragment = "Use SQL to automate reporting. " + "Operational detail. " * 100
        job = fixture_job().model_copy(
            update={"description": fragment + "\n" + fragment + "\n"}
        )
        client = OllamaClient(context_window=4096)
        client._request = Mock(
            return_value={
                "done_reason": "stop",
                "message": {
                    "content": RequirementBatch(
                        requirements=[
                            Requirement(
                                kind="responsibility",
                                quote="Use SQL to automate reporting.",
                            )
                        ]
                    ).model_dump_json()
                },
            }
        )
        analysis = analyze_requirements(client, job, fixture_profile())
        self.assertEqual(analysis.chunks_generated, 2)
        self.assertEqual(analysis.chunks_reviewed, 2)
        self.assertEqual(analysis.duplicate_chunks_skipped, 0)
        self.assertEqual(client._request.call_count, 1)
        self.assertEqual(client.context_headroom_report()["cache"]["hits"], 1)

    def test_every_job_folder_gets_a_context_protection_audit(self):
        profile, job = fixture_profile(), fixture_job()
        job.description_source = job.job_url
        draft = TailoredResume(
            target_title=job.title,
            professional_summary=profile.professional_summary,
            tailoring_report={"status": READY, "issues": []},
        )
        client = OllamaClient(context_window=4096)
        with (
            tempfile.TemporaryDirectory() as output,
            patch("jobtailor.automatic.enrich_job_description", return_value=job),
            patch(
                "jobtailor.automatic.analyze_requirements",
                return_value=RequirementAnalysis(
                    requirements=[
                        Requirement(
                            kind="responsibility",
                            quote="Use SQL to automate reporting.",
                        )
                    ]
                ),
            ),
            patch("jobtailor.automatic.tailor_resume", return_value=draft),
            patch(
                "jobtailor.automatic.audit_and_repair_draft",
                return_value=(draft, DraftValidation(passed=True)),
            ),
        ):
            row = prepare_job(output, profile, job, [], client, use_browser=False)
            folder = Path(row["folder"])
            report = json.loads(
                (folder / "Context_Headroom.json").read_text(encoding="utf-8")
            )
            tailoring = json.loads(
                (folder / "Tailoring_Report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["strategy"], "source_safe_context_headroom_v1")
            usage = json.loads((folder / "AI_Usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage, row["ai_usage"])
            self.assertTrue(usage["source_text_unchanged"])
            self.assertIsNone(usage["measured_speedup"])
            self.assertIn("context_headroom", tailoring)
            self.assertIn(
                "Context protection", (folder / "Tailoring_Report.html").read_text()
            )


if __name__ == "__main__":
    unittest.main()
