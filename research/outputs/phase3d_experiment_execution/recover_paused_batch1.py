#!/usr/bin/env python3
"""Recover paused Batch-1 outputs without issuing or retrying model calls."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
PAYLOADS = RESEARCH / "outputs/phase3c_pre_pilot_review/phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl"
WORKSPACES = {
    "P3-008-B-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-magx4563/workspace",
    "P3-012-C-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-6zmg73vb/workspace",
    "P3-014-B-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-ip_7zr9x/workspace",
    "P3-025-B-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-91utox2n/workspace",
    "P3-028-B-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-ysihmp9x/workspace",
    "P3-029-C-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-0ob9qdlj/workspace",
    "P3-030-A-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-ghnzx0cf/workspace",
    "P3-031-B-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-sb3__o9o/workspace",
    "P3-032-C-R1": "/var/folders/r0/_s_kp8jx3rj3f3rstq8zzcp00000gn/T/ossgb-phase3d-y5hbzzvm/workspace",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORMAL = load_module("phase3d_recovery_formal", HERE / "phase3d_formal_generation.py")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    rows = {row["task_id"]: row for row in FORMAL.load_payloads()}
    recovered = []
    for task_id, workspace_text in WORKSPACES.items():
        row = rows[task_id]
        workspace = Path(workspace_text)
        task_dir = FORMAL.task_dir(row)
        attempt_dir = task_dir / "attempt-1"
        summary_path = task_dir / "task_summary.json"
        if summary_path.exists():
            recovered.append(json.loads(summary_path.read_text(encoding="utf-8")))
            continue
        prompt = (workspace / "PROMPT.txt").read_bytes()
        if sha_bytes(prompt) != row["prompt_sha256"]:
            raise RuntimeError(f"{task_id}: recovered prompt hash mismatch")
        raw = (workspace / "answer.json").read_bytes() if (workspace / "answer.json").is_file() else b""
        if raw:
            schema_result, protocol = FORMAL.validate_completed_raw(row, raw)
            FORMAL.RUNNER.atomic_write(attempt_dir / "raw_response.json", raw)
            FORMAL.write_new(attempt_dir / "local_schema_validation.json", schema_result)
            FORMAL.write_new(attempt_dir / "protocol_validation.json", protocol)
            status = protocol["status"]
            summary = {
                "task_id": task_id, "bug_id": row["bug_id"], "condition": row["condition"],
                "repeat_index": row["repeat_index"], "attempt_index": 1, "attempt_count": 1,
                "status": status, "failure_class": protocol.get("failure_class"),
                "model_identifier_requested": FORMAL.MODEL, "model_version": "unavailable",
                "reasoning_effort": FORMAL.REASONING, "cli_version": FORMAL.EXPECTED_CLI,
                "prompt_sha256": row["prompt_sha256"], "schema_sha256": row["generation_schema_sha256"],
                "payload_line_sha256": row["_payload_line_sha256"], "payload_file_line": row["_payload_file_line"],
                "context_documents": row["context_documents"], "response_sha256": sha_bytes(raw),
                "start_timestamp": "unavailable_recovered_after_pause",
                "completion_timestamp": "unavailable_recovered_after_pause",
                "returncode": "unavailable_process_stale_or_exited",
                "raw_output_path": str((attempt_dir / "raw_response.json").relative_to(RESEARCH)),
                "schema_validation_status": schema_result["status"], "retry_eligible": False,
                "formal_observation": True, "recovery_provenance": "PAUSED_DISPOSABLE_WORKSPACE",
                "metadata_complete": False, "requires_execution_provenance_review": True,
                "completed_model_output_failure_not_retried": protocol.get("failure_class") == "MODEL_GENERATION_FAILURE",
            }
        else:
            summary = {
                "task_id": task_id, "bug_id": row["bug_id"], "condition": row["condition"],
                "repeat_index": row["repeat_index"], "attempt_index": 1, "attempt_count": 1,
                "status": "INTERRUPTED_UNKNOWN_INVOCATION_STATE", "failure_class": "INFRASTRUCTURE_FAILURE",
                "model_identifier_requested": FORMAL.MODEL, "model_version": "unavailable",
                "reasoning_effort": FORMAL.REASONING, "cli_version": FORMAL.EXPECTED_CLI,
                "prompt_sha256": row["prompt_sha256"], "schema_sha256": row["generation_schema_sha256"],
                "payload_line_sha256": row["_payload_line_sha256"], "payload_file_line": row["_payload_file_line"],
                "context_documents": row["context_documents"], "response_sha256": None,
                "start_timestamp": "unavailable_paused_attempt", "completion_timestamp": "unavailable_paused_attempt",
                "returncode": "stale_process_or_missing_process", "raw_output_path": None,
                "schema_validation_status": "NOT_RUN_NO_OUTPUT", "retry_eligible": False,
                "formal_observation": True, "recovery_provenance": "PAUSED_EMPTY_WORKSPACE",
                "metadata_complete": False, "requires_execution_provenance_review": True,
                "completed_model_output_failure_not_retried": False,
            }
        FORMAL.write_new(attempt_dir / "recovery_metadata.json", {
            "task_id": task_id, "workspace": workspace_text, "prompt_sha256": sha_bytes(prompt),
            "answer_bytes": len(raw), "answer_sha256": sha_bytes(raw) if raw else None,
            "automatic_retry_performed": False,
        })
        summary["attempts"] = [{k: summary[k] for k in (
            "task_id", "bug_id", "condition", "repeat_index", "attempt_index", "status",
            "failure_class", "prompt_sha256", "schema_sha256", "response_sha256", "retry_eligible",
        )}]
        FORMAL.write_new(summary_path, summary)
        recovered.append(summary)
    report = {
        "status": "PAUSED_BATCH_RECOVERY_COMPLETE",
        "records": len(recovered),
        "valid_recovered_outputs": sum(x["status"] in {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"} for x in recovered),
        "empty_unknown_attempts": sum(x["status"] == "INTERRUPTED_UNKNOWN_INVOCATION_STATE" for x in recovered),
        "automatic_retries": 0,
        "records_detail": recovered,
    }
    FORMAL.write_new(HERE / "paused_batch1_recovery_report.json", report)
    print(json.dumps({k: report[k] for k in ("status", "records", "valid_recovered_outputs", "empty_unknown_attempts", "automatic_retries")}, indent=2))


if __name__ == "__main__":
    main()
