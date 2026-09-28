"""Source-safe context budgeting inspired by Headroom's core principle.

Resume and job-description text is evidence. It must not be summarized or
semantically compressed before the model sees the relevant bounded fragment.
This module therefore limits optimization to measurable prompt budgets, exact
duplicate-fragment removal, and an in-memory cache for identical deterministic
requests. Any optional optimization failure leaves the original input intact.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any

CONTEXT_STRATEGY = "source_safe_context_headroom_v1"
DEFAULT_RESERVE_TOKENS = 256


@dataclass(frozen=True)
class PromptBudget:
    prompt_characters: int
    estimated_input_tokens: int
    max_output_tokens: int
    reserve_tokens: int
    context_window: int
    reserved_total_tokens: int
    remaining_tokens: int
    utilization_percent: float
    fits: bool

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExactDedupeResult:
    fragments: list[str]
    original_fragments: int
    duplicate_fragments_removed: int
    duplicate_characters_avoided: int


def estimate_prompt_budget(
    system_prompt: str,
    user_prompt: str,
    schema_json: str,
    *,
    context_window: int,
    max_output_tokens: int,
    correction: str = "",
    reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
) -> PromptBudget:
    """Use the app's deliberately conservative character-based token estimate."""
    characters = (
        len(system_prompt) + len(user_prompt) + len(correction) + len(schema_json)
    )
    estimated = (characters + 2) // 3
    reserved = estimated + max_output_tokens + reserve_tokens
    remaining = context_window - reserved
    utilization = (reserved / context_window * 100) if context_window else 100.0
    return PromptBudget(
        prompt_characters=characters,
        estimated_input_tokens=estimated,
        max_output_tokens=max_output_tokens,
        reserve_tokens=reserve_tokens,
        context_window=context_window,
        reserved_total_tokens=reserved,
        remaining_tokens=remaining,
        utilization_percent=round(utilization, 1),
        fits=remaining >= 0,
    )


