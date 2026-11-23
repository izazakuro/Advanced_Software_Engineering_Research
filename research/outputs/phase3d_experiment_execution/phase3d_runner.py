#!/usr/bin/env python3
"""Checkpointed Phase 3D generation/evaluation runner.

Real model calls occur only for explicit `smoke`, `generate`, or `evaluate` commands.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
P3C = RESEARCH / "outputs/phase3c_pre_pilot_review"
P3H = RESEARCH / "outputs/phase3c_h_ai_first_amendment"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
FORMAL_PAYLOADS = P3C / "phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl"
GEN_SCHEMA = P3C / "phase3_generation_output_v2_PROPOSED.schema.json"
EVAL_SCHEMA = P3H / "phase3c_h_ai_evaluator_output_v2.schema.json"
EVAL_PROMPT = P3H / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt"
RUBRIC = RESEARCH / "reports/test_scenario_evaluation_rubric_abc_PROPOSED.md"
CODEX = shutil.which("codex") or "/Users/zakuro/.vscode/extensions/openai.chatgpt-26.1106.51428-darwin-arm64/bin/macos-aarch64/codex"
GEN_MODEL = os.environ.get("PHASE3D_GENERATION_MODEL", "gpt-6.2-sol")
EVAL_MODEL = os.environ.get("PHASE3D_EVALUATION_MODEL", "gpt-6.2-astra")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


P3C_EVAL = load_module("phase3d_p3c_eval", P3C / "phase3c_offline_evaluator.py")
P3H_PIPE = load_module("phase3d_p3h_pipe", P3H / "phase3c_h_evaluation_pipeline.py")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite checkpoint: {path}")
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def safe_environment() -> dict[str, str]:
    keep = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TERM", "SSL_CERT_FILE", "SSL_CERT_DIR", "NO_PROXY", "HTTPS_PROXY", "HTTP_PROXY"}
    return {key: value for key, value in os.environ.items() if key in keep}


def codex_version() -> str:
    result = subprocess.run([CODEX, "--version"], text=True, capture_output=True, env=safe_environment(), timeout=15)
    return (result.stdout or result.stderr).strip().splitlines()[-1]


def run_codex_json(*, prompt: str, schema_path: Path, model: str, reasoning: str, run_dir: Path, timeout: int = 900) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace_parent = Path(tempfile.mkdtemp(prefix="ossgb-phase3d-"))
    workspace = workspace_parent / "workspace"
    workspace.mkdir(mode=0o700)
    prompt_path = workspace / "PROMPT.txt"
    local_schema = workspace / "OUTPUT_SCHEMA.json"
    answer_path = workspace / "answer.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    shutil.copyfile(schema_path, local_schema)
    command = [
        CODEX, "exec", "-m", model, "-s", "read-only", "--ephemeral",
        "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "-C", str(workspace), "-c", f'model_reasoning_effort="{reasoning}"',
        "-c", "shell_environment_policy.inherit=none",
        "--json", "-o", str(answer_path), "-",
    ]
    started = utcnow()
    before = sorted(x.name for x in workspace.iterdir())
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=safe_environment(), start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout)
        result = subprocess.CompletedProcess(command, process.returncode, stdout or "", stderr or "")
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        partial_stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        partial_stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result = subprocess.CompletedProcess(
            command, 124, (partial_stdout + (stdout or "")),
            (partial_stderr + (stderr or "") + f"\nTIMEOUT_AFTER_{timeout}_SECONDS"),
        )
    completed = utcnow()
    raw = answer_path.read_bytes() if answer_path.is_file() else b""
    after = sorted(x.name for x in workspace.iterdir())
    metadata = {
        "command_redacted": [x if x != prompt else "<PROMPT>" for x in command],
        "cli_version": codex_version(), "model_identifier_requested": model,
        "model_version": "unavailable", "reasoning_effort": reasoning,
        "start_timestamp": started, "completion_timestamp": completed,
        "returncode": result.returncode, "timed_out": timed_out,
        "prompt_sha256": sha_bytes(prompt.encode()), "schema_sha256": sha(schema_path),
        "schema_enforced_by_cli": False,
        "schema_enforcement_note": "Approved Schema v2 is validated locally because current strict Codex response_format rejects items:false.",
        "response_sha256": sha_bytes(raw) if raw else None,
        "workspace_files_before": before, "workspace_files_after": after,
        "workspace_is_disposable": True, "workspace_cleanup_attempted": True,
    }
    atomic_write(run_dir / "events.jsonl", (result.stdout or "").encode())
    atomic_write(run_dir / "stderr.txt", (result.stderr or "").encode())
    atomic_write(run_dir / "raw_response.json", raw)
    atomic_write(run_dir / "metadata.json", (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode())
    shutil.rmtree(workspace_parent, ignore_errors=True)
    return {"metadata": metadata, "raw": raw, "returncode": result.returncode, "stderr": result.stderr or ""}


def load_payloads() -> list[dict]:
    rows = jsonl(FORMAL_PAYLOADS)
    if len(rows) != 162:
        raise ValueError("formal payload count is not 162")
    return rows


def load_gold() -> dict[str, dict]:
    return {row["bug_id"]: row for row in jsonl(FROZEN / "gold_v1.jsonl")}


def smoke() -> dict:
    payload = next(row for row in load_payloads() if row["bug_id"] == "flame-engine/flame#1003" and row["condition"] == "A" and row["repeat_index"] == 1)
    smoke_id = "SMOKE-" + payload["task_id"]
    gen_base = HERE / "generation_outputs" / smoke_id
    attempt = 1
    while (gen_base / "metadata.json").is_file() if attempt == 1 else (gen_base / f"attempt-{attempt}" / "metadata.json").is_file():
        attempt += 1
    gen_dir = gen_base if attempt == 1 else gen_base / f"attempt-{attempt}"
    generation = run_codex_json(prompt=payload["rendered_prompt"], schema_path=GEN_SCHEMA, model=GEN_MODEL, reasoning="low", run_dir=gen_dir)
    gen_check = P3C_EVAL.classify_attempt(
        invocation_started=True, invocation_completed=generation["returncode"] == 0,
        raw=generation["raw"], expected_bug_id=payload["bug_id"],
        process_error=generation["stderr"],
    )
    atomic_write(gen_dir / "validation.json", (json.dumps(gen_check, indent=2, sort_keys=True) + "\n").encode())
    summary = {"smoke_id": smoke_id, "attempt_index": attempt, "generation_status": gen_check["status"], "generation_model": GEN_MODEL}
    if gen_check["status"] not in {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}:
        summary["status"] = "SMOKE_GENERATION_FAILED"
        atomic_write(HERE / f"smoke_summary_attempt_{attempt}.json", (json.dumps(summary, indent=2) + "\n").encode())
        return summary
    generated = gen_check["parsed"]
    gold = load_gold()[payload["bug_id"]]
    rubric_text = RUBRIC.read_text(encoding="utf-8") + "\n\n" + (P3H / "phase3c_h_primary_endpoint_specification.md").read_text(encoding="utf-8")
    package = P3H_PIPE.build_anonymous_evaluation_package("AR-SMOKE-001", generated, gold, rubric_text)
    atomic_write(HERE / "ai_evaluation_outputs" / smoke_id / "anonymous_package.json", (json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    template = EVAL_PROMPT.read_text(encoding="utf-8")
    eval_prompt = (template.replace("{{ANONYMOUS_RESPONSE_ID}}", package["anonymous_response_id"])
                   .replace("{{BUG_ID}}", package["bug_id"])
                   .replace("{{GENERATED_SCENARIOS_JSON}}", json.dumps(package["generated_scenarios_untrusted_data"], ensure_ascii=False))
                   .replace("{{FROZEN_GOLD_EVALUATION_JSON}}", json.dumps(package["frozen_gold_evaluation_projection"], ensure_ascii=False))
                   .replace("{{APPROVED_RUBRIC_TEXT}}", package["approved_rubric_text"]))
    eval_dir = HERE / "ai_evaluation_outputs" / smoke_id / f"generation-attempt-{attempt}"
    evaluation = run_codex_json(prompt=eval_prompt, schema_path=EVAL_SCHEMA, model=EVAL_MODEL, reasoning="medium", run_dir=eval_dir / "model_run")
    parsed = P3H_PIPE.parse_ai_assessment(evaluation["raw"])
    if parsed["status"] != "PARSED_PENDING_VALIDATION":
        summary.update({"status": "SMOKE_AI_EVALUATION_PARSE_FAILED", "evaluation_status": parsed["status"], "evaluation_model": EVAL_MODEL})
    else:
        assessment = parsed["assessment"]
        errors = P3H_PIPE.validate_ai_assessment(assessment, P3H_PIPE.load_eligibility(), P3H_PIPE.gold_requirement_catalog(gold))
        if errors:
            summary.update({"status": "SMOKE_AI_EVALUATION_VALIDATION_FAILED", "evaluation_status": "INVALID", "evaluation_errors": errors, "evaluation_model": EVAL_MODEL})
        else:
            scores = P3H_PIPE.deterministic_scores(assessment, P3H_PIPE.load_eligibility(), P3H_PIPE.gold_requirement_catalog(gold))
            atomic_write(eval_dir / "provisional_deterministic_scores.json", (json.dumps(scores, indent=2, sort_keys=True) + "\n").encode())
            summary.update({"status": "SMOKE_PASS_NOT_FORMAL", "evaluation_status": "VALID", "evaluation_model": EVAL_MODEL, "provisional_only": True})
    atomic_write(eval_dir / "assessment_parse_validation.json", (json.dumps(parsed, indent=2, sort_keys=True) + "\n").encode())
    atomic_write(HERE / f"smoke_summary_attempt_{attempt}.json", (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["smoke"])
    args = parser.parse_args()
    if args.command == "smoke":
        print(json.dumps(smoke(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
