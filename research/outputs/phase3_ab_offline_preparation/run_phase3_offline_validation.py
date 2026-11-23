#!/usr/bin/env python3
"""Run deterministic Phase 3 offline validations and write machine-readable results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import py_compile
import subprocess
import sys


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
FROZEN = RESEARCH / "outputs/gold_v1_frozen"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=RESEARCH, text=True, capture_output=True)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main() -> None:
    frozen_files = sorted(path for path in FROZEN.rglob("*") if path.is_file())
    frozen_before = {str(path.relative_to(RESEARCH)): sha(path) for path in frozen_files}
    generated = [
        HERE / "phase3_approved_context_integrity.json",
        HERE / "phase3_formal_162_task_schedule.jsonl",
        HERE / "phase3_pilot_18_task_schedule.jsonl",
        HERE / "phase3_formal_162_task_payloads_NOT_AUTHORIZED.jsonl",
        HERE / "phase3_pilot_18_task_payloads_NOT_AUTHORIZED.jsonl",
        HERE / "phase3_mock_fixtures.jsonl",
        HERE / "phase3_build_metadata.json",
    ]
    generated_before = {path.name: sha(path) for path in generated}
    commands = {
        "frozen_snapshot": run([sys.executable, "-B", "outputs/gold_v1_frozen/verify_frozen_snapshot.py", "outputs/gold_v1_frozen", "--check-only"]),
        "rebuild": run([sys.executable, "-B", str(HERE / "build_phase3_offline_artifacts.py")]),
        "unit_tests": run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(HERE / "tests"), "-v"]),
        "formal_payload_validation": run([sys.executable, "-B", str(HERE / "phase3_codex_task_adapter.py"), "validate-manifest", str(HERE / "phase3_formal_162_task_payloads_NOT_AUTHORIZED.jsonl")]),
        "pilot_payload_validation": run([sys.executable, "-B", str(HERE / "phase3_codex_task_adapter.py"), "validate-manifest", str(HERE / "phase3_pilot_18_task_payloads_NOT_AUTHORIZED.jsonl")]),
    }
    compile_errors = []
    for path in (HERE / "phase3_codex_task_adapter.py", HERE / "phase3_offline_evaluator.py", HERE / "build_phase3_offline_artifacts.py", HERE / "run_phase3_offline_validation.py"):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_errors.append(str(exc))
    frozen_after = {str(path.relative_to(RESEARCH)): sha(path) for path in frozen_files}
    generated_after = {path.name: sha(path) for path in generated}
    schema = json.loads((HERE / "phase3_generation_output.schema.json").read_text(encoding="utf-8"))
    integrity = json.loads((HERE / "phase3_approved_context_integrity.json").read_text(encoding="utf-8"))
    formal = [json.loads(x) for x in (HERE / "phase3_formal_162_task_schedule.jsonl").read_text().splitlines() if x]
    pilot = [json.loads(x) for x in (HERE / "phase3_pilot_18_task_schedule.jsonl").read_text().splitlines() if x]
    source_text = "\n".join((HERE / name).read_text(encoding="utf-8") for name in ("phase3_codex_task_adapter.py", "phase3_offline_evaluator.py"))
    checks = {
        "frozen_snapshot_integrity": commands["frozen_snapshot"]["returncode"] == 0 and "PASS_GOLD_V1_FROZEN_SNAPSHOT" in commands["frozen_snapshot"]["stdout"],
        "frozen_files_unchanged": frozen_before == frozen_after,
        "deterministic_rebuild": generated_before == generated_after,
        "unit_tests_pass": commands["unit_tests"]["returncode"] == 0,
        "python_compilation_pass": not compile_errors,
        "formal_payloads_pass": commands["formal_payload_validation"]["returncode"] == 0,
        "pilot_payloads_pass": commands["pilot_payload_validation"]["returncode"] == 0,
        "context_integrity_pass": integrity["status"] == "PASS" and integrity["approved_total_assignments"] == 108,
        "formal_schedule_162": len(formal) == 162 and len({x["task_id"] for x in formal}) == 162,
        "pilot_schedule_18": len(pilot) == 18 and len({x["target_bug_id"] for x in pilot}) == 6,
        "schema_declares_exact_three": schema["properties"]["scenarios"]["minItems"] == 3 and schema["properties"]["scenarios"]["maxItems"] == 3,
        "no_model_api_implementation": "openai" not in source_text.casefold() and "codex exec" not in source_text.casefold(),
        "no_bm25_index_implementation": "create index" not in source_text.casefold() and "rank_bm25" not in source_text.casefold(),
        "all_execution_unauthorized": all(x["execution_status"] == "PLANNED_NOT_AUTHORIZED" for x in formal) and all(x["execution_status"] == "ENGINEERING_PILOT_NOT_AUTHORIZED" for x in pilot),
    }
    results = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "commands": commands,
        "compile_errors": compile_errors,
        "frozen_gold_sha256": sha(FROZEN / "gold_v1.jsonl"),
        "frozen_manifest_sha256": sha(FROZEN / "gold_v1_freeze_manifest.json"),
        "official_model_calls": 0,
        "llm_judge_calls": 0,
        "bm25_indexes_built": 0,
        "generated_artifact_hashes": generated_after,
    }
    (HERE / "phase3_validation_results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": results["status"], "checks": checks}, indent=2))
    if results["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
