#!/usr/bin/env python3
"""Phase 3C offline validation/scoring interfaces. No semantic inference or LLM judge."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
AB_DIR = RESEARCH / "outputs/phase3_ab_offline_preparation"


def _load_base():
    path = AB_DIR / "phase3_offline_evaluator.py"
    spec = importlib.util.spec_from_file_location("phase3_ab_evaluator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


BASE = _load_base()

GENERATION_FAILURE_STATUSES = {"EMPTY_OUTPUT", "PARSING_FAILURE", "SCHEMA_FAILURE", "NO_USABLE_SCENARIO"}


def classify_attempt(*, invocation_started: bool, invocation_completed: bool, raw: bytes | None, expected_bug_id: str, process_error: str | None = None, seen_hashes: set[str] | None = None) -> dict:
    """Separate infrastructure failure from an invoked model's unusable output."""
    if not invocation_started or not invocation_completed:
        return {
            "status": "INFRASTRUCTURE_FAILURE",
            "failure_class": "INFRASTRUCTURE_FAILURE",
            "process_error": process_error or "unavailable",
            "metric_scoring_eligible": False,
            "retry_eligible": True,
        }
    if raw is None or raw == b"":
        return {
            "status": "EMPTY_OUTPUT",
            "failure_class": "MODEL_GENERATION_FAILURE",
            "errors": ["invocation completed with empty output"],
            "metric_scoring_eligible": True,
            "retry_eligible": False,
        }
    result = BASE.validate_raw_response(raw, expected_bug_id, seen_hashes)
    if result["status"] in {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}:
        extra_errors = []
        for scenario in result["parsed"]["scenarios"]:
            for step in scenario["ordered_execution_steps"]:
                if not step["action"].strip():
                    extra_errors.append(f"{scenario['scenario_id']}: step action is whitespace-only")
            if not scenario["expected_observable_behavior"].strip():
                extra_errors.append(f"{scenario['scenario_id']}: expected behavior is whitespace-only")
            if not scenario["test_oracle"].strip():
                extra_errors.append(f"{scenario['scenario_id']}: Oracle is whitespace-only")
        if extra_errors:
            result["status"] = "SCHEMA_FAILURE"
            result["errors"].extend(extra_errors)
    status = result["status"]
    if status == "GENERATION_FAILURE":
        status = "EMPTY_OUTPUT"
    result["status"] = status
    if status in {"PARSING_FAILURE", "SCHEMA_FAILURE"}:
        result["failure_class"] = "MODEL_GENERATION_FAILURE"
        result["metric_scoring_eligible"] = True
        result["retry_eligible"] = False
    elif status == "SEMANTICALLY_INCOMPLETE":
        result["failure_class"] = None
        result["metric_scoring_eligible"] = True
        result["retry_eligible"] = False
    elif status == "STRUCTURALLY_VALID":
        result["failure_class"] = None
        result["metric_scoring_eligible"] = True
        result["retry_eligible"] = False
    elif status == "DUPLICATE_RESPONSE":
        result["failure_class"] = "PROTOCOL_DEVIATION"
        result["metric_scoring_eligible"] = False
        result["retry_eligible"] = False
    return result


def _eligibility_by_bug(metric_eligibility: dict) -> dict[str, dict]:
    return {row["bug_id"]: row for row in metric_eligibility["per_record"]}


def select_best_tcc(metric_eligibility: dict, bug_id: str, human_comparisons: list[dict]) -> dict:
    """Best coherent scenario/path by TCC; judgments must cite frozen condition IDs."""
    bug = _eligibility_by_bug(metric_eligibility).get(bug_id)
    if bug is None:
        raise ValueError(f"unknown bug {bug_id}")
    paths = {path["path_id"]: path for path in bug["tcc_paths"]}
    scored = []
    for item in human_comparisons:
        if set(item) != {"scenario_id", "path_id", "coherent", "covered_required_condition_ids"}:
            raise ValueError("TCC comparison has unexpected fields")
        if item["scenario_id"] not in {"S1", "S2", "S3"}:
            raise ValueError("invalid scenario ID")
        path = paths.get(item["path_id"])
        if path is None:
            raise ValueError("comparison uses a non-eligible TCC path")
        required = set(path["required_condition_ids"])
        covered = set(item["covered_required_condition_ids"])
        if not covered <= required:
            raise ValueError("covered TCC IDs fall outside frozen denominator")
        if not item["coherent"]:
            continue
        scored.append({
            "scenario_id": item["scenario_id"], "path_id": item["path_id"],
            "covered_required_condition_ids": sorted(covered),
            "missing_required_condition_ids": sorted(required - covered),
            "tcc_numerator": len(covered), "tcc_denominator": path["denominator"],
            "tcc_proportion": len(covered) / path["denominator"],
        })
    if not scored:
        return {"status": "NO_COHERENT_SCENARIO", "tcc_numerator": 0, "tcc_denominator": None, "tcc_proportion": 0.0}
    # Phase 2 fixes earliest scenario for a TCC tie. Path-ID tie-break remains a Phase 3C proposal.
    return min(scored, key=lambda x: (-x["tcc_proportion"], int(x["scenario_id"][1:]), x["path_id"]))


