# Compression behavior and boundaries

The internal adapter is `jobtailor.content_compression.AgentContentCompressor`.
It requires no extra package, GPU, cloud service, agent framework or LLM.
It runs for the local Ollama client's structured requests: resume parsing, role
suggestions, requirement extraction, evidence matching, rewriting and auditing.

## What changes

The adapter deep-copies the Pydantic format schema and removes `title` annotations
only when they match a generated root/model/property name. It traverses schema
positions, never arbitrary data dictionaries. A candidate field literally named
`title`, defaults, examples, numeric constraints, descriptions, enums, references
and required fields all remain. Pydantic validates output against the original
model. This does not establish identical live-model behavior; annotations can
influence generation even when validation rules are unchanged.

App-owned JSON used in prompts is serialized without decorative whitespace.
Source string values remain exact. Existing prompt builders still choose their
bounded source fragments as before. Raw resume/JD text is not summarized,
truncated, token-pruned or sent to a second model for compression.

No output limits or factual-audit checks have been relaxed. An optimization
exception reuses the original schema; a context-limit or validation failure does
not bypass its gate. `ORBITAPPLY_COMPRESSION=0` disables schema optimization.
The constructor's `compression_enabled` argument supports test/SDK overrides.

## Accounting

Each job has its own client ledger. Every attempted model call counts, including
retries and failed results. Cache hits are counted by the existing context ledger
and excluded from actual inference totals. The cache's old estimate now excludes
reserved output capacity and safety margin, since neither is consumed input.

`AI_Usage.json` includes:

- Before/after message-plus-schema character counts and a character/3 estimate.
- Ollama-reported prompt and generated-token totals when provided, including
  invalid/truncated responses. Each metric includes its response coverage count.
- Compression overhead, request wall time, transport failures and bypass reasons.
- Null measured savings/speedup fields until a paired live baseline is available.

Characters and token estimates are not exact tokenizer counts. Provider template
overhead is not included in the estimate. Ollama can serialize format schemas
differently internally; only paired inference can establish actual savings.
Unavailable usage is null, not zero. Totals with partial coverage are incomplete.
The panel exposes a summary; JSON contains coverage details. Compression overhead
covers request preparation for attempted calls, not cache lookup or whole app time.

The ledger contains no prompt, resume or credential text. It is memory-only until
saved in the user's job folder. Reports are excluded from version control.

## Reproducible offline measurement

Run `python benchmarks/measure_payload.py`. The baseline uses the same messages
and full schema already serialized compactly. Four synthetic prompt/schema
fixtures are prepared 200 times each; median CPU compaction time is reported.
The JSON-formatting illustration is separate and is not counted as model savings.
See `benchmarks/RESULTS.json` for the result captured for this release.

For a live comparison use the same model digest, context, sources and options,
disable response caching, warm the model equally, and alternate enabled/disabled
runs. Compare total input/output tokens, retries, wall time and human-reviewed
claim fidelity across multiple paired trials. Do not interpret an optimized-only
run as measured savings. No such live benchmark was run in this environment.

## External plugin status

The requested plugin could not be located among available tools or project files.
An earlier design specification was available and informed these safeguards.
This implementation is not the complete Agent Context Engine described in that
specification: it has no MCP server, command registry, retrieval store or proxy.
To integrate a specific engine, provide its repository/package and version, inspect
its license/API, then adapt this boundary with the existing fallback and tests.
Do not automatically install an unverified similarly named package.

References: [Ollama chat API](https://docs.ollama.com/api/chat),
[JSON Schema annotations](https://json-schema.org/understanding-json-schema/reference/annotations).
