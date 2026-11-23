#!/usr/bin/env python3
"""Offline task export/import boundary for Phase 3. Never invokes Codex or an API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


HERE = Path(__file__).resolve().parent
ALLOWED_PAYLOAD_KEYS = {
    "task_id", "bug_id", "condition", "repeat_index", "prompt_sha256",
    "context_documents", "rendered_prompt", "output_artifact_path",
    "generation_schema_sha256", "authorization_status",
}
ALLOWED_CONTEXT_KEYS = {"doc_id", "content_sha256", "slot"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def approved_context_map() -> dict[tuple[str, str], list[dict]]:
    path = HERE / "phase3_approved_context_integrity.json"
    if not path.is_file():
        raise FileNotFoundError(f"approved context integrity artifact is missing: {path}")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("status") != "PASS" or artifact.get("approved_total_assignments") != 108:
        raise ValueError("approved context integrity artifact is not valid")
    result: dict[tuple[str, str], list[dict]] = {}
    for record in artifact["records"]:
        result[(record["target_bug_id"], "A")] = []
        for condition in ("B", "C"):
            result[(record["target_bug_id"], condition)] = [
                {"slot": item["slot"], "doc_id": item["doc_id"], "content_sha256": item["content_sha256"]}
                for item in record[condition]
            ]
    return result


def approved_schedule_map() -> dict[str, dict]:
    paths = (HERE / "phase3_formal_162_task_schedule.jsonl", HERE / "phase3_pilot_18_task_schedule.jsonl")
    if not all(path.is_file() for path in paths):
        raise FileNotFoundError("approved offline task schedules are missing")
    return {row["task_id"]: row for path in paths for row in _jsonl(path)}


def validate_task_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    if set(payload) != ALLOWED_PAYLOAD_KEYS:
        errors.append("task payload keys violate the approved export allowlist")
    for key in ("task_id", "bug_id", "condition", "prompt_sha256", "rendered_prompt"):
        if not isinstance(payload.get(key), str) or not payload.get(key):
            errors.append(f"{key} must be a non-empty string")
    if payload.get("condition") not in {"A", "B", "C"}:
        errors.append("condition must be A, B, or C")
    if payload.get("repeat_index") not in {1, 2, 3}:
        errors.append("repeat_index must be 1, 2, or 3")
    contexts = payload.get("context_documents")
    if not isinstance(contexts, list):
        errors.append("context_documents must be an array")
    else:
        expected = 0 if payload.get("condition") == "A" else 3
        if len(contexts) != expected:
            errors.append(f"condition {payload.get('condition')} requires {expected} contexts")
        for item in contexts:
            if not isinstance(item, dict) or set(item) != ALLOWED_CONTEXT_KEYS:
                errors.append("context metadata violates the export allowlist")
                continue
            if not all(isinstance(item.get(k), str) for k in ("doc_id", "content_sha256")):
                errors.append("context identifiers and hashes must be strings")
            if not isinstance(item.get("slot"), int):
                errors.append("context slot must be an integer")
    rendered = payload.get("rendered_prompt")
    if isinstance(rendered, str) and sha256_bytes(rendered.encode("utf-8")) != payload.get("prompt_sha256"):
        errors.append("rendered prompt hash mismatch")
    forbidden = ("gold_reference", "repair_commit", "repair_diff", "evaluation_only", "audit_metadata")
    if any(token in json.dumps(payload, ensure_ascii=False).casefold() for token in forbidden):
        errors.append("payload contains a forbidden evaluation/repair marker")
    if payload.get("authorization_status") != "NOT_AUTHORIZED_FOR_EXECUTION":
        errors.append("task is not marked NOT_AUTHORIZED_FOR_EXECUTION")
    try:
        expected_contexts = approved_context_map().get((payload.get("bug_id"), payload.get("condition")))
        if expected_contexts is None or contexts != expected_contexts:
            errors.append("context manifest mismatch")
        schedule = approved_schedule_map().get(payload.get("task_id"))
        if schedule is None:
            errors.append("task ID is absent from approved offline schedules")
        else:
            comparisons = {
                "bug_id": "target_bug_id", "condition": "condition", "repeat_index": "repeat_index",
                "prompt_sha256": "prompt_sha256", "output_artifact_path": "output_artifact_path",
            }
            if any(payload.get(left) != schedule.get(right) for left, right in comparisons.items()):
                errors.append("task payload differs from the approved offline schedule")
            if [x.get("doc_id") for x in contexts or []] != schedule.get("context_document_ids"):
                errors.append("task context IDs differ from the approved offline schedule")
            if [x.get("content_sha256") for x in contexts or []] != schedule.get("context_content_sha256"):
                errors.append("task context hashes differ from the approved offline schedule")
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"approved-boundary validation failed: {exc}")
    return errors


def atomic_create(path: Path, data: bytes) -> None:
    """Create once; never silently overwrite an exported task or imported response."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = path.read_bytes()
        if existing != data:
            raise FileExistsError(f"refusing to overwrite conflicting immutable artifact: {path}")
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def export_task(payload: dict, workspace: Path) -> Path:
    errors = validate_task_payload(payload)
    if errors:
        raise ValueError("; ".join(errors))
    task_dir = workspace / payload["task_id"]
    task_file = task_dir / "task.json"
    atomic_create(task_file, canonical_json(payload))
    atomic_create(task_dir / "PROMPT.txt", payload["rendered_prompt"].encode("utf-8"))
    return task_file


