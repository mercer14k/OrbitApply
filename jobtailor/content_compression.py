"""Built-in, conservative content-compression adapter for Ollama requests.

This is an original implementation of the project's compression principles,
not an installation of an external plugin. Source messages are never rewritten.
Only redundant Pydantic-generated schema titles are omitted. All constraints,
field names, descriptions, defaults, enum values and examples are preserved.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from threading import Lock
from time import perf_counter


def compact_json(value: object) -> str:
    """Serialize app-owned structured data, preserving every value and field."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def compact_schema(schema: dict, model_name: str = "") -> dict:
    """Walk schema positions only, never dictionaries holding data/defaults."""
    result = deepcopy(schema)

    def walk(node, expected_title=""):
        if not isinstance(node, dict):
            return
        if expected_title and node.get("title") == expected_title:
            del node["title"]
        for key, child in node.get("properties", {}).items():
            walk(child, key.replace("_", " ").title())
        for key, child in node.get("$defs", {}).items():
            walk(child, key)
        for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else"):
            walk(node.get(key))
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for child in node.get(key, []):
                walk(child)

    walk(result, model_name)
    return result


@dataclass(frozen=True)
class PreparedRequest:
    schema: dict
    before_characters: int
    after_characters: int
    before_estimated_tokens: int
    after_estimated_tokens: int
    overhead_ms: float
    reason: str


class AgentContentCompressor:
    """Per-client plugin boundary with an unchanged-input fallback and ledger."""
    name = "orbitapply-conservative-compression-v1"

    def __init__(self, enabled: bool | None = None):
        self.enabled = (os.getenv("ORBITAPPLY_COMPRESSION", "1").lower()
                        not in {"0", "false", "off"}) if enabled is None else enabled
        self._lock = Lock()
        self._requests = self._changed = 0
        self._before = self._after = self._estimated_saved = 0
        self._overhead_ms = self._wall_seconds = 0.0
        self._responses = self._failures = 0
        self._values = {key: 0 for key in (
            "prompt_eval_count", "eval_count", "total_duration", "load_duration",
            "prompt_eval_duration", "eval_duration",
        )}
        self._coverage = dict.fromkeys(self._values, 0)
        self._bypasses: dict[str, int] = {}

    def prepare(self, system: str, user: str, schema: dict, model_name: str) -> PreparedRequest:
        started = perf_counter()
        original = compact_json(schema)
        result, reason = schema, "disabled"
        if self.enabled:
            try:
                candidate = compact_schema(schema, model_name)
                if len(compact_json(candidate)) < len(original):
                    result, reason = candidate, "redundant_schema_titles"
                else:
                    reason = "already_compact"
            except Exception:  # Optional optimization cannot block a valid request.
                result, reason = schema, "optimization_failed"
        before = len(system) + len(user) + len(original)
        after = len(system) + len(user) + len(compact_json(result))
        return PreparedRequest(result, before, after, (before + 2) // 3,
                               (after + 2) // 3, (perf_counter() - started) * 1000, reason)

    def record_attempt(self, prepared: PreparedRequest):
        # Count actual attempts, including validation retries, never cache hits.
        with self._lock:
            self._requests += 1
            self._changed += prepared.reason == "redundant_schema_titles"
            self._before += prepared.before_characters
            self._after += prepared.after_characters
            self._estimated_saved += prepared.before_estimated_tokens - prepared.after_estimated_tokens
            self._overhead_ms += prepared.overhead_ms
            if prepared.reason != "redundant_schema_titles":
                self._bypasses[prepared.reason] = self._bypasses.get(prepared.reason, 0) + 1

    def record_response(self, result: dict | None, elapsed: float):
        with self._lock:
            self._wall_seconds += elapsed
            if result is None:
                self._failures += 1
                return
            self._responses += 1
            for key in self._values:
                value = result.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self._values[key] += value
                    self._coverage[key] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "implementation": self.name,
                "external_plugin_integrated": False,
                "enabled": self.enabled,
                "scope": "Current AI client; attempts include invalid/truncated responses and retries. Cache hits excluded.",
                "source_text_unchanged": True,
                "attempts": self._requests,
                "optimized_attempts": self._changed,
                "bypasses": dict(self._bypasses),
                "payload": {
                    "before_characters": self._before,
                    "after_characters": self._after,
                    "characters_removed": self._before - self._after,
                    "estimated_input_tokens_removed": self._estimated_saved,
                    "basis": "Character/3 estimate for messages plus compact schema; not tokenizer counts or measured savings.",
                },
                "observed_ollama": {
                    key: {"total": self._values[key] if self._coverage[key] else None,
                          "responses_with_metric": self._coverage[key]}
                    for key in self._values
                },
                "responses_received": self._responses,
                "transport_failures": self._failures,
                "compression_overhead_ms": round(self._overhead_ms, 3),
                "request_wall_seconds": round(self._wall_seconds, 3),
                "measured_token_savings": None,
                "measured_speedup": None,
                "comparison": "No paired live baseline. Shorter schemas do not prove faster or better model output.",
                "privacy": "No prompt text, resume data, or credentials in this ledger; memory-only until saved with a job.",
            }
