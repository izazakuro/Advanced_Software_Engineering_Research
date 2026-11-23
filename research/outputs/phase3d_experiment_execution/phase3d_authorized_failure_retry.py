#!/usr/bin/env python3
"""Run one explicitly authorized retry for each failed formal generation task."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
from pathlib import Path
import threading


HERE = Path(__file__).resolve().parent
VALID = {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}
_lock = threading.Lock()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORMAL = load_module("phase3d_authorized_retry_formal", HERE / "phase3d_formal_generation.py")


def retry_one(row: dict) -> dict:
    directory = FORMAL.task_dir(row)
    original = json.loads((directory / "task_summary.json").read_text(encoding="utf-8"))
    if original["status"] in VALID:
        return {"task_id": row["task_id"], "status": "NOT_ELIGIBLE_ORIGINAL_VALID"}
    retry_summary_path = directory / "authorized_retry_summary.json"
    if retry_summary_path.exists():
        return json.loads(retry_summary_path.read_text(encoding="utf-8"))
    attempt_dir = directory / "attempt-2-authorized-retry"
    FORMAL.write_new(attempt_dir / "attempt_intent.json", {
        "task_id": row["task_id"], "attempt_index": 2,
        "authorization": "Human message on 2026-11-19: 允许重试一次失败的任务",
        "protocol_deviation": "Completed model-output failures were previously non-retryable; explicit later human authorization permits one retry.",
        "prompt_sha256": row["prompt_sha256"], "schema_sha256": row["generation_schema_sha256"],
        "payload_line_sha256": row["_payload_line_sha256"],
        "model": FORMAL.MODEL, "reasoning_effort": FORMAL.REASONING,
    })
    FORMAL.RUNNER.run_codex_json(
        prompt=row["rendered_prompt"], schema_path=FORMAL.SCHEMA,
        model=FORMAL.MODEL, reasoning=FORMAL.REASONING, run_dir=attempt_dir,
    )
    retry = FORMAL.finalize_attempt(row, attempt_dir, 2)
    result = {
        "task_id": row["task_id"], "bug_id": row["bug_id"], "condition": row["condition"],
        "repeat_index": row["repeat_index"], "authorization_recorded": True,
        "protocol_deviation_recorded": original["failure_class"] == "MODEL_GENERATION_FAILURE",
        "original_status": original["status"], "original_response_sha256": original.get("response_sha256"),
        "retry_status": retry["status"], "retry_response_sha256": retry.get("response_sha256"),
        "retry_attempt_count": 1, "further_retry_allowed": False,
        "effective_status": retry["status"],
        "effective_attempt_index": 2,
        "effective_outcome_policy": "EXPLICITLY_AUTHORIZED_SINGLE_RETRY_AFTER_FAILED_TASK",
        "original_artifacts_preserved": True,
        "retry": retry,
    }
    FORMAL.write_new(retry_summary_path, result)
    with _lock:
        print(json.dumps({"task_id": row["task_id"], "original": original["status"], "retry": retry["status"]}), flush=True)
    return result


def summarize(batch: int, rows: list[dict]) -> dict:
    records = []
    for row in rows:
        directory = FORMAL.task_dir(row)
        original = json.loads((directory / "task_summary.json").read_text(encoding="utf-8"))
        retry_path = directory / "authorized_retry_summary.json"
        retry = json.loads(retry_path.read_text(encoding="utf-8")) if retry_path.exists() else None
        records.append({
            "task_id": row["task_id"], "condition": row["condition"],
            "original_status": original["status"],
            "effective_status": retry["effective_status"] if retry else original["status"],
            "authorized_retry_used": retry is not None,
        })
    effective_valid = Counter(x["condition"] for x in records if x["effective_status"] in VALID)
    effective_failed = Counter(x["condition"] for x in records if x["effective_status"] not in VALID)
    result = {
        "batch": batch, "planned_tasks": len(rows), "original_task_attempts": len(rows),
        "authorized_retry_calls": sum(x["authorized_retry_used"] for x in records),
        "total_model_invocations_or_unknown_started_attempts": len(rows) + sum(x["authorized_retry_used"] for x in records),
        "effective_valid": {"total": sum(effective_valid.values()), "by_condition": {c: effective_valid[c] for c in "ABC"}},
        "effective_failed": {"total": sum(effective_failed.values()), "by_condition": {c: effective_failed[c] for c in "ABC"}},
        "remaining_unattempted_tasks": 0,
        "further_retries_allowed": False,
        "protocol_deviation_disclosed": True,
        "formal_ai_evaluation_run": False,
        "records": records,
    }
    path = FORMAL.OUTPUT_ROOT / f"batch-{batch}" / "batch_summary_after_authorized_retry.json"
    data = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        path.write_bytes(data)
    else:
        FORMAL.RUNNER.atomic_write(path, data)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=int, choices=[1, 2, 3])
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    gate = FORMAL.preflight()
    if gate["status"] != "PASS":
        raise RuntimeError(json.dumps(gate))
    rows = [row for row in FORMAL.load_payloads() if row["repeat_index"] == args.batch]
    failed = []
    for row in rows:
        summary = json.loads((FORMAL.task_dir(row) / "task_summary.json").read_text(encoding="utf-8"))
        if summary["status"] not in VALID:
            failed.append(row)
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix=f"phase3d-retry-b{args.batch}") as pool:
        futures = [pool.submit(retry_one, row) for row in failed]
        for future in as_completed(futures):
            future.result()
    print(json.dumps(summarize(args.batch, rows), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
