#!/usr/bin/env python3
"""Materialize the truthful Phase 3D blocked checkpoint from smoke attempts."""

from __future__ import annotations

import csv
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
SMOKE = HERE / "generation_outputs/SMOKE-P3-004-A-R1"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    attempts = [(1, SMOKE), (2, SMOKE / "attempt-2"), (3, SMOKE / "attempt-3")]
    manifest = []
    failures = []
    for index, directory in attempts:
        meta = load(directory / "metadata.json")
        validation = load(directory / "validation.json")
        row = {
            "record_type": "SMOKE_NOT_FORMAL",
            "smoke_id": "SMOKE-P3-004-A-R1",
            "source_task_id": "P3-004-A-R1",
            "attempt_index": index,
            "condition": "A",
            "repeat_index": 1,
            "bug_id": "flame-engine/flame#1003",
            "model_identifier_requested": meta["model_identifier_requested"],
            "model_version": meta["model_version"],
            "start_timestamp": meta["start_timestamp"],
            "completion_timestamp": meta["completion_timestamp"],
            "prompt_sha256": meta["prompt_sha256"],
            "schema_sha256": meta["schema_sha256"],
            "returncode": meta["returncode"],
            "generation_status": validation["status"],
            "response_sha256": meta["response_sha256"],
            "formal_observation": False,
        }
        manifest.append(row)
        stderr = (directory / "stderr.txt").read_text(encoding="utf-8")
        events = (directory / "events.jsonl").read_text(encoding="utf-8")
        category = "LOCAL_SANDBOX_APP_SERVER_INITIALIZATION" if index == 1 else "CLI_STRICT_SCHEMA_INCOMPATIBILITY"
        if validation["status"] == "INFRASTRUCTURE_FAILURE":
            failures.append({**row, "failure_category": category, "error_excerpt": (stderr or events)[-2000:]})
    (HERE / "execution_manifest.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in manifest))
    (HERE / "generation_failures.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in failures))
    eval_meta = load(HERE / "ai_evaluation_outputs/SMOKE-P3-004-A-R1/generation-attempt-3/model_run/metadata.json")
    eval_parse = load(HERE / "ai_evaluation_outputs/SMOKE-P3-004-A-R1/generation-attempt-3/assessment_parse_validation.json")
    eval_failure = {
        "record_type": "SMOKE_NOT_FORMAL", "smoke_id": "SMOKE-P3-004-A-R1",
        "anonymous_response_id": "AR-SMOKE-001", "model_identifier_requested": eval_meta["model_identifier_requested"],
        "model_version": eval_meta["model_version"], "prompt_sha256": eval_meta["prompt_sha256"],
        "schema_sha256": eval_meta["schema_sha256"], "response_sha256": eval_meta["response_sha256"],
        "returncode": eval_meta["returncode"], "status": "AI_EVALUATION_SCHEMA_FAILURE",
        "validation_errors": ["assessment top-level keys violate schema"],
        "raw_response_preserved": True, "formal_observation": False,
    }
    (HERE / "evaluation_failures.jsonl").write_text(json.dumps(eval_failure, sort_keys=True) + "\n")
    (HERE / "provisional_scores.csv").write_text("anonymous_response_id,bug_id,condition,repeat_index,metric,value,verification_status\n")
    stats = {
        "status": "NOT_RUN_NO_VALID_AI_EVALUATIONS",
        "primary_analysis_locked": False,
        "arms_unblinded": False,
        "formal_observations": 0,
        "provisional_observations": 0,
        "reason": "Smoke generation passed, but smoke AI evaluation failed its output schema; no scores exist."
    }
    (HERE / "statistical_results.json").write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    table = HERE / "tables/table1_execution_summary.csv"
    table.parent.mkdir(parents=True, exist_ok=True)
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["scope", "planned", "attempted", "valid", "failed", "skipped", "validity_rate"])
        writer.writeheader()
        writer.writerow({"scope": "smoke_generation_not_formal", "planned": 1, "attempted": 3, "valid": 1, "failed": 2, "skipped": 0, "validity_rate": "1/3 attempts; final authorized attempt valid"})
        writer.writerow({"scope": "smoke_ai_evaluation_not_formal", "planned": 1, "attempted": 1, "valid": 0, "failed": 1, "skipped": 0, "validity_rate": 0})
        writer.writerow({"scope": "formal", "planned": 162, "attempted": 0, "valid": 0, "failed": 0, "skipped": 162, "validity_rate": "NA"})
    print(json.dumps({"status": "PHASE3D_EXECUTION_FAILED", "generation_smoke_attempts": 3, "valid_generation_outputs": 1, "ai_evaluation_attempts": 1, "valid_ai_evaluations": 0}))


if __name__ == "__main__":
    main()
