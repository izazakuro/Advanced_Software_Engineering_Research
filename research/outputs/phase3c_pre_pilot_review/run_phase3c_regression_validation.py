#!/usr/bin/env python3
"""Run Phase 3C static/synthetic regression validation. No model calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
AB = RESEARCH / "outputs/phase3_ab_offline_preparation"
HANDOFF = RESEARCH / "phase2_11E_9_codex_handoff"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: list[str]) -> dict:
    result = subprocess.run(args, cwd=RESEARCH, text=True, capture_output=True)
    return {"command": args, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main() -> None:
    protected = [
        FROZEN / "gold_v1.jsonl", FROZEN / "gold_v1_freeze_manifest.json",
        HANDOFF / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl", HANDOFF / "approved_pairs_108.csv",
        AB / "phase3_generation_prompt_v1_PROPOSED.txt", AB / "phase3_generation_output.schema.json",
        AB / "phase3_formal_162_task_schedule.jsonl", AB / "phase3_pilot_18_task_schedule.jsonl",
    ]
    before = {str(x.relative_to(RESEARCH)): sha(x) for x in protected}
    generated = [
        HERE / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl",
        HERE / "phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl",
        HERE / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl",
        HERE / "phase3c_pilot_18_task_payloads_v2_NOT_AUTHORIZED.jsonl",
        HERE / "phase3c_v2_derivation_manifest.json",
    ]
    generated_before = {x.name: sha(x) for x in generated}
    commands = {
        "frozen_snapshot": run([sys.executable, "-B", "outputs/gold_v1_frozen/verify_frozen_snapshot.py", "outputs/gold_v1_frozen", "--check-only"]),
        "phase3ab_validation": run([sys.executable, "-B", "outputs/phase3_ab_offline_preparation/run_phase3_offline_validation.py"]),
        "phase3c_rebuild": run([sys.executable, "-B", str(HERE / "build_phase3c_artifacts.py")]),
        "phase3ab_tests": run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(AB / "tests"), "-v"]),
        "phase3c_tests": run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(HERE / "tests"), "-v"]),
        "formal_payloads": run([sys.executable, "-B", str(HERE / "phase3c_task_adapter.py"), str(HERE / "phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl")]),
        "pilot_payloads": run([sys.executable, "-B", str(HERE / "phase3c_task_adapter.py"), str(HERE / "phase3c_pilot_18_task_payloads_v2_NOT_AUTHORIZED.jsonl")]),
    }
    compile_errors = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, path in enumerate((HERE / "phase3c_offline_evaluator.py", HERE / "phase3c_task_adapter.py", HERE / "build_phase3c_artifacts.py", HERE / "run_phase3c_regression_validation.py")):
            try:
                py_compile.compile(str(path), cfile=str(Path(tmp) / f"{index}.pyc"), doraise=True)
            except py_compile.PyCompileError as exc:
                compile_errors.append(str(exc))
    after = {str(x.relative_to(RESEARCH)): sha(x) for x in protected}
    generated_after = {x.name: sha(x) for x in generated}
    formal = [json.loads(x) for x in generated[0].read_text().splitlines() if x]
    pilot = [json.loads(x) for x in generated[2].read_text().splitlines() if x]
    integrity = json.loads((AB / "phase3_approved_context_integrity.json").read_text())
    derivation = json.loads((HERE / "phase3c_v2_derivation_manifest.json").read_text())
    output_schema = json.loads((HERE / "phase3_generation_output_v2_PROPOSED.schema.json").read_text())
    review_schema = json.loads((HERE / "phase3c_human_review_record.schema.json").read_text())
    source = "\n".join((HERE / name).read_text() for name in ("phase3c_offline_evaluator.py", "phase3c_task_adapter.py"))
    checks = {
        "frozen_snapshot_pass": commands["frozen_snapshot"]["returncode"] == 0 and "PASS_GOLD_V1_FROZEN_SNAPSHOT" in commands["frozen_snapshot"]["stdout"],
        "protected_inputs_unchanged": before == after,
        "phase3ab_regression_pass": commands["phase3ab_validation"]["returncode"] == 0,
        "phase3c_deterministic_rebuild": generated_before == generated_after,
        "phase3ab_tests_pass_12": commands["phase3ab_tests"]["returncode"] == 0 and "Ran 12 tests" in commands["phase3ab_tests"]["stderr"],
        "phase3c_tests_pass_13": commands["phase3c_tests"]["returncode"] == 0 and "Ran 13 tests" in commands["phase3c_tests"]["stderr"],
        "python_compilation_pass": not compile_errors,
        "context_integrity_108": integrity["status"] == "PASS" and integrity["approved_total_assignments"] == 108,
        "formal_schedule_162": len(formal) == 162 and len({x["task_id"] for x in formal}) == 162,
        "pilot_schedule_18": len(pilot) == 18 and len({x["target_bug_id"] for x in pilot}) == 6,
        "formal_payloads_fail_closed_pass": commands["formal_payloads"]["returncode"] == 0,
        "pilot_payloads_fail_closed_pass": commands["pilot_payloads"]["returncode"] == 0,
        "schema_exact_s1_s2_s3": output_schema["properties"]["scenarios"]["items"] is False and len(output_schema["properties"]["scenarios"]["prefixItems"]) == 3,
        "review_schema_blinded": "condition" not in review_schema["properties"],
        "no_design_or_context_changes": derivation["formal_task_identity_or_order_changes"] == 0 and derivation["pilot_task_identity_or_order_changes"] == 0 and derivation["context_assignment_changes"] == 0,
        "prompt_hashes_versioned": derivation["formal_rendered_prompt_hash_changes"] == 162 and derivation["pilot_rendered_prompt_hash_changes"] == 18,
        "all_revised_tasks_unauthorized": all(x["execution_status"] == "VERSIONED_V2_PLANNED_NOT_AUTHORIZED" for x in formal) and all(x["execution_status"] == "VERSIONED_V2_ENGINEERING_PILOT_NOT_AUTHORIZED" for x in pilot),
        "no_model_or_bm25_implementation": "subprocess" not in source and "rank_bm25" not in source and "openai" not in source.casefold(),
    }
    results = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks, "commands": commands, "compile_errors": compile_errors,
        "protected_hashes": after, "generated_hashes": generated_after,
        "official_model_calls": 0, "llm_judge_calls": 0, "bm25_indexes_built": 0,
        "pilot_executed": False, "formal_experiment_executed": False,
    }
    (HERE / "phase3c_validation_results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": results["status"], "checks": checks}, indent=2))
    if results["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
