#!/usr/bin/env python3
"""Build versioned Phase 3C schedules/payloads from approved Phase 3 inputs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
AB_DIR = RESEARCH / "outputs/phase3_ab_offline_preparation"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rows), encoding="utf-8")


def main() -> None:
    ab = load("phase3_ab_builder", AB_DIR / "build_phase3_offline_artifacts.py")
    serializer = load("frozen_serializer_phase3c", FROZEN / "generation_input_serializer.py")
    verified, integrity, errors = ab.validate_and_load_contexts()
    if errors or integrity["status"] != "PASS":
        raise SystemExit("approved context integrity failed: " + "; ".join(errors))
    prompt_path = HERE / "phase3_generation_prompt_v2_PROPOSED.txt"
    schema_path = HERE / "phase3_generation_output_v2_PROPOSED.schema.json"
    formal, payloads = ab.build_schedule(verified, serializer, prompt_path.read_text(encoding="utf-8"), sha(schema_path))
    for row in formal:
        row["output_artifact_path"] = f"responses/phase3c_v2/formal/{row['task_id']}/raw_response.bin"
        row["execution_status"] = "VERSIONED_V2_PLANNED_NOT_AUTHORIZED"
        row["protocol_version"] = "phase3c-v2-proposed"
    for payload, schedule in zip(payloads, formal):
        payload["output_artifact_path"] = schedule["output_artifact_path"]
        payload["protocol_version"] = "phase3c-v2-proposed"
    formal_schedule = HERE / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl"
    formal_payloads = HERE / "phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl"
    write_jsonl(formal_schedule, formal); write_jsonl(formal_payloads, payloads)

    ab_pilot = jsonl(AB_DIR / "phase3_pilot_18_task_schedule.jsonl")
    wanted = {(x["target_bug_id"], x["condition"], x["repeat_index"]): x for x in formal}
    payload_map = {(x["bug_id"], x["condition"], x["repeat_index"]): x for x in payloads}
    pilot = []
    pilot_payloads = []
    for order, old in enumerate(ab_pilot, 1):
        key = (old["target_bug_id"], old["condition"], old["repeat_index"])
        source = wanted[key]
        row = dict(source)
        row["task_id"] = old["task_id"]
        row["planned_execution_order"] = order
        row["output_artifact_path"] = f"responses/phase3c_v2/pilot/{row['task_id']}/raw_response.bin"
        row["execution_status"] = "VERSIONED_V2_ENGINEERING_PILOT_NOT_AUTHORIZED"
        pilot.append(row)
        payload = dict(payload_map[key])
        payload["task_id"] = row["task_id"]
        payload["output_artifact_path"] = row["output_artifact_path"]
        pilot_payloads.append(payload)
    pilot_schedule = HERE / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl"
    pilot_payload_path = HERE / "phase3c_pilot_18_task_payloads_v2_NOT_AUTHORIZED.jsonl"
    write_jsonl(pilot_schedule, pilot); write_jsonl(pilot_payload_path, pilot_payloads)

    old_formal = jsonl(AB_DIR / "phase3_formal_162_task_schedule.jsonl")
    old_pilot = jsonl(AB_DIR / "phase3_pilot_18_task_schedule.jsonl")
    differences = {
        "status": "VERSIONED_DERIVATION_NOT_AUTHORIZED",
        "old_prompt_template_sha256": sha(AB_DIR / "phase3_generation_prompt_v1_PROPOSED.txt"),
        "new_prompt_template_sha256": sha(prompt_path),
        "old_schema_sha256": sha(AB_DIR / "phase3_generation_output.schema.json"),
        "new_schema_sha256": sha(schema_path),
        "formal_records": len(formal), "pilot_records": len(pilot),
        "formal_task_identity_or_order_changes": sum((a["task_id"], a["target_bug_id"], a["condition"], a["repeat_index"], a["planned_execution_order"]) != (b["task_id"], b["target_bug_id"], b["condition"], b["repeat_index"], b["planned_execution_order"]) for a, b in zip(old_formal, formal)),
        "pilot_task_identity_or_order_changes": sum((a["task_id"], a["target_bug_id"], a["condition"], a["repeat_index"], a["planned_execution_order"]) != (b["task_id"], b["target_bug_id"], b["condition"], b["repeat_index"], b["planned_execution_order"]) for a, b in zip(old_pilot, pilot)),
        "formal_rendered_prompt_hash_changes": sum(a["prompt_sha256"] != b["prompt_sha256"] for a, b in zip(old_formal, formal)),
        "pilot_rendered_prompt_hash_changes": sum(a["prompt_sha256"] != b["prompt_sha256"] for a, b in zip(old_pilot, pilot)),
        "context_assignment_changes": sum(a["context_document_ids"] != b["context_document_ids"] for a, b in zip(old_formal, formal)),
        "gold_or_metric_changes": 0,
        "official_model_calls": 0,
        "artifacts": {path.name: sha(path) for path in (formal_schedule, formal_payloads, pilot_schedule, pilot_payload_path)},
    }
    (HERE / "phase3c_v2_derivation_manifest.json").write_text(json.dumps(differences, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(differences, indent=2))


if __name__ == "__main__":
    main()
