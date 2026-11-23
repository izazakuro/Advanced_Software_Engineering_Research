#!/usr/bin/env python3
"""Checkpointed formal Phase 3D generation-only execution."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
from pathlib import Path
import threading
from typing import Any


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
P3C = RESEARCH / "outputs/phase3c_pre_pilot_review"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
PAYLOADS = P3C / "phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl"
SCHEDULE = P3C / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl"
PROMPT_TEMPLATE = P3C / "phase3_generation_prompt_v2_PROPOSED.txt"
SCHEMA = P3C / "phase3_generation_output_v2_PROPOSED.schema.json"
OUTPUT_ROOT = HERE / "formal_generation_outputs"
MODEL = "gpt-6.2-sol"
REASONING = "low"
EXPECTED_CLI = "codex-cli 0.167.0-alpha.4.1"
EXPECTED_HASHES = {
    FROZEN / "gold_v1_freeze_manifest.json": "0886571b6390ab461b1f72ee43ff4628c39cfc76275378207b0546026c51abdc",
    FROZEN / "gold_v1.jsonl": "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66",
    PAYLOADS: "2a15d220ed1ecffe3643c50eda33d2219cde34a1ee0892c35dc4b93b789a1153",
    SCHEDULE: "a3efd162269a1cdd1878460bd6c7376af9dbfff97e781a79ee6d15fbac0e7c98",
    PROMPT_TEMPLATE: "cc1f9373e275543281ccd0b6d9bff7dc2b1d84d855c059867fa0791228598f5c",
    SCHEMA: "e63d1d66cab758e83be279037437e0582e17392ffdb44fc0b317c296c9e5fd8e",
}
_print_lock = threading.Lock()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_module("phase3d_formal_runner_base", HERE / "phase3d_runner.py")
SCHEMA_VALIDATOR = load_module("phase3d_formal_schema_validator", HERE / "phase3d_evaluator_only_retry.py")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_new(path: Path, value: dict) -> None:
    RUNNER.atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def load_payloads() -> list[dict]:
    rows = []
    for index, line in enumerate(PAYLOADS.read_bytes().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        row["_payload_line_sha256"] = sha_bytes(line)
        row["_payload_file_line"] = index
        rows.append(row)
    return rows


def preflight() -> dict:
    actual = {str(path.relative_to(RESEARCH)): sha(path) for path in EXPECTED_HASHES}
    expected = {str(path.relative_to(RESEARCH)): digest for path, digest in EXPECTED_HASHES.items()}
    hash_errors = [key for key in expected if actual.get(key) != expected[key]]
    rows = load_payloads()
    task_ids = [row["task_id"] for row in rows]
    structural_errors = []
    if len(rows) != 162 or len(set(task_ids)) != 162:
        structural_errors.append("formal payloads are not 162 unique tasks")
    schedule_rows = [json.loads(line) for line in SCHEDULE.read_text().splitlines() if line.strip()]
    if [row["task_id"] for row in schedule_rows] != task_ids:
        structural_errors.append("payload task order differs from frozen schedule")
    batch_balance = {}
    for repeat in (1, 2, 3):
        selected = [row for row in rows if row["repeat_index"] == repeat]
        counts = Counter(row["condition"] for row in selected)
        bugs = Counter(row["bug_id"] for row in selected)
        batch_balance[str(repeat)] = {"tasks": len(selected), "conditions": dict(sorted(counts.items())), "bugs": len(bugs)}
        if len(selected) != 54 or counts != Counter({"A": 18, "B": 18, "C": 18}) or len(bugs) != 18 or set(bugs.values()) != {3}:
            structural_errors.append(f"batch {repeat} is not balanced")
    for row in rows:
        if sha_bytes(row["rendered_prompt"].encode()) != row["prompt_sha256"]:
            structural_errors.append(f"{row['task_id']}: rendered prompt hash mismatch")
        if row["generation_schema_sha256"] != expected[str(SCHEMA.relative_to(RESEARCH))]:
            structural_errors.append(f"{row['task_id']}: schema hash mismatch")
    cli = RUNNER.codex_version()
    if cli != EXPECTED_CLI:
        structural_errors.append(f"CLI differs from validated smoke: {cli!r} != {EXPECTED_CLI!r}")
    result = {
        "status": "PASS" if not hash_errors and not structural_errors else "BLOCKED",
        "actual_hashes": actual,
        "expected_hashes": expected,
        "hash_mismatches": hash_errors,
        "structural_errors": structural_errors,
        "records": len(rows),
        "unique_task_ids": len(set(task_ids)),
        "batch_balance": batch_balance,
        "model": MODEL,
        "reasoning_effort": REASONING,
        "cli_version": cli,
        "local_schema_validation": "complete JSON Schema v2 subset plus existing fail-closed protocol validator",
        "cli_output_schema_used": False,
    }
    path = HERE / "formal_generation_preflight.json"
    data = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        path.write_bytes(data)
    else:
        RUNNER.atomic_write(path, data)
    return result


def task_dir(row: dict) -> Path:
    return OUTPUT_ROOT / f"batch-{row['repeat_index']}" / row["task_id"]


def validate_completed_raw(row: dict, raw: bytes) -> tuple[dict, dict]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    parsed_error = None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        parsed = None
        parsed_error = str(exc)
    schema_errors = ([{"json_path": "$", "keyword": "parse", "message": parsed_error}]
                     if parsed_error else SCHEMA_VALIDATOR.validate_schema(parsed, schema, schema))
    schema_result = {
        "status": "SCHEMA_VALID" if not schema_errors else "SCHEMA_INVALID",
        "schema_sha256": sha(SCHEMA),
        "response_sha256": sha_bytes(raw),
        "error_count": len(schema_errors),
        "errors": schema_errors,
    }
    protocol = RUNNER.P3C_EVAL.classify_attempt(
        invocation_started=True,
        invocation_completed=True,
        raw=raw,
        expected_bug_id=row["bug_id"],
        process_error=None,
    )
    if schema_errors and protocol.get("status") in {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}:
        protocol = dict(protocol)
        protocol["status"] = "SCHEMA_FAILURE"
        protocol["failure_class"] = "MODEL_GENERATION_FAILURE"
        protocol["metric_scoring_eligible"] = True
        protocol["retry_eligible"] = False
        protocol["errors"] = [f"{item['json_path']}: {item['message']}" for item in schema_errors]
    return schema_result, protocol


def finalize_attempt(row: dict, attempt_dir: Path, attempt_index: int) -> dict:
    meta = json.loads((attempt_dir / "metadata.json").read_text(encoding="utf-8"))
    raw = (attempt_dir / "raw_response.json").read_bytes()
    if meta["returncode"] != 0:
        status = {
            "status": "INFRASTRUCTURE_FAILURE",
            "failure_class": "INFRASTRUCTURE_FAILURE",
            "retry_eligible": True,
            "process_error": (attempt_dir / "stderr.txt").read_text(encoding="utf-8")[-4000:],
        }
        schema_result = {"status": "NOT_RUN_INVOCATION_FAILED", "errors": []}
    else:
        schema_result, status = validate_completed_raw(row, raw)
    write_new(attempt_dir / "local_schema_validation.json", schema_result)
    write_new(attempt_dir / "protocol_validation.json", status)
    return {
        "task_id": row["task_id"],
        "bug_id": row["bug_id"],
        "condition": row["condition"],
        "repeat_index": row["repeat_index"],
        "attempt_index": attempt_index,
        "status": status["status"],
        "failure_class": status.get("failure_class"),
        "model_identifier_requested": meta["model_identifier_requested"],
        "model_version": meta["model_version"],
        "reasoning_effort": meta["reasoning_effort"],
        "cli_version": meta["cli_version"],
        "prompt_sha256": meta["prompt_sha256"],
        "schema_sha256": meta["schema_sha256"],
        "payload_line_sha256": row["_payload_line_sha256"],
        "payload_file_line": row["_payload_file_line"],
        "context_documents": row["context_documents"],
        "response_sha256": meta["response_sha256"],
        "start_timestamp": meta["start_timestamp"],
        "completion_timestamp": meta["completion_timestamp"],
        "returncode": meta["returncode"],
        "raw_output_path": str((attempt_dir / "raw_response.json").relative_to(RESEARCH)),
        "schema_validation_status": schema_result["status"],
        "retry_eligible": status.get("retry_eligible", False),
        "formal_observation": True,
    }


def existing_or_run(row: dict, allow_infra_retry: bool) -> dict:
    directory = task_dir(row)
    summary_path = directory / "task_summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    directory.mkdir(parents=True, exist_ok=True)
    attempts = []
    for attempt_index in (1, 2):
        attempt_dir = directory / f"attempt-{attempt_index}"
        if (attempt_dir / "metadata.json").exists():
            result = finalize_attempt(row, attempt_dir, attempt_index)
        elif (attempt_dir / "attempt_intent.json").exists():
            result = {
                "task_id": row["task_id"], "bug_id": row["bug_id"], "condition": row["condition"],
                "repeat_index": row["repeat_index"], "attempt_index": attempt_index,
                "status": "INTERRUPTED_UNKNOWN_INVOCATION_STATE", "failure_class": "INFRASTRUCTURE_FAILURE",
                "retry_eligible": False, "formal_observation": True,
            }
        else:
            write_new(attempt_dir / "attempt_intent.json", {
                "task_id": row["task_id"], "payload_line_sha256": row["_payload_line_sha256"],
                "prompt_sha256": row["prompt_sha256"], "schema_sha256": row["generation_schema_sha256"],
                "model": MODEL, "reasoning_effort": REASONING, "attempt_index": attempt_index,
            })
            RUNNER.run_codex_json(
                prompt=row["rendered_prompt"], schema_path=SCHEMA, model=MODEL,
                reasoning=REASONING, run_dir=attempt_dir,
            )
            result = finalize_attempt(row, attempt_dir, attempt_index)
        attempts.append(result)
        if result["status"] != "INFRASTRUCTURE_FAILURE":
            break
        if not allow_infra_retry or attempt_index == 2:
            break
    final = dict(attempts[-1])
    final["attempts"] = attempts
    final["attempt_count"] = len(attempts)
    final["completed_model_output_failure_not_retried"] = (
        final.get("failure_class") == "MODEL_GENERATION_FAILURE" and len(attempts) == 1
    )
    write_new(summary_path, final)
    with _print_lock:
        print(json.dumps({"task_id": row["task_id"], "condition": row["condition"], "status": final["status"], "attempts": len(attempts)}), flush=True)
    return final


def summarize_batch(batch: int, rows: list[dict]) -> dict:
    summaries = []
    for row in rows:
        path = task_dir(row) / "task_summary.json"
        if path.exists():
            summaries.append(json.loads(path.read_text(encoding="utf-8")))
    valid_statuses = {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}
    planned = Counter(row["condition"] for row in rows)
    attempted = Counter(item["condition"] for item in summaries)
    valid = Counter(item["condition"] for item in summaries if item["status"] in valid_statuses)
    failed = Counter(item["condition"] for item in summaries if item["status"] not in valid_statuses)
    result = {
        "batch": batch,
        "repeat_index": batch,
        "status": "COMPLETE" if len(summaries) == 54 else "INCOMPLETE",
        "planned": {"total": len(rows), "by_condition": dict(sorted(planned.items()))},
        "attempted": {"total": len(summaries), "by_condition": dict(sorted(attempted.items()))},
        "valid": {"total": sum(valid.values()), "by_condition": {key: valid[key] for key in "ABC"}},
        "failed": {"total": sum(failed.values()), "by_condition": {key: failed[key] for key in "ABC"}},
        "remaining": {"total": len(rows) - len(summaries), "by_condition": {key: planned[key] - attempted[key] for key in "ABC"}},
        "status_counts": dict(sorted(Counter(item["status"] for item in summaries).items())),
        "model": MODEL,
        "reasoning_effort": REASONING,
        "formal_ai_evaluation_run": False,
    }
    path = OUTPUT_ROOT / f"batch-{batch}" / "batch_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        path.write_bytes(data)
    else:
        RUNNER.atomic_write(path, data)
    return result


def execute_batch(batch: int, workers: int, allow_infra_retry: bool) -> dict:
    gate = preflight()
    if gate["status"] != "PASS":
        raise RuntimeError(json.dumps(gate, ensure_ascii=False))
    rows = [row for row in load_payloads() if row["repeat_index"] == batch]
    pending = [row for row in rows if not (task_dir(row) / "task_summary.json").exists()]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"phase3d-b{batch}") as pool:
        futures = [pool.submit(existing_or_run, row, allow_infra_retry) for row in pending]
        for future in as_completed(futures):
            future.result()
    result = summarize_batch(batch, rows)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    run = sub.add_parser("run-batch")
    run.add_argument("batch", type=int, choices=[1, 2, 3])
    run.add_argument("--workers", type=int, default=6)
    run.add_argument("--allow-one-infrastructure-retry", action="store_true")
    summary = sub.add_parser("summarize-batch")
    summary.add_argument("batch", type=int, choices=[1, 2, 3])
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(preflight(), ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "run-batch":
        execute_batch(args.batch, args.workers, args.allow_one_infrastructure_retry)
    else:
        rows = [row for row in load_payloads() if row["repeat_index"] == args.batch]
        print(json.dumps(summarize_batch(args.batch, rows), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
