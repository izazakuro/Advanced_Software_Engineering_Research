#!/usr/bin/env python3
"""Offline structural validation and human-judgment aggregation for Phase 3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean, median


HERE = Path(__file__).resolve().parent
REQUIRED_SCENARIO_FIELDS = {
    "scenario_id", "preconditions", "environment_assumptions", "trigger_conditions",
    "ordered_execution_steps", "expected_observable_behavior", "test_oracle",
    "uncertainty_or_unsupported_assumptions",
}
METRICS = {"tcc", "cspc", "strict_oracle", "scenario_adequacy"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_raw_response(raw: bytes | None, expected_bug_id: str, seen_hashes: set[str] | None = None) -> dict:
    if raw is None or raw == b"":
        return {"status": "GENERATION_FAILURE", "errors": ["missing response"]}
    digest = sha256_bytes(raw)
    if seen_hashes is not None and digest in seen_hashes:
        return {"status": "DUPLICATE_RESPONSE", "errors": ["duplicate raw response"], "response_sha256": digest}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "PARSING_FAILURE", "errors": [str(exc)], "response_sha256": digest}
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != {"bug_id", "scenarios"}:
        errors.append("top-level object must contain exactly bug_id and scenarios")
    if isinstance(value, dict) and value.get("bug_id") != expected_bug_id:
        errors.append("bug_id mismatch")
    scenarios = value.get("scenarios") if isinstance(value, dict) else None
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        errors.append("scenarios must contain exactly three entries")
        scenarios = []
    ids: list[str] = []
    semantic_incomplete = False
    for index, scenario in enumerate(scenarios, 1):
        if not isinstance(scenario, dict) or set(scenario) != REQUIRED_SCENARIO_FIELDS:
            errors.append(f"scenario {index} field set is invalid")
            continue
        scenario_id = scenario.get("scenario_id")
        ids.append(scenario_id)
        if scenario_id != f"S{index}":
            errors.append(f"scenario {index} must have id S{index}")
        for key in ("preconditions", "environment_assumptions", "trigger_conditions", "uncertainty_or_unsupported_assumptions"):
            if not isinstance(scenario.get(key), list) or not all(isinstance(x, str) for x in scenario.get(key, [])):
                errors.append(f"{scenario_id}: {key} must be an array of strings")
        steps = scenario.get("ordered_execution_steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{scenario_id}: ordered_execution_steps must be non-empty")
        else:
            for step_index, step in enumerate(steps, 1):
                if not isinstance(step, dict) or set(step) != {"step", "action"} or step.get("step") != step_index or not isinstance(step.get("action"), str) or not step.get("action"):
                    errors.append(f"{scenario_id}: execution steps must be consecutive and non-empty")
                    break
        for key in ("expected_observable_behavior", "test_oracle"):
            if not isinstance(scenario.get(key), str) or not scenario.get(key).strip():
                errors.append(f"{scenario_id}: {key} must be non-empty")
        if not scenario.get("trigger_conditions"):
            semantic_incomplete = True
    if len(ids) != len(set(ids)):
        errors.append("scenario IDs are not unique")
    if errors:
        status = "SCHEMA_FAILURE"
    elif semantic_incomplete:
        status = "SEMANTICALLY_INCOMPLETE"
    else:
        status = "STRUCTURALLY_VALID"
    if seen_hashes is not None:
        seen_hashes.add(digest)
    return {"status": status, "errors": errors, "response_sha256": digest, "parsed": value}


def validate_human_judgment(record: dict, metric_eligibility: dict) -> list[str]:
    errors: list[str] = []
    required = {"task_id", "bug_id", "scenario_id", "metric", "numerator", "denominator", "reviewer_id", "evidence_note"}
    if set(record) != required:
        errors.append("judgment field set is invalid")
    if record.get("metric") not in METRICS:
        errors.append("unknown metric")
    if not isinstance(record.get("numerator"), (int, float)) or not isinstance(record.get("denominator"), (int, float)):
        errors.append("numerator and denominator must be numeric")
    elif record["denominator"] <= 0 or not 0 <= record["numerator"] <= record["denominator"]:
        errors.append("invalid numerator/denominator")
    per_bug = {row["bug_id"]: row for row in metric_eligibility["per_record"]}
    eligibility = per_bug.get(record.get("bug_id"))
    if eligibility is None:
        errors.append("unknown bug_id")
    elif record.get("metric") == "strict_oracle":
        included = [x for x in eligibility["strict_oracles"] if x["strict_scoring_disposition"] == "INCLUDED"]
        if not included:
            errors.append("bug has no strict-Oracle-eligible entry")
    return errors


def _bug_eligibility(metric_eligibility: dict, bug_id: str) -> dict:
    match = next((row for row in metric_eligibility["per_record"] if row["bug_id"] == bug_id), None)
    if match is None:
        raise ValueError(f"unknown frozen Gold bug: {bug_id}")
    return match


def evaluate_tcc(metric_eligibility: dict, bug_id: str, path_id: str, human_covered_condition_ids: list[str]) -> dict:
    """Calculate TCC from explicit human condition judgments; never infers coverage."""
    record = _bug_eligibility(metric_eligibility, bug_id)
    path = next((item for item in record["tcc_paths"] if item["path_id"] == path_id), None)
    if path is None:
        raise ValueError(f"{bug_id}: path {path_id} is not TCC-eligible")
    required = set(path["required_condition_ids"])
    covered = set(human_covered_condition_ids)
    if not covered <= required:
        raise ValueError(f"{bug_id}/{path_id}: covered IDs are outside the frozen denominator")
    return {"metric": "tcc", "bug_id": bug_id, "path_id": path_id, "numerator": len(covered), "denominator": path["denominator"], "score": len(covered) / path["denominator"], "semantic_source": "HUMAN_JUDGMENT"}


def evaluate_cspc(metric_eligibility: dict, bug_id: str, path_id: str, human_complete_single_scenario: bool) -> dict:
    """Score an eligible path from a human coherence/completeness judgment."""
    record = _bug_eligibility(metric_eligibility, bug_id)
    if path_id not in record["cspc_eligible_path_ids"]:
        return {"metric": "cspc", "bug_id": bug_id, "path_id": path_id, "score": None, "status": "NA_FROZEN_INELIGIBLE", "semantic_source": "FROZEN_ELIGIBILITY"}
    return {"metric": "cspc", "bug_id": bug_id, "path_id": path_id, "numerator": int(human_complete_single_scenario), "denominator": 1, "score": float(human_complete_single_scenario), "status": "SCORED", "semantic_source": "HUMAN_JUDGMENT"}


def evaluate_strict_oracle(metric_eligibility: dict, bug_id: str, oracle_id: str, human_correct: bool) -> dict:
    """Respect frozen strict-Oracle disposition before accepting a human score."""
    record = _bug_eligibility(metric_eligibility, bug_id)
    oracle = next((item for item in record["strict_oracles"] if item["oracle_id"] == oracle_id), None)
    if oracle is None:
        raise ValueError(f"{bug_id}: unknown Oracle {oracle_id}")
    disposition = oracle["strict_scoring_disposition"]
    if disposition != "INCLUDED":
        return {"metric": "strict_oracle", "bug_id": bug_id, "oracle_id": oracle_id, "score": None, "status": disposition, "semantic_source": "FROZEN_ELIGIBILITY"}
    return {"metric": "strict_oracle", "bug_id": bug_id, "oracle_id": oracle_id, "numerator": int(human_correct), "denominator": 1, "score": float(human_correct), "status": "SCORED", "semantic_source": "HUMAN_JUDGMENT"}


def evaluate_scenario_adequacy(metric_eligibility: dict, bug_id: str, human_adequate: bool) -> dict:
    record = _bug_eligibility(metric_eligibility, bug_id)
    if not record["scenario_adequacy_required"]:
        return {"metric": "scenario_adequacy", "bug_id": bug_id, "score": None, "status": "NA_FROZEN_INELIGIBLE"}
    return {"metric": "scenario_adequacy", "bug_id": bug_id, "numerator": int(human_adequate), "denominator": 1, "score": float(human_adequate), "status": "SCORED", "semantic_source": "HUMAN_JUDGMENT"}


def select_scenario(judgments: list[dict]) -> str:
    """PROPOSED rule: highest TCC proportion, then earliest scenario ID only."""
    tcc = [x for x in judgments if x["metric"] == "tcc"]
    if not tcc:
        raise ValueError("scenario selection requires TCC judgments")
    return min(tcc, key=lambda x: (-x["numerator"] / x["denominator"], x["scenario_id"]))["scenario_id"]


def aggregate_task(judgments: list[dict]) -> dict:
    chosen = select_scenario(judgments)
    selected = [x for x in judgments if x["scenario_id"] == chosen]
    return {"selected_scenario_id": chosen, "metrics": {m: next((x["numerator"] / x["denominator"] for x in selected if x["metric"] == m), None) for m in sorted(METRICS)}}


def aggregate_bug(task_results: list[dict]) -> dict:
    """Average repeats within each condition; Bug remains the inferential unit."""
    conditions: dict[str, dict[str, list[float]]] = {}
    for row in task_results:
        bucket = conditions.setdefault(row["condition"], {})
        for metric, value in row["metrics"].items():
            if value is not None:
                bucket.setdefault(metric, []).append(value)
    return {condition: {metric: mean(values) for metric, values in metrics.items()} for condition, metrics in conditions.items()}


def paired_summary(per_bug: list[dict], metric: str, left: str, right: str) -> dict:
    differences = [row[left][metric] - row[right][metric] for row in per_bug if metric in row.get(left, {}) and metric in row.get(right, {})]
    return {"bugs": len(differences), "mean_paired_difference": mean(differences) if differences else None, "median_paired_difference": median(differences) if differences else None, "differences": differences}


if __name__ == "__main__":
    print(json.dumps({"status": "OFFLINE_EVALUATOR_MODULE_READY", "llm_judge_calls": 0, "semantic_judgment": "HUMAN_REQUIRED"}))
