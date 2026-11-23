#!/usr/bin/env python3
"""Checkpointed AI evaluation for the 53 valid Phase 3D Stage-1 outputs."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import threading


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
P3H = RESEARCH / "outputs/phase3c_h_ai_first_amendment"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
STAGE1_MANIFEST = HERE / "phase3d_stage1_54_generation_manifest.jsonl"
OUTPUT_ROOT = HERE / "stage1_ai_evaluation"
RESTRICTED = OUTPUT_ROOT / "restricted"
PUBLIC = OUTPUT_ROOT / "public"
MODEL = "gpt-6.2-astra"
REASONING = "medium"
EXPECTED_CLI = "codex-cli 0.167.0-alpha.4.1"
EXPECTED = {
    STAGE1_MANIFEST: "38215a9d6034bc30825fed55fdf433f90f2399bdd5c21de61321a953b2d1ca76",
    P3H / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt": "fc8cf1e3cb9a03ce9196cc46683e8fc6e14e976ebdaa9835abee27cf0827d969",
    P3H / "phase3c_h_ai_evaluator_output_v2.schema.json": "a9cae1afc85403d0606a4d70d252e931f33c8cca849bbf2d7c8366996e7ab2ae",
    FROZEN / "gold_v1.jsonl": "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66",
    FROZEN / "gold_v1_metric_eligibility.json": "e028d0e3abf27dca7d247553ed7c0d0d0d59bf423a0b3aec3498f7e43183a3ef",
    RESEARCH / "reports/test_scenario_evaluation_rubric_abc_PROPOSED.md": "233033c5a0adc8d4fccfcdae312a47574b2dd09897cb63ebdfa28284581d27d9",
    P3H / "phase3c_h_primary_endpoint_specification.md": "78ca765f0a88ff94ce554c18f3ff6e8ab83ac913319816afc1cd3d22e0657d3b",
    HERE / "stage1_ai_evaluation/diagnostics/semantic_contract_runtime_appendix_v1_PROPOSED.txt": "03c6d8120a75549ee62e3edf231d2afaa35cb6ff165e5cf5b000db39fa4b6ff6",
}
VALID_GENERATION = {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}
_lock = threading.Lock()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_module("phase3d_stage1_eval_runner", HERE / "phase3d_runner.py")
RETRY = load_module("phase3d_stage1_eval_schema", HERE / "phase3d_evaluator_only_retry.py")
PIPE = load_module("phase3d_stage1_eval_pipeline", P3H / "phase3c_h_evaluation_pipeline.py")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: object, *, overwrite_summary: bool = False) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite_summary and path.exists():
        path.write_bytes(data)
    else:
        RUNNER.atomic_write(path, data)


def load_or_create_mapping(rows: list[dict]) -> tuple[dict[str, str], dict[str, dict]]:
    secret_path = RESTRICTED / "stage1_blinding_secret.bin"
    mapping_path = RESTRICTED / "stage1_task_mapping.json"
    public_path = PUBLIC / "stage1_anonymous_responses.jsonl"
    RESTRICTED.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        task_to_anon = {value["task_id"]: key for key, value in mapping.items()}
        if set(task_to_anon) != {row["task_id"] for row in rows}:
            raise RuntimeError("restricted anonymous mapping differs from Stage-1 valid set")
        return task_to_anon, mapping
    secret = os.urandom(32)
    fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secret)
    mapping = {}
    task_to_anon = {}
    public_rows = []
    for row in rows:
        opaque = "AR1-" + hmac.new(secret, row["task_id"].encode(), hashlib.sha256).hexdigest()[:24]
        if opaque in mapping:
            raise RuntimeError("anonymous ID collision")
        mapping[opaque] = {
            "task_id": row["task_id"], "condition": row["condition"],
            "repeat_index": row["repeat_index"], "bug_id": row["bug_id"],
            "generation_output_sha256": row["effective_output_sha256"],
        }
        task_to_anon[row["task_id"]] = opaque
        public_rows.append({"anonymous_response_id": opaque, "bug_id": row["bug_id"]})
    write_json(mapping_path, mapping)
    os.chmod(mapping_path, 0o600)
    RUNNER.atomic_write(public_path, "".join(json.dumps(x, sort_keys=True) + "\n" for x in public_rows).encode())
    return task_to_anon, mapping


def preflight() -> dict:
    hashes = {str(path.relative_to(RESEARCH)): sha(path) for path in EXPECTED}
    mismatches = [str(path.relative_to(RESEARCH)) for path, expected in EXPECTED.items() if sha(path) != expected]
    rows = jsonl(STAGE1_MANIFEST)
    valid = [row for row in rows if row["effective_status"] in VALID_GENERATION]
    errors = []
    if len(rows) != 54 or len(valid) != 53:
        errors.append("Stage-1 manifest must contain 54 records and 53 valid outputs")
    if {row["repeat_index"] for row in rows} != {1}:
        errors.append("non-Repeat-1 record in Stage-1 manifest")
    invalid = [row["task_id"] for row in rows if row["effective_status"] not in VALID_GENERATION]
    if invalid != ["P3-025-B-R1"]:
        errors.append(f"unexpected generation missingness: {invalid}")
    for row in valid:
        path = RESEARCH / row["effective_output_path"]
        if not path.is_file() or sha(path) != row["effective_output_sha256"]:
            errors.append(f"{row['task_id']}: generation output hash mismatch")
    cli = RUNNER.codex_version()
    if cli != EXPECTED_CLI:
        errors.append(f"CLI configuration drift: {cli}")
    result = {
        "status": "PASS" if not mismatches and not errors else "BLOCKED",
        "hashes": hashes, "hash_mismatches": mismatches, "errors": errors,
        "stage1_manifest_sha256": sha(STAGE1_MANIFEST),
        "stage1_records": len(rows), "evaluation_inputs": len(valid),
        "excluded_generation_missing": invalid,
        "batch2_outputs_included": False, "model": MODEL, "reasoning_effort": REASONING,
        "cli_version": cli, "cli_output_schema_used": False,
    }
    write_json(OUTPUT_ROOT / "preflight.json", result, overwrite_summary=True)
    return result


def build_inputs(row: dict, anonymous_id: str, gold: dict) -> tuple[dict, str]:
    generated = json.loads((RESEARCH / row["effective_output_path"]).read_text(encoding="utf-8"))
    rubric = ((RESEARCH / "reports/test_scenario_evaluation_rubric_abc_PROPOSED.md").read_text(encoding="utf-8") +
              "\n\n" + (P3H / "phase3c_h_primary_endpoint_specification.md").read_text(encoding="utf-8"))
    package = PIPE.build_anonymous_evaluation_package(anonymous_id, generated, gold, rubric)
    template = (P3H / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt").read_text(encoding="utf-8")
    base = (template.replace("{{ANONYMOUS_RESPONSE_ID}}", anonymous_id)
            .replace("{{BUG_ID}}", package["bug_id"])
            .replace("{{GENERATED_SCENARIOS_JSON}}", json.dumps(package["generated_scenarios_untrusted_data"], ensure_ascii=False))
            .replace("{{FROZEN_GOLD_EVALUATION_JSON}}", json.dumps(package["frozen_gold_evaluation_projection"], ensure_ascii=False))
            .replace("{{APPROVED_RUBRIC_TEXT}}", package["approved_rubric_text"]))
    schema_path = P3H / "phase3c_h_ai_evaluator_output_v2.schema.json"
    schema_text = schema_path.read_text(encoding="utf-8")
    eligibility = PIPE.eligibility_by_bug(PIPE.load_eligibility())[row["bug_id"]]
    exact_sets = {
        "required_tcc_path_ids": sorted(item["path_id"] for item in eligibility["tcc_paths"]),
        "required_cspc_path_ids": sorted(eligibility["cspc_eligible_path_ids"]),
        "required_strict_oracle_ids": sorted(
            item["oracle_id"] for item in eligibility["strict_oracles"]
            if item["strict_scoring_disposition"] == "INCLUDED"
        ),
        "allowed_gold_evidence_ids": sorted(PIPE.gold_requirement_catalog(gold)),
    }
    appendix_path = HERE / "stage1_ai_evaluation/diagnostics/semantic_contract_runtime_appendix_v1_PROPOSED.txt"
    semantic_appendix = (appendix_path.read_text(encoding="utf-8")
        .replace("{{REQUIRED_TCC_PATH_IDS_JSON}}", json.dumps(exact_sets["required_tcc_path_ids"]))
        .replace("{{REQUIRED_CSPC_PATH_IDS_JSON}}", json.dumps(exact_sets["required_cspc_path_ids"]))
        .replace("{{REQUIRED_STRICT_ORACLE_IDS_JSON}}", json.dumps(exact_sets["required_strict_oracle_ids"]))
        .replace("{{ALLOWED_GOLD_EVIDENCE_IDS_JSON}}", json.dumps(exact_sets["allowed_gold_evidence_ids"])))
    prompt = RETRY.runtime_prompt(base, schema_text, json.loads(schema_text)) + "\n\n" + semantic_appendix
    return package, prompt


def finalize(row: dict, anonymous_id: str, run_dir: Path, gold: dict) -> dict:
    meta = json.loads((run_dir / "model_run/metadata.json").read_text(encoding="utf-8"))
    raw = (run_dir / "model_run/raw_response.json").read_bytes()
    result = {
        "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"],
        "generation_output_sha256": row["effective_output_sha256"],
        "model_identifier_requested": meta["model_identifier_requested"],
        "model_version": meta["model_version"], "reasoning_effort": meta["reasoning_effort"],
        "cli_version": meta["cli_version"], "start_timestamp": meta["start_timestamp"],
        "completion_timestamp": meta["completion_timestamp"], "returncode": meta["returncode"],
        "prompt_sha256": meta["prompt_sha256"], "schema_sha256": meta["schema_sha256"],
        "response_sha256": meta["response_sha256"], "formal_ai_evaluation": True,
        "generation_failure": False, "deterministic_scoring_run": False,
    }
    if meta["returncode"] != 0:
        result.update({"status": "AI_EVALUATION_INFRASTRUCTURE_FAILURE", "failure_class": "AI_EVALUATION_FAILURE"})
        write_json(run_dir / "evaluation_summary.json", result)
        return result
    parsed = PIPE.parse_ai_assessment(raw)
    if parsed["status"] != "PARSED_PENDING_VALIDATION":
        validation = {"status": "INVALID_JSON", "errors": [{"json_path": "$", "keyword": "parse", "message": parsed["error"]}]}
        write_json(run_dir / "schema_validation.json", validation)
        result.update({"status": "AI_EVALUATION_JSON_FAILURE", "failure_class": "AI_EVALUATION_FAILURE", "validation_errors": validation["errors"]})
        write_json(run_dir / "evaluation_summary.json", result)
        return result
    assessment = parsed["assessment"]
    schema = json.loads((P3H / "phase3c_h_ai_evaluator_output_v2.schema.json").read_text(encoding="utf-8"))
    structural = RETRY.validate_schema(assessment, schema, schema)
    write_json(run_dir / "schema_validation.json", {
        "status": "SCHEMA_VALID" if not structural else "SCHEMA_INVALID",
        "error_count": len(structural), "errors": structural,
        "schema_sha256": sha(P3H / "phase3c_h_ai_evaluator_output_v2.schema.json"),
        "response_sha256": sha_bytes(raw),
    })
    if structural:
        result.update({"status": "AI_EVALUATION_SCHEMA_FAILURE", "failure_class": "AI_EVALUATION_FAILURE", "validation_errors": structural})
        write_json(run_dir / "evaluation_summary.json", result)
        return result
    write_json(run_dir / "ai_assessment.json", assessment)
    semantic = PIPE.validate_ai_assessment(assessment, PIPE.load_eligibility(), PIPE.gold_requirement_catalog(gold))
    write_json(run_dir / "semantic_validation.json", {"status": "SEMANTIC_VALID" if not semantic else "SEMANTIC_INVALID", "errors": semantic})
    if semantic:
        result.update({"status": "AI_EVALUATION_SEMANTIC_FAILURE", "failure_class": "AI_EVALUATION_FAILURE", "validation_errors": semantic})
        write_json(run_dir / "evaluation_summary.json", result)
        return result
    scores = PIPE.deterministic_scores(assessment, PIPE.load_eligibility(), PIPE.gold_requirement_catalog(gold))
    write_json(run_dir / "provisional_deterministic_scores.json", scores)
    if json.loads((run_dir / "provisional_deterministic_scores.json").read_text(encoding="utf-8")) != scores:
        raise RuntimeError("persisted deterministic score mismatch")
    result.update({
        "status": "AI_EVALUATION_VALID_PROVISIONAL", "failure_class": None,
        "schema_validation": "PASS", "semantic_validation": "PASS",
        "deterministic_scoring_run": True,
        "assessment_sha256": sha(run_dir / "ai_assessment.json"),
        "deterministic_score_sha256": sha(run_dir / "provisional_deterministic_scores.json"),
    })
    write_json(run_dir / "evaluation_summary.json", result)
    return result


def evaluate_one(row: dict, anonymous_id: str, gold: dict) -> dict:
    run_dir = OUTPUT_ROOT / "responses" / anonymous_id
    summary_path = run_dir / "evaluation_summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    if (run_dir / "model_run/metadata.json").exists():
        result = finalize(row, anonymous_id, run_dir, gold)
    elif (run_dir / "attempt_intent.json").exists():
        result = {
            "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"],
            "status": "AI_EVALUATION_INTERRUPTED_UNKNOWN", "failure_class": "AI_EVALUATION_FAILURE",
            "deterministic_scoring_run": False, "formal_ai_evaluation": True,
        }
        write_json(summary_path, result)
    else:
        package, prompt = build_inputs(row, anonymous_id, gold)
        write_json(run_dir / "anonymous_evaluation_package.json", package)
        write_json(run_dir / "attempt_intent.json", {
            "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"],
            "generation_output_sha256": row["effective_output_sha256"],
            "model": MODEL, "reasoning_effort": REASONING,
            "frozen_prompt_template_sha256": sha(P3H / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt"),
            "schema_sha256": sha(P3H / "phase3c_h_ai_evaluator_output_v2.schema.json"),
            "runtime_prompt_sha256": sha_bytes(prompt.encode()),
            "semantic_runtime_appendix_sha256": sha(HERE / "stage1_ai_evaluation/diagnostics/semantic_contract_runtime_appendix_v1_PROPOSED.txt"),
            "condition_visible_to_evaluator": False, "task_id_visible_to_evaluator": False,
        })
        RUNNER.run_codex_json(
            prompt=prompt, schema_path=P3H / "phase3c_h_ai_evaluator_output_v2.schema.json",
            model=MODEL, reasoning=REASONING, run_dir=run_dir / "model_run",
        )
        result = finalize(row, anonymous_id, run_dir, gold)
    with _lock:
        print(json.dumps({"anonymous_response_id": anonymous_id, "bug_id": row["bug_id"], "status": result["status"]}), flush=True)
    return result


def summarize(rows: list[dict], task_to_anon: dict[str, str]) -> dict:
    results = []
    for row in rows:
        path = OUTPUT_ROOT / "responses" / task_to_anon[row["task_id"]] / "evaluation_summary.json"
        if path.exists():
            results.append(json.loads(path.read_text(encoding="utf-8")))
    statuses = Counter(x["status"] for x in results)
    summary = {
        "status": "COMPLETE" if len(results) == len(rows) else "INCOMPLETE",
        "planned_evaluations": len(rows), "attempted_evaluations": len(results),
        "valid_evaluations": statuses["AI_EVALUATION_VALID_PROVISIONAL"],
        "failed_evaluations": len(results) - statuses["AI_EVALUATION_VALID_PROVISIONAL"],
        "remaining_evaluations": len(rows) - len(results),
        "status_counts": dict(sorted(statuses.items())),
        "stage1_manifest_sha256": sha(STAGE1_MANIFEST),
        "batch2_outputs_included": False, "generation_missing_task": "P3-025-B-R1",
        "generation_missing_treatment": "INFRASTRUCTURE_MISSING_NOT_ZERO",
        "model": MODEL, "reasoning_effort": REASONING,
        "formal_statistical_analysis_run": False, "score_locked": False, "arms_unblinded": False,
    }
    write_json(OUTPUT_ROOT / "evaluation_execution_summary.json", summary, overwrite_summary=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "run", "summarize"])
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    gate = preflight()
    if gate["status"] != "PASS":
        raise RuntimeError(json.dumps(gate))
    all_rows = jsonl(STAGE1_MANIFEST)
    rows = [row for row in all_rows if row["effective_status"] in VALID_GENERATION]
    task_to_anon, _ = load_or_create_mapping(rows)
    if args.command == "preflight":
        print(json.dumps(gate, indent=2, sort_keys=True))
        return
    if args.command == "run":
        gold = {row["bug_id"]: row for row in jsonl(FROZEN / "gold_v1.jsonl")}
        pending = [row for row in rows if not (OUTPUT_ROOT / "responses" / task_to_anon[row["task_id"]] / "evaluation_summary.json").exists()]
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="phase3d-stage1-eval") as pool:
            futures = [pool.submit(evaluate_one, row, task_to_anon[row["task_id"]], gold[row["bug_id"]]) for row in pending]
            for future in as_completed(futures):
                future.result()
    print(json.dumps(summarize(rows, task_to_anon), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
