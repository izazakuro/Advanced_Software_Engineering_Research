#!/usr/bin/env python3
"""Fail-closed adapter for Phase 3C v2 proposed task payloads; never invokes Codex."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
AB_DIR = RESEARCH / "outputs/phase3_ab_offline_preparation"
PAYLOAD_KEYS = {"task_id", "bug_id", "condition", "repeat_index", "prompt_sha256", "context_documents", "rendered_prompt", "output_artifact_path", "generation_schema_sha256", "authorization_status", "protocol_version"}
CONTEXT_KEYS = {"doc_id", "content_sha256", "slot"}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def schema_bytes() -> bytes:
    return (HERE / "phase3_generation_output_v2_PROPOSED.schema.json").read_bytes()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def schedule_map() -> dict[str, dict]:
    paths = (HERE / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl", HERE / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl")
    if not all(path.is_file() for path in paths):
        raise FileNotFoundError("Phase 3C v2 schedules are missing")
    return {row["task_id"]: row for path in paths for row in jsonl(path)}


def context_map() -> dict[tuple[str, str], list[dict]]:
    artifact = json.loads((AB_DIR / "phase3_approved_context_integrity.json").read_text(encoding="utf-8"))
    if artifact.get("status") != "PASS" or artifact.get("approved_total_assignments") != 108:
        raise ValueError("Phase 3A/B context integrity is not PASS/108")
    result = {}
    for row in artifact["records"]:
        result[(row["target_bug_id"], "A")] = []
        for condition in ("B", "C"):
            result[(row["target_bug_id"], condition)] = [{"slot": x["slot"], "doc_id": x["doc_id"], "content_sha256": x["content_sha256"]} for x in row[condition]]
    return result


def validate_payload(payload: dict) -> list[str]:
    errors = []
    if set(payload) != PAYLOAD_KEYS:
        errors.append("payload keys violate the Phase 3C allowlist")
    if payload.get("protocol_version") != "phase3c-v2-proposed":
        errors.append("wrong protocol version")
    if payload.get("authorization_status") != "NOT_AUTHORIZED_FOR_EXECUTION":
        errors.append("payload is not explicitly unauthorized")
    if payload.get("condition") not in {"A", "B", "C"}:
        errors.append("invalid condition")
    contexts = payload.get("context_documents")
    if not isinstance(contexts, list) or any(not isinstance(x, dict) or set(x) != CONTEXT_KEYS for x in contexts):
        errors.append("invalid context projection")
    try:
        if contexts != context_map().get((payload.get("bug_id"), payload.get("condition"))):
            errors.append("approved context manifest mismatch")
        schedule = schedule_map().get(payload.get("task_id"))
        if schedule is None:
            errors.append("unknown Phase 3C task ID")
        else:
            pairs = (("bug_id", "target_bug_id"), ("condition", "condition"), ("repeat_index", "repeat_index"), ("prompt_sha256", "prompt_sha256"), ("output_artifact_path", "output_artifact_path"))
            if any(payload.get(a) != schedule.get(b) for a, b in pairs):
                errors.append("payload differs from versioned Phase 3C schedule")
            if [x.get("doc_id") for x in contexts or []] != schedule["context_document_ids"]:
                errors.append("context IDs differ from schedule")
            if [x.get("content_sha256") for x in contexts or []] != schedule["context_content_sha256"]:
                errors.append("context hashes differ from schedule")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"boundary validation failed: {exc}")
    rendered = payload.get("rendered_prompt")
    if not isinstance(rendered, str) or sha_bytes(rendered.encode("utf-8")) != payload.get("prompt_sha256"):
        errors.append("rendered prompt hash mismatch")
    if payload.get("generation_schema_sha256") != sha_bytes(schema_bytes()):
        errors.append("generation schema hash mismatch")
    forbidden = ("gold_reference", "repair_commit", "repair_diff", "evaluation_only", "audit_metadata")
    if any(x in json.dumps(payload, ensure_ascii=False).casefold() for x in forbidden):
        errors.append("forbidden evaluation/repair marker")
    return errors


def atomic_create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def export_payload(payload: dict, workspace: Path) -> Path:
    errors = validate_payload(payload)
    if errors:
        raise ValueError("; ".join(errors))
    task_dir = workspace / payload["task_id"]
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    atomic_create(task_dir / "task.json", raw)
    atomic_create(task_dir / "PROMPT.txt", payload["rendered_prompt"].encode("utf-8"))
    atomic_create(task_dir / "OUTPUT_SCHEMA.json", schema_bytes())
    return task_dir / "task.json"


def import_attempt(task_file: Path, response_file: Path | None, import_root: Path, metadata: dict) -> dict:
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    errors = validate_payload(payload)
    if errors:
        raise ValueError("invalid task: " + "; ".join(errors))
    attempt = int(metadata.get("attempt_index", 1))
    if attempt < 1 or attempt > 3:
        raise ValueError("attempt_index must be 1..3 under the proposed retry policy")
    raw = b"" if response_file is None else response_file.read_bytes()
    attempt_dir = import_root / payload["task_id"] / f"attempt-{attempt}"
    if response_file is not None:
        atomic_create(attempt_dir / "raw_response.bin", raw)
    record = {
        "task_id": payload["task_id"], "attempt_index": attempt,
        "prompt_sha256": payload["prompt_sha256"], "context_hashes": [x["content_sha256"] for x in payload["context_documents"]],
        "response_sha256": sha_bytes(raw) if response_file is not None else None,
        "execution_timestamp": metadata.get("execution_timestamp", "unavailable"),
        "execution_method": metadata.get("execution_method", "unavailable"),
        "model_identifier": metadata.get("model_identifier", "unavailable"),
        "model_version": metadata.get("model_version", "unavailable"),
        "token_usage": metadata.get("token_usage", "unavailable"),
        "process_status": metadata.get("process_status", "unavailable"),
        "original_attempt_preserved": True,
    }
    atomic_create(attempt_dir / "attempt_record.json", (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    rows = jsonl(args.manifest)
    failures = {row.get("task_id", "<missing>"): validate_payload(row) for row in rows}
    failures = {k: v for k, v in failures.items() if v}
    if failures:
        raise SystemExit(json.dumps(failures, indent=2))
    print(json.dumps({"status": "PASS_PHASE3C_PAYLOADS", "records": len(rows), "model_calls": 0}))


if __name__ == "__main__":
    main()