def import_response(task_file: Path, response_file: Path, import_root: Path, metadata: dict | None = None) -> dict:
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    errors = validate_task_payload(payload)
    if errors:
        raise ValueError("invalid source task: " + "; ".join(errors))
    raw = response_file.read_bytes()
    if not raw:
        raise ValueError("empty response cannot be imported")
    response_hash = sha256_bytes(raw)
    destination = import_root / payload["task_id"] / "raw_response.bin"
    atomic_create(destination, raw)
    record = {
        "task_id": payload["task_id"],
        "bug_id": payload["bug_id"],
        "condition": payload["condition"],
        "repeat_index": payload["repeat_index"],
        "prompt_sha256": payload["prompt_sha256"],
        "context_hashes": [item["content_sha256"] for item in payload["context_documents"]],
        "response_sha256": response_hash,
        "raw_response_path": str(destination),
        "execution_timestamp": (metadata or {}).get("execution_timestamp", "unavailable"),
        "execution_method": (metadata or {}).get("execution_method", "unavailable"),
        "model_identifier": (metadata or {}).get("model_identifier", "unavailable"),
        "model_version": (metadata or {}).get("model_version", "unavailable"),
        "token_usage": (metadata or {}).get("token_usage", "unavailable"),
        "configuration_parameters": (metadata or {}).get("configuration_parameters", "unavailable"),
        "status": "IMPORTED_UNVALIDATED",
    }
    atomic_create(destination.with_name("import_record.json"), canonical_json(record))
    return record


def load_jsonl(path: Path) -> list[dict]:
    return _jsonl(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate-manifest")
    check.add_argument("manifest", type=Path)
    export = sub.add_parser("export")
    export.add_argument("manifest", type=Path)
    export.add_argument("workspace", type=Path)
    imp = sub.add_parser("import-response")
    imp.add_argument("task_file", type=Path)
    imp.add_argument("response_file", type=Path)
    imp.add_argument("import_root", type=Path)
    args = parser.parse_args()
    if args.command in {"validate-manifest", "export"}:
        rows = load_jsonl(args.manifest)
        all_errors = {row.get("task_id", "<missing>"): validate_task_payload(row) for row in rows}
        all_errors = {key: value for key, value in all_errors.items() if value}
        if all_errors:
            raise SystemExit(json.dumps(all_errors, indent=2))
        if args.command == "export":
            for row in rows:
                export_task(row, args.workspace)
        print(json.dumps({"status": "PASS", "records": len(rows), "model_calls": 0}))
    else:
        print(json.dumps(import_response(args.task_file, args.response_file, args.import_root), indent=2))


if __name__ == "__main__":
    main()
