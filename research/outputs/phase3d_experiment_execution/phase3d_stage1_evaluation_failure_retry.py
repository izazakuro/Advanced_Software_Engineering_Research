#!/usr/bin/env python3
"""One explicitly authorized retry for each unresolved Stage-1 evaluation failure."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
from pathlib import Path
import threading


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
ROOT = HERE / "stage1_ai_evaluation"
RESPONSES = ROOT / "responses"
VALID_GENERATION = {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}
_lock = threading.Lock()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVAL = load_module("phase3d_stage1_eval_retry_base", HERE / "phase3d_stage1_ai_evaluation.py")


def original_effective_failure(anonymous_id: str) -> tuple[bool, str, list[str]]:
    directory = RESPONSES / anonymous_id
    summary = json.loads((directory / "evaluation_summary.json").read_text(encoding="utf-8"))
    final_path = directory / "semantic_validation_v3_final.json"
    if final_path.is_file():
        final = json.loads(final_path.read_text(encoding="utf-8"))
        if final["status"] == "SEMANTIC_VALID_FINAL":
            return False, "AI_VALID_PROVISIONAL", []
        return True, "EVALUATION_FAILED_SEMANTIC", final["errors"]
    return True, "EVALUATION_FAILED_INFRASTRUCTURE", []


def retry_one(row: dict, anonymous_id: str, gold: dict) -> dict:
    directory = RESPONSES / anonymous_id
    eligible, original_status, original_errors = original_effective_failure(anonymous_id)
    if not eligible:
        return {"anonymous_response_id": anonymous_id, "status": "NOT_ELIGIBLE_ORIGINAL_VALID"}
    summary_path = directory / "authorized_retry_1_summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    retry_dir = directory / "authorized_retry_1"
    package, prompt = EVAL.build_inputs(row, anonymous_id, gold)
    EVAL.write_json(retry_dir / "anonymous_evaluation_package.json", package)
    EVAL.write_json(retry_dir / "attempt_intent.json", {
        "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"],
        "authorization": "Human authorization on 2026-11-19: 失败任务重试次数控制为一次",
        "retry_index": 1, "maximum_authorized_retries": 1,
        "original_effective_status": original_status, "original_errors": original_errors,
        "generation_output_sha256": row["effective_output_sha256"],
        "model": EVAL.MODEL, "reasoning_effort": EVAL.REASONING,
        "runtime_prompt_sha256": EVAL.sha_bytes(prompt.encode()),
        "schema_sha256": EVAL.sha(EVAL.P3H / "phase3c_h_ai_evaluator_output_v2.schema.json"),
        "semantic_runtime_appendix_sha256": EVAL.sha(EVAL.HERE / "stage1_ai_evaluation/diagnostics/semantic_contract_runtime_appendix_v1_PROPOSED.txt"),
        "automatic_output_repair": False,
    })
    EVAL.RUNNER.run_codex_json(
        prompt=prompt, schema_path=EVAL.P3H / "phase3c_h_ai_evaluator_output_v2.schema.json",
        model=EVAL.MODEL, reasoning=EVAL.REASONING, run_dir=retry_dir / "model_run",
    )
    retry = EVAL.finalize(row, anonymous_id, retry_dir, gold)
    result = {
        "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"],
        "original_effective_status": original_status, "original_errors": original_errors,
        "retry_status": retry["status"], "retry_response_sha256": retry.get("response_sha256"),
        "retry_count": 1, "further_retry_allowed": False,
        "effective_status": "AI_VALID_PROVISIONAL" if retry["status"] == "AI_EVALUATION_VALID_PROVISIONAL" else retry["status"],
        "original_artifacts_preserved": True, "retry": retry,
    }
    EVAL.write_json(summary_path, result)
    with _lock:
        print(json.dumps({"anonymous_response_id": anonymous_id, "bug_id": row["bug_id"], "original": original_status, "retry": retry["status"]}), flush=True)
    return result


def main() -> None:
    gate = EVAL.preflight()
    if gate["status"] != "PASS":
        raise RuntimeError(json.dumps(gate))
    stage1 = EVAL.jsonl(EVAL.STAGE1_MANIFEST)
    rows = {row["task_id"]: row for row in stage1 if row["effective_status"] in VALID_GENERATION}
    mapping = json.loads((EVAL.RESTRICTED / "stage1_task_mapping.json").read_text(encoding="utf-8"))
    gold = {row["bug_id"]: row for row in EVAL.jsonl(EVAL.FROZEN / "gold_v1.jsonl")}
    eligible = []
    for anonymous_id, restricted in mapping.items():
        is_failure, _, _ = original_effective_failure(anonymous_id)
        if is_failure:
            eligible.append((rows[restricted["task_id"]], anonymous_id))
    if len(eligible) != 11:
        raise RuntimeError(f"expected 11 unresolved evaluation failures, found {len(eligible)}")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="phase3d-eval-retry") as pool:
        futures = [pool.submit(retry_one, row, anonymous_id, gold[row["bug_id"]]) for row, anonymous_id in eligible]
        for future in as_completed(futures):
            future.result()
    records = [json.loads((RESPONSES / anonymous_id / "authorized_retry_1_summary.json").read_text(encoding="utf-8")) for _, anonymous_id in eligible]
    report = {
        "status": "AUTHORIZED_SINGLE_RETRY_COMPLETE",
        "eligible_failures": len(eligible), "retry_calls": len(records),
        "retry_valid": sum(row["effective_status"] == "AI_VALID_PROVISIONAL" for row in records),
        "retry_failed": sum(row["effective_status"] != "AI_VALID_PROVISIONAL" for row in records),
        "further_retries_allowed": False, "records": records,
    }
    EVAL.write_json(ROOT / "authorized_failure_retry_summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
