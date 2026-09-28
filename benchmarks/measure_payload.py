"""Offline payload comparison. Run from repository root; makes no model calls."""
from pathlib import Path
import json
import platform
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobtailor.content_compression import AgentContentCompressor, compact_json
from jobtailor.evidence_tailoring import EvidenceDecision, ClaimDraft, ClaimReview, MATCH_SYSTEM, REWRITE_SYSTEM, REVIEW_SYSTEM
from jobtailor.models import CandidateProfile
from jobtailor.requirements_analysis import RequirementBatch, REQUIREMENT_SYSTEM

source = ("SOURCE: Improved planning accuracy by 12% using existing SQL reports.\n"
          "REQUIREMENT: Forecast inventory requirements. No visa sponsorship.\n")
fixtures = [(EvidenceDecision, MATCH_SYSTEM), (ClaimDraft, REWRITE_SYSTEM),
            (ClaimReview, REVIEW_SYSTEM), (RequirementBatch, REQUIREMENT_SYSTEM)]
rows = []
for model, system in fixtures:
    engine = AgentContentCompressor(True)
    samples = [engine.prepare(system, source, model.model_json_schema(), model.__name__) for _ in range(200)]
    result = samples[-1]
    rows.append({
        "schema": model.__name__,
        "before_payload_characters": result.before_characters,
        "after_payload_characters": result.after_characters,
        "payload_character_reduction_pct": round(100 * (1 - result.after_characters / result.before_characters), 2),
        "estimated_input_token_reduction": result.before_estimated_tokens - result.after_estimated_tokens,
        "median_compaction_ms": round(statistics.median(s.overhead_ms for s in samples), 4),
    })
profile = CandidateProfile.model_json_schema()
pretty = json.dumps(profile, indent=2, ensure_ascii=False)
compact = compact_json(profile)
print(json.dumps({
    "python": platform.python_version(), "platform": platform.system(),
    "samples_per_schema": 200,
    "baseline": "Same messages and full schema, already compact JSON. No artificially padded baseline.",
    "protected_source_bytes_unchanged": True,
    "schema_results": rows,
    "format_only_example": {"pretty_json_chars": len(pretty), "compact_json_chars": len(compact),
                            "round_trip_equal": json.loads(pretty) == json.loads(compact)},
    "model_calls": 0, "measured_token_savings": None, "measured_inference_speedup": None,
    "limitations": "Synthetic payload fixtures, not paired model runs. Character estimates exclude provider framing. No inference quality or hardware throughput claim."
}, indent=2))
