import unittest
from unittest.mock import Mock

from test_core import fixture_profile

from jobtailor.ai_tasks import RoleTitleBatch, SmallRoleTitleBatch, suggest_keywords
from jobtailor.ollama_client import OllamaClient, OllamaOutputLimitError


class RoleExpansionTests(unittest.TestCase):
    def test_output_limit_retries_smaller_schema_and_keeps_source_skills(self):
        client = Mock()
        client.chat_json.side_effect = [
            OllamaOutputLimitError("limited"),
            SmallRoleTitleBatch(titles=["Inventory Analyst"]),
        ]
        profile = fixture_profile()
        result = suggest_keywords(client, profile)
        self.assertFalse(result.warnings)
        self.assertEqual(client.chat_json.call_args_list[0].args[0], RoleTitleBatch)
        self.assertEqual(
            client.chat_json.call_args_list[1].args[0], SmallRoleTitleBatch
        )
        self.assertIn("Inventory Analyst", [k.keyword for k in result.keywords])
        self.assertTrue(set(profile.skills) <= {k.keyword for k in result.keywords})

    def test_long_profile_is_batched_and_one_failure_keeps_other_results(self):
        client = Mock()
        calls = []

        def answer(schema, system, prompt, **kwargs):
            calls.append(prompt)
            if len(calls) <= 2:
                raise OllamaOutputLimitError("limited")
            return schema(titles=["Procurement Analyst"])

        client.chat_json.side_effect = answer
        profile = fixture_profile()
        profile.skills = [f"Test skill {i}" for i in range(300)]
        result = suggest_keywords(client, profile)
        self.assertGreater(len(calls), 3)
        self.assertTrue(all(len(p) < 1900 for p in calls))
        self.assertEqual(len(result.warnings), 1)
        self.assertTrue(set(profile.skills) <= {k.keyword for k in result.keywords})
        self.assertIn("Procurement Analyst", [k.keyword for k in result.keywords])

    def test_truncated_json_is_rejected_even_if_parseable(self):
        client = OllamaClient()
        client._request = Mock(
            return_value={
                "done_reason": "length",
                "message": {"content": '{"titles": ["Analyst"]}'},
            }
        )
        with self.assertRaises(OllamaOutputLimitError):
            client.chat_json(RoleTitleBatch, "titles", "source")
