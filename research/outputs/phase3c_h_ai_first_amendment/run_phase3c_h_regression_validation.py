#!/usr/bin/env python3
"""Regression validation for the Phase 3C-H offline amendment."""

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
C = RESEARCH / "outputs/phase3c_pre_pilot_review"
HANDOFF = RESEARCH / "phase2_11E_9_codex_handoff"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: list[str]) -> dict:
    result = subprocess.run(args, cwd=RESEARCH, text=True, capture_output=True)
    return {"command": args, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main() -> None:
    protected_expected = {
        FROZEN / "gold_v1.jsonl": "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66",
        FROZEN / "gold_v1_freeze_manifest.json": "0886571b6390ab461b1f72ee43ff4628c39cfc76275378207b0546026c51abdc",
        HANDOFF / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl": "a4d145121d623ffb5f51fc717c5f5dc0d1af2c7b517665ccfd1cc3eb33e40e17",
        HANDOFF / "approved_pairs_108.csv": "eda8fa6001fd800d31aa086bdaa6edf79ec753ce851310fdc4ab7e87df593d5f",
        C / "phase3_generation_prompt_v2_PROPOSED.txt": "cc1f9373e275543281ccd0b6d9bff7dc2b1d84d855c059867fa0791228598f5c",
        C / "phase3_generation_output_v2_PROPOSED.schema.json": "e63d1d66cab758e83be279037437e0582e17392ffdb44fc0b317c296c9e5fd8e",
        C / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl": "a3efd162269a1cdd1878460bd6c7376af9dbfff97e781a79ee6d15fbac0e7c98",
        C / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl": "f07ab0dfd2833baeef909e990778bf8ba9c35a894c3be10df10620cd87f2201f",
    }
    protected_hashes = {str(path.relative_to(RESEARCH)): sha(path) for path in protected_expected}
    commands = {
        "frozen": run([sys.executable, "-B", "outputs/gold_v1_frozen/verify_frozen_snapshot.py", "outputs/gold_v1_frozen", "--check-only"]),
        "phase3ab": run([sys.executable, "-B", "outputs/phase3_ab_offline_preparation/run_phase3_offline_validation.py"]),
        "phase3c": run([sys.executable, "-B", "outputs/phase3c_pre_pilot_review/run_phase3c_regression_validation.py"]),
        "phase3ab_tests": run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(AB / "tests"), "-v"]),
        "phase3c_tests": run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(C / "tests"), "-v"]),
        "phase3ch_tests": run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(HERE / "tests"), "-v"]),
    }
    compile_errors = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, path in enumerate((HERE / "phase3c_h_evaluation_pipeline.py", HERE / "build_phase3c_h_queue_plans.py", HERE / "run_phase3c_h_regression_validation.py")):
            try:
                py_compile.compile(str(path), cfile=str(Path(tmp) / f"{index}.pyc"), doraise=True)
            except py_compile.PyCompileError as exc:
                compile_errors.append(str(exc))
    eligibility = json.loads((FROZEN / "gold_v1_metric_eligibility.json").read_text())
    scopes = eligibility["frozen_metric_scopes"]
    context = json.loads((AB / "phase3_approved_context_integrity.json").read_text())
    formal = [json.loads(x) for x in (C / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl").read_text().splitlines() if x]
    pilot = [json.loads(x) for x in (C / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl").read_text().splitlines() if x]
    fixtures = [json.loads(x) for x in (HERE / "phase3c_h_synthetic_evaluation_fixtures.jsonl").read_text().splitlines() if x]
    formal_queue = [json.loads(x) for x in (HERE / "phase3c_h_formal_cspc_queue_plan_108_RESTRICTED.jsonl").read_text().splitlines() if x]
    pilot_queue = [json.loads(x) for x in (HERE / "phase3c_h_pilot_cspc_queue_plan_15_RESTRICTED.jsonl").read_text().splitlines() if x]
    eval_schema = json.loads((HERE / "phase3c_h_ai_evaluator_output_v2.schema.json").read_text())
    pipeline_source = (HERE / "phase3c_h_evaluation_pipeline.py").read_text().casefold()
    checks = {
        "frozen_verifier_pass": commands["frozen"]["returncode"] == 0 and "PASS_GOLD_V1_FROZEN_SNAPSHOT" in commands["frozen"]["stdout"],
        "protected_hashes_exact": all(sha(path) == expected for path, expected in protected_expected.items()),
        "phase3ab_regression_pass": commands["phase3ab"]["returncode"] == 0,
        "phase3c_regression_pass": commands["phase3c"]["returncode"] == 0,
        "phase3ab_tests_12": commands["phase3ab_tests"]["returncode"] == 0 and "Ran 12 tests" in commands["phase3ab_tests"]["stderr"],
        "phase3c_tests_13": commands["phase3c_tests"]["returncode"] == 0 and "Ran 13 tests" in commands["phase3c_tests"]["stderr"],
        "phase3ch_tests_19": commands["phase3ch_tests"]["returncode"] == 0 and "Ran 19 tests" in commands["phase3ch_tests"]["stderr"],
        "python_compile_pass": not compile_errors,
        "contexts_108_unchanged": context["status"] == "PASS" and context["approved_total_assignments"] == 108,
        "abc_and_schedules_unchanged": len(formal) == 162 and len(pilot) == 18 and {x["condition"] for x in formal} == {"A", "B", "C"},
        "cspc_scope_unchanged": (scopes["cspc_bugs"], scopes["cspc_paths"]) == (11, 12),
        "tcc_scope_unchanged": (scopes["tcc_bugs"], scopes["tcc_paths"], scopes["tcc_required_condition_denominator"]) == (18, 19, 24),
        "oracle_scope_unchanged": (scopes["strict_oracle_entries"], scopes["strict_oracle_bugs"], scopes["strict_pending_excluded"], scopes["strict_ineligible_incomplete_excluded"]) == (10, 9, 2, 7),
        "ai_schema_path_level": eval_schema["properties"]["cspc_path_assessments"]["type"] == "array",
        "synthetic_fixture_count_30": len(fixtures) == 30 and all(x["synthetic"] for x in fixtures),
        "primary_queue_blueprints_exact": len(formal_queue) == 108 and len(pilot_queue) == 15 and all("condition" not in x for x in formal_queue + pilot_queue),
        "no_real_model_implementation": "subprocess" not in pipeline_source and "openai" not in pipeline_source and "codex exec" not in pipeline_source,
        "all_generation_tasks_unauthorized": all("NOT_AUTHORIZED" in x["execution_status"] for x in formal + pilot),
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "commands": commands, "compile_errors": compile_errors,
        "protected_hashes": protected_hashes,
        "ai_evaluator_prompt_sha256": sha(HERE / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt"),
        "ai_evaluator_schema_sha256": sha(HERE / "phase3c_h_ai_evaluator_output_v2.schema.json"),
        "official_generation_calls": 0, "real_ai_evaluator_calls": 0, "llm_judge_calls": 0,
        "pilot_executed": False, "formal_experiment_executed": False, "arms_unblinded": False,
    }
    (HERE / "phase3c_h_validation_results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "checks": checks}, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
