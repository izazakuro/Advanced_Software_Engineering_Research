#!/usr/bin/env python3
"""Build the paused Phase 3D Stage-1 generation-only checkpoint."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORMAL = load_module("phase3d_stage1_formal", HERE / "phase3d_formal_generation.py")
VALID = {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main() -> None:
    rows = [row for row in FORMAL.load_payloads() if row["repeat_index"] == 1]
    manifest = []
    for row in rows:
        directory = FORMAL.task_dir(row)
        original = json.loads((directory / "task_summary.json").read_text(encoding="utf-8"))
        retry_path = directory / "authorized_retry_summary.json"
        retry = json.loads(retry_path.read_text(encoding="utf-8")) if retry_path.exists() else None
        effective_status = retry["effective_status"] if retry else original["status"]
        if retry and retry["retry"].get("raw_output_path"):
            output_path = RESEARCH / retry["retry"]["raw_output_path"]
            output_hash = retry["retry"].get("response_sha256")
        elif original.get("raw_output_path"):
            output_path = RESEARCH / original["raw_output_path"]
            output_hash = original.get("response_sha256")
        else:
            output_path = None
            output_hash = None
        if effective_status in VALID:
            if output_path is None or not output_path.is_file() or sha(output_path) != output_hash:
                raise RuntimeError(f"{row['task_id']}: effective output missing or hash mismatch")
        manifest.append({
            "task_id": row["task_id"], "bug_id": row["bug_id"], "condition": row["condition"],
            "repeat_index": 1, "payload_line_sha256": row["_payload_line_sha256"],
            "prompt_sha256": row["prompt_sha256"], "schema_sha256": row["generation_schema_sha256"],
            "original_status": original["status"], "authorized_retry_used": retry is not None,
            "effective_status": effective_status,
            "effective_attempt_index": retry["effective_attempt_index"] if retry else original["attempt_index"],
            "effective_output_path": str(output_path.relative_to(RESEARCH)) if output_path else None,
            "effective_output_sha256": output_hash,
            "metadata_complete": original.get("metadata_complete", True) if not retry else True,
            "requires_execution_provenance_review": original.get("requires_execution_provenance_review", False) if not retry else False,
            "formal_ai_evaluation_run": False,
        })
    valid = Counter(x["condition"] for x in manifest if x["effective_status"] in VALID)
    failed = Counter(x["condition"] for x in manifest if x["effective_status"] not in VALID)
    complete_bugs = 0
    for bug in sorted({x["bug_id"] for x in manifest}):
        values = [x for x in manifest if x["bug_id"] == bug]
        if len(values) == 3 and all(x["effective_status"] in VALID for x in values):
            complete_bugs += 1
    manifest_path = HERE / "phase3d_stage1_54_generation_manifest.jsonl"
    write(manifest_path, "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in manifest).encode())

    batch2 = FORMAL.OUTPUT_ROOT / "batch-2"
    batch2_intents = sorted(x.parents[1].name for x in batch2.glob("P3-*/attempt-1/attempt_intent.json"))
    batch2_summaries = {}
    for path in batch2.glob("P3-*/task_summary.json"):
        batch2_summaries[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
    pause_report = {
        "status": "USER_PAUSED_AFTER_STAGE1",
        "user_instruction": "暂停，第一阶段仅需要54条",
        "stage1_scope": "Repeat 1 only, 54 frozen tasks",
        "batch2_intents_created_before_pause": batch2_intents,
        "batch2_completed_before_pause": [
            {"task_id": task, "status": value["status"], "response_sha256": value.get("response_sha256"), "excluded_from_stage1": True}
            for task, value in sorted(batch2_summaries.items())
        ],
        "batch2_interrupted_before_metadata": sorted(set(batch2_intents) - set(batch2_summaries)),
        "batch2_and_batch3_execution_authorized_now": False,
        "all_batch2_artifacts_excluded_from_stage1": True,
        "formal_ai_evaluation_run": False,
    }
    write(HERE / "batch2_user_pause_report.json", (json.dumps(pause_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    summary = {
        "status": "STAGE1_GENERATION_COMPLETE_WITH_ONE_UNRESOLVED_INFRASTRUCTURE_FAILURE",
        "planned_tasks": 54, "task_identities_attempted": 54,
        "authorized_retry_calls": sum(x["authorized_retry_used"] for x in manifest),
        "effective_valid": {"total": sum(valid.values()), "by_condition": {c: valid[c] for c in "ABC"}},
        "effective_failed": {"total": sum(failed.values()), "by_condition": {c: failed[c] for c in "ABC"}},
        "complete_bug_triplets": complete_bugs,
        "incomplete_bug_triplets": 18 - complete_bugs,
        "unresolved_tasks": [x["task_id"] for x in manifest if x["effective_status"] not in VALID],
        "recovered_outputs_with_incomplete_metadata": [x["task_id"] for x in manifest if not x["metadata_complete"]],
        "protocol_deviation": "Five failed tasks received one explicitly human-authorized retry; original artifacts remain preserved.",
        "manifest_path": str(manifest_path.relative_to(RESEARCH)),
        "manifest_sha256": sha(manifest_path),
        "formal_ai_evaluation_ready": True,
        "formal_ai_evaluation_readiness_qualification": "53 valid outputs; one B task is missing, leaving 17 complete A/B/C Bug triplets.",
        "formal_ai_evaluation_authorized": False,
        "human_adjudication_run": False, "statistical_analysis_run": False,
        "score_locked": False, "arms_unblinded": False,
    }
    write(HERE / "phase3d_stage1_generation_summary.json", (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    report = f"""# Phase 3D Stage-1 Formal Generation Checkpoint

Status: `{summary['status']}`

- Planned frozen Repeat-1 tasks: 54
- Task identities attempted: 54
- Explicitly authorized retry calls: {summary['authorized_retry_calls']}
- Effective valid outputs: {summary['effective_valid']['total']} (A {valid['A']}, B {valid['B']}, C {valid['C']})
- Effective unresolved failures: {summary['effective_failed']['total']} (A {failed['A']}, B {failed['B']}, C {failed['C']})
- Complete A/B/C Bug triplets: {complete_bugs}/18
- Unresolved task: {', '.join(summary['unresolved_tasks'])}

Five failed tasks received one later human-authorized retry. The first attempts and their errors remain preserved; the retry is explicitly recorded as a protocol deviation. No further retry is allowed. Five outputs recovered after the pause have incomplete process metadata and remain flagged for execution-provenance review.

Batch 2 was stopped immediately after the user limited execution to Stage 1. Two Batch-2 outputs completed before termination and two intents were interrupted; all four are preserved but excluded from the Stage-1 dataset. Batch 3 was never started.

No formal AI evaluation, human adjudication, statistical analysis, score lock, or unblinding was performed.
"""
    write(HERE / "phase3d_stage1_generation_report.md", report.encode())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
