#!/usr/bin/env python3
"""One-shot evaluator-only smoke retry with local Schema v2 enforcement."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
P3H = RESEARCH / "outputs/phase3c_h_ai_first_amendment"
P3C = RESEARCH / "outputs/phase3c_pre_pilot_review"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
SCHEMA_PATH = P3H / "phase3c_h_ai_evaluator_output_v2.schema.json"
PROMPT_PATH = P3H / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt"
GENERATION_DIR = HERE / "generation_outputs/SMOKE-P3-004-A-R1/attempt-3"
PREVIOUS_EVALUATION_DIR = HERE / "ai_evaluation_outputs/SMOKE-P3-004-A-R1/generation-attempt-3/model_run"
RETRY_DIR = HERE / "ai_evaluation_outputs/SMOKE-P3-004-A-R1/generation-attempt-3/evaluator-only-retry-1"
OLD_AUDIT_PATH = HERE / "evaluator_only_retry_old_output_schema_audit.json"
SUMMARY_PATH = HERE / "evaluator_only_retry_summary.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_module("phase3d_retry_runner", HERE / "phase3d_runner.py")
PIPE = load_module("phase3d_retry_pipeline", P3H / "phase3c_h_evaluation_pipeline.py")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def path_join(path: str, token: str | int) -> str:
    if isinstance(token, int):
        return f"{path}[{token}]"
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def resolve_ref(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported external schema reference: {ref}")
    value: Any = root
    for token in ref[2:].split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise TypeError(f"schema reference is not an object: {ref}")
    return value


def error(path: str, keyword: str, message: str, *, expected: Any = None, actual: Any = None) -> dict:
    result = {"json_path": path, "keyword": keyword, "message": message}
    if expected is not None:
        result["expected"] = expected
    if actual is not None:
        result["actual"] = actual
    return result


def validate_schema(instance: Any, schema: dict, root: dict, path: str = "$") -> list[dict]:
    """Validate every assertion keyword used by the frozen evaluator Schema v2."""
    errors: list[dict] = []
    if "$ref" in schema:
        errors.extend(validate_schema(instance, resolve_ref(root, schema["$ref"]), root, path))
    for subschema in schema.get("allOf", []):
        errors.extend(validate_schema(instance, subschema, root, path))

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = [expected_type] if isinstance(expected_type, str) else expected_type
        actual = json_type(instance)
        type_ok = actual in allowed or (actual == "integer" and "number" in allowed)
        if not type_ok:
            return errors + [error(path, "type", "type mismatch", expected=allowed, actual=actual)]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(error(path, "enum", "value is not in allowed enum", expected=schema["enum"], actual=instance))
    if "const" in schema and instance != schema["const"]:
        errors.append(error(path, "const", "value differs from required constant", expected=schema["const"], actual=instance))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = sorted(set(required) - set(instance))
        for key in missing:
            errors.append(error(path_join(path, key), "required", "required key is missing", expected="present", actual="missing"))
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in sorted(set(instance) - set(properties)):
                errors.append(error(path_join(path, key), "additionalProperties", "unexpected key is forbidden", expected="absent", actual="present"))
        for key, subschema in properties.items():
            if key in instance:
                errors.extend(validate_schema(instance[key], subschema, root, path_join(path, key)))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(error(path, "minItems", "array has too few items", expected=schema["minItems"], actual=len(instance)))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(error(path, "maxItems", "array has too many items", expected=schema["maxItems"], actual=len(instance)))
        if schema.get("uniqueItems"):
            fingerprints = [json.dumps(x, sort_keys=True, ensure_ascii=False) for x in instance]
            if len(set(fingerprints)) != len(fingerprints):
                errors.append(error(path, "uniqueItems", "array items are not unique", expected="unique", actual="duplicates present"))
        prefix = schema.get("prefixItems", [])
        for index, subschema in enumerate(prefix[: len(instance)]):
            errors.extend(validate_schema(instance[index], subschema, root, path_join(path, index)))
        items = schema.get("items")
        start = len(prefix) if prefix else 0
        if items is False and len(instance) > len(prefix):
            for index in range(len(prefix), len(instance)):
                errors.append(error(path_join(path, index), "items", "additional array item is forbidden", expected="absent", actual=json_type(instance[index])))
        elif isinstance(items, dict):
            for index in range(start, len(instance)):
                errors.extend(validate_schema(instance[index], items, root, path_join(path, index)))

    if isinstance(instance, str) and "minLength" in schema and len(instance) < schema["minLength"]:
        errors.append(error(path, "minLength", "string is too short", expected=schema["minLength"], actual=len(instance)))
    if isinstance(instance, str) and "pattern" in schema and re.search(schema["pattern"], instance) is None:
        errors.append(error(path, "pattern", "string does not match required pattern", expected=schema["pattern"], actual=instance))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool) and "minimum" in schema and instance < schema["minimum"]:
        errors.append(error(path, "minimum", "number is below minimum", expected=schema["minimum"], actual=instance))
    return errors


def write_new(path: Path, value: dict) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    RUNNER.atomic_write(path, data)


def audit_previous() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    raw = (PREVIOUS_EVALUATION_DIR / "raw_response.json").read_bytes()
    previous = json.loads(raw.decode("utf-8"))
    errors = validate_schema(previous, schema, schema)
    result = {
        "status": "PREVIOUS_OUTPUT_INVALID",
        "schema_path": str(SCHEMA_PATH),
        "schema_sha256": sha(SCHEMA_PATH),
        "previous_output_path": str(PREVIOUS_EVALUATION_DIR / "raw_response.json"),
        "previous_output_sha256": sha_bytes(raw),
        "error_count": len(errors),
        "errors": errors,
    }
    write_new(OLD_AUDIT_PATH, result)
    return result


def build_base_prompt() -> tuple[str, dict, dict]:
    generation_raw = (GENERATION_DIR / "raw_response.json").read_bytes()
    generation_validation = json.loads((GENERATION_DIR / "validation.json").read_text(encoding="utf-8"))
    if generation_validation.get("status") != "STRUCTURALLY_VALID":
        raise RuntimeError("preserved smoke generation is not structurally valid")
    if generation_validation.get("response_sha256") != sha_bytes(generation_raw):
        raise RuntimeError("preserved smoke generation hash mismatch")
    generated = json.loads(generation_raw.decode("utf-8"))
    gold = next(row for row in RUNNER.load_gold().values() if row["bug_id"] == generated["bug_id"])
    rubric = (RUNNER.RUBRIC.read_text(encoding="utf-8") + "\n\n" +
              (P3H / "phase3c_h_primary_endpoint_specification.md").read_text(encoding="utf-8"))
    package = PIPE.build_anonymous_evaluation_package("AR-SMOKE-001", generated, gold, rubric)
    template = PROMPT_PATH.read_text(encoding="utf-8")
    base = (template.replace("{{ANONYMOUS_RESPONSE_ID}}", package["anonymous_response_id"])
            .replace("{{BUG_ID}}", package["bug_id"])
            .replace("{{GENERATED_SCENARIOS_JSON}}", json.dumps(package["generated_scenarios_untrusted_data"], ensure_ascii=False))
            .replace("{{FROZEN_GOLD_EVALUATION_JSON}}", json.dumps(package["frozen_gold_evaluation_projection"], ensure_ascii=False))
            .replace("{{APPROVED_RUBRIC_TEXT}}", package["approved_rubric_text"]))
    return base, package, gold


def runtime_prompt(base: str, schema_text: str, schema: dict) -> str:
    exact_keys = json.dumps(sorted(schema["properties"]), ensure_ascii=False)
    appendix = (
        "\n\nRUNTIME-ONLY SCHEMA ENFORCEMENT (the frozen prompt and schema files are unchanged)\n"
        "Return exactly one JSON object and no other text. The object MUST contain exactly these top-level keys, "
        f"with no missing or additional keys: {exact_keys}. "
        "Every nested object and array MUST conform exactly to the complete Schema v2 below, including required keys, "
        "types, enum values, array order/length, and additionalProperties=false.\n"
        "BEGIN COMPLETE UNCHANGED EVALUATOR SCHEMA V2\n"
        + schema_text +
        "\nEND COMPLETE UNCHANGED EVALUATOR SCHEMA V2\n"
    )
    return base + appendix


def run_once() -> dict:
    if (RETRY_DIR / "model_run/metadata.json").exists() or SUMMARY_PATH.exists():
        raise RuntimeError("evaluator-only retry has already been attempted; refusing an additional model call")
    old = audit_previous()
    if old["previous_output_sha256"] != "885411b50784fcb4ffe24f8bb7241047cbcfc774faf23fa6d8e2f5a3e2506c50":
        raise RuntimeError("previous invalid evaluator output changed")
    previous_meta = json.loads((PREVIOUS_EVALUATION_DIR / "metadata.json").read_text(encoding="utf-8"))
    base, package, gold = build_base_prompt()
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    schema = json.loads(schema_text)
    prompt = runtime_prompt(base, schema_text, schema)
    model = previous_meta["model_identifier_requested"]
    reasoning = previous_meta["reasoning_effort"]
    evaluation = RUNNER.run_codex_json(
        prompt=prompt,
        schema_path=SCHEMA_PATH,
        model=model,
        reasoning=reasoning,
        run_dir=RETRY_DIR / "model_run",
    )
    raw = evaluation["raw"]
    summary: dict[str, Any] = {
        "status": "EVALUATOR_ONLY_RETRY_FAILED",
        "model_identifier_requested": model,
        "reasoning_effort": reasoning,
        "generation_reused": True,
        "generation_response_sha256": sha(GENERATION_DIR / "raw_response.json"),
        "frozen_prompt_file_sha256": sha(PROMPT_PATH),
        "frozen_schema_sha256": sha(SCHEMA_PATH),
        "runtime_prompt_sha256": sha_bytes(prompt.encode()),
        "cli_output_schema_used": False,
        "returncode": evaluation["returncode"],
        "raw_response_sha256": sha_bytes(raw) if raw else None,
        "deterministic_scoring_run": False,
    }
    if evaluation["returncode"] != 0:
        summary["failure_stage"] = "MODEL_INVOCATION"
        summary["validation_errors"] = []
        write_new(SUMMARY_PATH, summary)
        return summary
    parsed = PIPE.parse_ai_assessment(raw)
    if parsed["status"] != "PARSED_PENDING_VALIDATION":
        validation = {"status": "INVALID_JSON", "errors": [{"json_path": "$", "keyword": "parse", "message": parsed["error"]}]}
        write_new(RETRY_DIR / "schema_validation.json", validation)
        summary.update({"failure_stage": "JSON_PARSE", "validation_errors": validation["errors"]})
        write_new(SUMMARY_PATH, summary)
        return summary
    assessment = parsed["assessment"]
    structural_errors = validate_schema(assessment, schema, schema)
    validation = {
        "status": "SCHEMA_VALID" if not structural_errors else "SCHEMA_INVALID",
        "schema_sha256": sha(SCHEMA_PATH),
        "raw_response_sha256": sha_bytes(raw),
        "error_count": len(structural_errors),
        "errors": structural_errors,
    }
    write_new(RETRY_DIR / "schema_validation.json", validation)
    if structural_errors:
        summary.update({"failure_stage": "SCHEMA_VALIDATION", "validation_errors": structural_errors})
        write_new(SUMMARY_PATH, summary)
        return summary
    semantic_errors = PIPE.validate_ai_assessment(assessment, PIPE.load_eligibility(), PIPE.gold_requirement_catalog(gold))
    write_new(RETRY_DIR / "semantic_validation.json", {
        "status": "SEMANTIC_VALID" if not semantic_errors else "SEMANTIC_INVALID",
        "errors": semantic_errors,
    })
    if semantic_errors:
        summary.update({"failure_stage": "SEMANTIC_VALIDATION", "validation_errors": semantic_errors})
        write_new(SUMMARY_PATH, summary)
        return summary
    scores = PIPE.deterministic_scores(assessment, PIPE.load_eligibility(), PIPE.gold_requirement_catalog(gold))
    write_new(RETRY_DIR / "provisional_deterministic_scores.json", scores)
    # Verify the persisted score record is exactly reproducible.
    persisted = json.loads((RETRY_DIR / "provisional_deterministic_scores.json").read_text(encoding="utf-8"))
    if persisted != scores or persisted["anonymous_response_id"] != package["anonymous_response_id"] or persisted["bug_id"] != package["bug_id"]:
        raise RuntimeError("deterministic score persistence verification failed")
    summary.update({
        "status": "EVALUATOR_ONLY_SMOKE_PASS_NOT_FORMAL",
        "failure_stage": None,
        "validation_errors": [],
        "schema_validation": "PASS",
        "semantic_validation": "PASS",
        "deterministic_scoring_run": True,
        "deterministic_score_sha256": sha(RETRY_DIR / "provisional_deterministic_scores.json"),
        "score_record_verified": True,
        "provisional_only": True,
    })
    write_new(SUMMARY_PATH, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit-previous", "run-once"])
    args = parser.parse_args()
    result = audit_previous() if args.command == "audit-previous" else run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
