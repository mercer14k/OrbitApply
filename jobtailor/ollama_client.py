from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .context_headroom import ContextHeadroom, estimate_prompt_budget
from .content_compression import AgentContentCompressor, compact_json

T = TypeVar("T", bound=BaseModel)


class OllamaError(RuntimeError):
    pass


class OllamaOutputLimitError(OllamaError):
    """A response was truncated and must not be used as completed output."""


class OllamaClient:
    def __init__(
        self,
        model: str = "qwen3:8b",
        base_url: str = "http://127.0.0.1:11434",
        context_window: int = 8192,
        timeout_seconds: int = 600,
        context_cache_entries: int = 128,
        compression_enabled: bool | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.context_window = context_window
        self.timeout_seconds = timeout_seconds
        self._context_headroom = ContextHeadroom(context_cache_entries)
        self._compressor = AgentContentCompressor(compression_enabled)

    def context_headroom_report(self) -> dict:
        """Return a JSON-safe audit of this client's bounded local AI calls."""
        report = self._context_headroom.snapshot()
        report["compression"] = self._compressor.snapshot()
        return report

    def record_exact_deduplication(self, result) -> None:
        self._context_headroom.record_exact_deduplication(result)

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(
                f"Ollama returned HTTP {exc.code}: {details[:500]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OllamaError(
                "Could not connect to Ollama. Start Ollama and run: ollama pull "
                + self.model
            ) from exc

    def available_models(self) -> list[str]:
        payload = self._request("/api/tags")
        return [
            item.get("name", "")
            for item in payload.get("models", [])
            if item.get("name")
        ]

    def is_ready(self) -> tuple[bool, str]:
        try:
            models = self.available_models()
        except OllamaError as exc:
            return False, str(exc)
        acceptable_names = {self.model}
        if ":" not in self.model:
            acceptable_names.add(f"{self.model}:latest")
        if not acceptable_names.intersection(models):
            return (
                False,
                f"Ollama is running, but {self.model!r} is not installed. Run: ollama pull {self.model}",
            )
        return True, f"Connected to Ollama with {self.model}."

    @staticmethod
    def _extract_json(value: str) -> str:
        value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL).strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value)
            value = re.sub(r"\s*```$", "", value)
        first = value.find("{")
        last = value.rfind("}")
        return value[first : last + 1] if first >= 0 and last > first else value

    def chat_json(
        self,
        response_model: type[T],
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.15,
        retries: int = 2,
        max_output_tokens: int = 2048,
    ) -> T:
        schema = response_model.model_json_schema()
        # Cache identity uses the full source schema; optimization cannot merge
        # distinct jobs or source facts. Budget against the transmitted schema.
        prepared = self._compressor.prepare(system_prompt, user_prompt + "\n/no_think", schema, response_model.__name__)
        schema_json = compact_json(prepared.schema)
        initial_budget = estimate_prompt_budget(
            system_prompt,
            user_prompt,
            schema_json,
            context_window=self.context_window,
            max_output_tokens=max_output_tokens,
            correction="\n/no_think",
        )
        cacheable = temperature == 0.0 and self._context_headroom.cache_entries > 0
        self._context_headroom.begin_call(initial_budget, cacheable=cacheable)
        cache_key = None
        if cacheable:
            cache_key = self._context_headroom.cache_key(
                model=self.model,
                response_type=f"{response_model.__module__}.{response_model.__qualname__}",
                schema=schema,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                context_window=self.context_window,
            )
            cached = self._context_headroom.cache_get(cache_key, initial_budget)
            if cached is not None:
                return response_model.model_validate(cached)
        validation_error = ""
        for attempt in range(retries + 1):
            correction = ""
            if validation_error:
                correction = (
                    "\n\nYour previous response failed schema validation. Return only corrected JSON. "
                    f"Validation error: {validation_error[:1000]}"
                )
            if attempt:
                prepared = self._compressor.prepare(
                    system_prompt, user_prompt + correction + "\n/no_think",
                    schema, response_model.__name__,
                )
            schema_json = compact_json(prepared.schema)
            payload = {
                "model": self.model,
                "stream": False,
                "think": False,
                "format": prepared.schema,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_prompt + correction + "\n/no_think",
                    },
                ],
                "options": {
                    "temperature": temperature,
                    "num_ctx": self.context_window,
                    "num_predict": max_output_tokens,
                },
            }
            # Conservative guard, not a tokenizer. Refuse oversize inputs rather
            # than allow Ollama to silently drop earlier source text.
            budget = estimate_prompt_budget(
                system_prompt,
                user_prompt,
                schema_json,
                context_window=self.context_window,
                max_output_tokens=max_output_tokens,
                correction=correction + "\n/no_think",
            )
            if not budget.fits:
                self._context_headroom.record_oversize_block()
                raise OllamaError(
                    "This AI request is too large for the selected context window. Increase it under More options "
                    "or use a smaller source fragment. No source text was silently discarded."
                )
            self._context_headroom.record_model_request(budget)
            self._compressor.record_attempt(prepared)
            started = time.perf_counter()
            try:
                result = self._request("/api/chat", payload)
            except Exception:
                self._compressor.record_response(None, time.perf_counter() - started)
                raise
            self._compressor.record_response(result, time.perf_counter() - started)
            if result.get("done_reason") == "length":
                self._context_headroom.record_output_limit_stop()
                raise OllamaOutputLimitError(
                    "The model reached its output limit. No partial result was accepted. "
                    "Use a smaller input section or a model with reliable structured output."
                )
            content = result.get("message", {}).get("content", "")
            try:
                parsed = response_model.model_validate_json(self._extract_json(content))
                self._context_headroom.record_success()
                if cache_key is not None:
                    self._context_headroom.cache_put(
                        cache_key, parsed.model_dump(mode="json")
                    )
                return parsed
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                self._context_headroom.record_invalid_response()
                validation_error = str(exc)
                if attempt < retries:
                    time.sleep(0.25)
        raise OllamaError(
            f"The model did not return valid structured data: {validation_error[:1200]}"
        )