def _normalized_exact_fragment(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def deduplicate_exact_fragments(values: Iterable[str]) -> ExactDedupeResult:
    """Remove only whole fragments that are identical after text normalization.

    The original fragment is retained verbatim. If normalization ever fails,
    fail open by returning every original fragment in its original order.
    """
    original = list(values)
    try:
        seen: set[str] = set()
        unique: list[str] = []
        avoided = 0
        for value in original:
            key = _normalized_exact_fragment(value)
            if key and key in seen:
                avoided += len(value)
                continue
            seen.add(key)
            unique.append(value)
        return ExactDedupeResult(
            fragments=unique,
            original_fragments=len(original),
            duplicate_fragments_removed=len(original) - len(unique),
            duplicate_characters_avoided=avoided,
        )
    except Exception:  # noqa: BLE001 - optimization must never block source review
        return ExactDedupeResult(
            fragments=original,
            original_fragments=len(original),
            duplicate_fragments_removed=0,
            duplicate_characters_avoided=0,
        )


class ContextHeadroom:
    """Per-client request ledger and bounded deterministic response cache."""

    def __init__(self, cache_entries: int = 128) -> None:
        self.cache_entries = max(0, int(cache_entries))
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = Lock()
        self._logical_calls = 0
        self._model_requests = 0
        self._successful_responses = 0
        self._invalid_responses = 0
        self._oversize_requests_blocked = 0
        self._output_limit_stops = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._estimated_tokens_avoided = 0
        self._largest_estimated_input_tokens = 0
        self._largest_reserved_total_tokens = 0
        self._largest_utilization_percent = 0.0
        self._duplicate_fragments_removed = 0
        self._duplicate_characters_avoided = 0

    @staticmethod
    def cache_key(
        *,
        model: str,
        response_type: str,
        schema: dict,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
        context_window: int,
    ) -> str:
        serialized = json.dumps(
            {
                "model": model,
                "response_type": response_type,
                "schema": schema,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "context_window": context_window,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def begin_call(self, budget: PromptBudget, *, cacheable: bool) -> None:
        with self._lock:
            self._logical_calls += 1
            self._largest_estimated_input_tokens = max(
                self._largest_estimated_input_tokens, budget.estimated_input_tokens
            )
            self._largest_reserved_total_tokens = max(
                self._largest_reserved_total_tokens, budget.reserved_total_tokens
            )
            self._largest_utilization_percent = max(
                self._largest_utilization_percent, budget.utilization_percent
            )
            if cacheable:
                self._cache_misses += 1

    def cache_get(self, key: str, budget: PromptBudget) -> dict[str, Any] | None:
        with self._lock:
            value = self._cache.pop(key, None)
            if value is None:
                return None
            self._cache[key] = value
            self._cache_hits += 1
            self._cache_misses = max(0, self._cache_misses - 1)
            # Output caps and safety reserves are not consumed tokens.
            self._estimated_tokens_avoided += budget.estimated_input_tokens
            return deepcopy(value)

    def cache_put(self, key: str, value: dict[str, Any]) -> None:
        if not self.cache_entries:
            return
        with self._lock:
            self._cache.pop(key, None)
            self._cache[key] = deepcopy(value)
            while len(self._cache) > self.cache_entries:
                self._cache.popitem(last=False)
                self._cache_evictions += 1

    def record_model_request(self, budget: PromptBudget) -> None:
        with self._lock:
            self._model_requests += 1
            self._largest_estimated_input_tokens = max(
                self._largest_estimated_input_tokens, budget.estimated_input_tokens
            )
            self._largest_reserved_total_tokens = max(
                self._largest_reserved_total_tokens, budget.reserved_total_tokens
            )
            self._largest_utilization_percent = max(
                self._largest_utilization_percent, budget.utilization_percent
            )

    def record_success(self) -> None:
        with self._lock:
            self._successful_responses += 1

    def record_invalid_response(self) -> None:
        with self._lock:
            self._invalid_responses += 1

    def record_oversize_block(self) -> None:
        with self._lock:
            self._oversize_requests_blocked += 1

    def record_output_limit_stop(self) -> None:
        with self._lock:
            self._output_limit_stops += 1

    def record_exact_deduplication(self, result: ExactDedupeResult) -> None:
        with self._lock:
            self._duplicate_fragments_removed += result.duplicate_fragments_removed
            self._duplicate_characters_avoided += result.duplicate_characters_avoided

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "strategy": CONTEXT_STRATEGY,
                "source_protection": {
                    "resume_or_jd_semantic_compression": False,
                    "silent_source_truncation": False,
                    "deduplication": "Whole fragments only when text is identical after Unicode/whitespace/case normalization.",
                },
                "scope": "Current local AI client only; cache is memory-only and is not shared across jobs or restarts.",
                "telemetry": "None. No context statistics are sent to an external service.",
                "logical_calls": self._logical_calls,
                "model_requests": self._model_requests,
                "successful_model_responses": self._successful_responses,
                "invalid_model_responses": self._invalid_responses,
                "oversize_requests_blocked": self._oversize_requests_blocked,
                "output_limit_stops": self._output_limit_stops,
                "cache": {
                    "eligible_misses": self._cache_misses,
                    "hits": self._cache_hits,
                    "entries": len(self._cache),
                    "capacity": self.cache_entries,
                    "evictions": self._cache_evictions,
                    "estimated_tokens_avoided": self._estimated_tokens_avoided,
                    "estimate_scope": "Input only, character estimate. Excludes output caps and safety reserves.",
                },
                "context_budget": {
                    "largest_estimated_input_tokens": self._largest_estimated_input_tokens,
                    "largest_reserved_total_tokens": self._largest_reserved_total_tokens,
                    "largest_utilization_percent": round(
                        self._largest_utilization_percent, 1
                    ),
                    "estimator": "Conservative character estimate; not an exact tokenizer count.",
                },
                "exact_deduplication": {
                    "fragments_removed": self._duplicate_fragments_removed,
                    "characters_avoided": self._duplicate_characters_avoided,
                },
            }
