#!/usr/bin/env python3
"""Recover the two evaluator outputs completed while the scheduler was paused."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
WORKSPACES = {
    "AR1-e71385c581b39099831c44e1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-xrwcx5jx/workspace",
    "AR1-1a34f1531898a80039aea069": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-sgt1_55p/workspace",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVAL = load_module("phase3d_paused_eval_recovery", HERE / "phase3d_stage1_ai_evaluation.py")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    stage1 = EVAL.jsonl(EVAL.STAGE1_MANIFEST)
    by_task = {row["task_id"]: row for row in stage1}
    mapping = json.loads((EVAL.RESTRICTED / "stage1_task_mapping.json").read_text(encoding="utf-8"))
    gold = {row["bug_id"]: row for row in EVAL.jsonl(EVAL.FROZEN / "gold_v1.jsonl")}
    records = []
    for anonymous_id, workspace_text in WORKSPACES.items():
        task = mapping[anonymous_id]
        row = by_task[task["task_id"]]
        run_dir = EVAL.OUTPUT_ROOT / "responses" / anonymous_id
        workspace = Path(workspace_text)
        prompt = (workspace / "PROMPT.txt").read_bytes()
        intent = json.loads((run_dir / "attempt_intent.json").read_text(encoding="utf-8"))
        if sha_bytes(prompt) != intent["runtime_prompt_sha256"]:
            raise RuntimeError(f"{anonymous_id}: runtime prompt mismatch")
        raw = (workspace / "answer.json").read_bytes()
        if not raw:
            raise RuntimeError(f"{anonymous_id}: recovered evaluator output is empty")
        model_dir = run_dir / "model_run"
        EVAL.RUNNER.atomic_write(model_dir / "raw_response.json", raw)
        EVAL.RUNNER.atomic_write(model_dir / "events.jsonl", b"")
        EVAL.RUNNER.atomic_write(model_dir / "stderr.txt", b"")
        metadata = {
            "cli_version": EVAL.EXPECTED_CLI,
            "model_identifier_requested": EVAL.MODEL,
            "model_version": "unavailable",
            "reasoning_effort": EVAL.REASONING,
            "start_timestamp": "unavailable_recovered_after_pause",
            "completion_timestamp": "unavailable_recovered_after_pause",
            "returncode": 0,
            "timed_out": False,
            "prompt_sha256": intent["runtime_prompt_sha256"],
            "schema_sha256": intent["schema_sha256"],
            "schema_enforced_by_cli": False,
            "response_sha256": sha_bytes(raw),
            "recovery_provenance": "PAUSED_DISPOSABLE_EVALUATOR_WORKSPACE",
            "metadata_complete": False,
            "original_events_unavailable": True,
        }
        EVAL.write_json(model_dir / "metadata.json", metadata)
        result = EVAL.finalize(row, anonymous_id, run_dir, gold[row["bug_id"]])
        result["recovery_provenance"] = metadata["recovery_provenance"]
        result["metadata_complete"] = False
        # Preserve finalize's immutable summary and add a separate recovery overlay.
        EVAL.write_json(run_dir / "recovery_overlay.json", result)
        records.append({"anonymous_response_id": anonymous_id, "status": result["status"], "response_sha256": sha_bytes(raw)})
    report = {"status": "PAUSED_IN_FLIGHT_EVALUATIONS_RECOVERED", "records": records, "new_model_calls": 0}
    EVAL.write_json(EVAL.OUTPUT_ROOT / "paused_inflight_recovery_report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