def score_cspc_paths(metric_eligibility: dict, bug_id: str, human_path_reviews: list[dict]) -> dict:
    """Retain every path result, then derive the Rubric's any-eligible-path response binary."""
    bug = _eligibility_by_bug(metric_eligibility).get(bug_id)
    if bug is None:
        raise ValueError(f"unknown bug {bug_id}")
    eligible = list(bug["cspc_eligible_path_ids"])
    reviews = {row["path_id"]: row for row in human_path_reviews}
    path_results = []
    required_fields = {"path_id", "matching_scenario_id", "required_setup_covered", "trigger_coverage_complete", "execution_order_valid", "observable_result_covered"}
    for path_id in eligible:
        row = reviews.get(path_id)
        if row is None or set(row) != required_fields:
            raise ValueError(f"missing or invalid CSPC review for {path_id}")
        scenario = row["matching_scenario_id"]
        if scenario not in {None, "S1", "S2", "S3"}:
            raise ValueError("invalid CSPC matching scenario")
        complete = bool(scenario) and all(bool(row[key]) for key in ("required_setup_covered", "trigger_coverage_complete", "execution_order_valid", "observable_result_covered"))
        path_results.append({**row, "eligible": True, "cspc": int(complete)})
    if not eligible:
        return {"bug_id": bug_id, "path_results": [], "bug_response_cspc": None, "status": "NA_FROZEN_INELIGIBLE"}
    return {"bug_id": bug_id, "path_results": path_results, "bug_response_cspc": int(any(x["cspc"] for x in path_results)), "aggregation": "ANY_ELIGIBLE_PATH_PER_RESPONSE", "status": "SCORED"}


def score_strict_oracle(metric_eligibility: dict, bug_id: str, oracle_id: str, selected_scenario_id: str, human_category: str) -> dict:
    if selected_scenario_id not in {"S1", "S2", "S3"}:
        raise ValueError("Oracle must use the TCC-selected scenario")
    if human_category not in {"correct", "partial", "incorrect", "not_scorable"}:
        raise ValueError("invalid Oracle category")
    bug = _eligibility_by_bug(metric_eligibility).get(bug_id)
    oracle = next((x for x in bug["strict_oracles"] if x["oracle_id"] == oracle_id), None) if bug else None
    if oracle is None:
        raise ValueError("unknown frozen Oracle")
    disposition = oracle["strict_scoring_disposition"]
    if disposition != "INCLUDED":
        return {"bug_id": bug_id, "oracle_id": oracle_id, "selected_scenario_id": selected_scenario_id, "category": None, "score": None, "status": disposition}
    if human_category == "not_scorable":
        raise ValueError("a frozen eligible Oracle cannot be removed from the denominator without an approved Gold amendment")
    return {"bug_id": bug_id, "oracle_id": oracle_id, "selected_scenario_id": selected_scenario_id, "category": human_category, "score": 1 if human_category == "correct" else 0, "status": "SCORED"}


def generation_failure_scores(metric_eligibility: dict, bug_id: str) -> dict:
    bug = _eligibility_by_bug(metric_eligibility)[bug_id]
    return {
        "tcc": 0.0,
        "scenario_adequacy": 0.0,
        "cspc": 0.0 if bug["cspc_eligible_path_ids"] else None,
        "strict_oracle": [
            {"oracle_id": x["oracle_id"], "score": 0 if x["strict_scoring_disposition"] == "INCLUDED" else None, "status": x["strict_scoring_disposition"]}
            for x in bug["strict_oracles"]
        ],
        "missing_output": True,
        "failure_class": "MODEL_GENERATION_FAILURE",
    }


if __name__ == "__main__":
    print(json.dumps({"status": "PHASE3C_OFFLINE_EVALUATOR_READY", "llm_judge_calls": 0, "semantic_judgment": "HUMAN_REQUIRED"}))
