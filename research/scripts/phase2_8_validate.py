#!/usr/bin/env python3
"""Structural/provenance validator for Phase 2.8; makes no semantic claim."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    errors: list[str] = []
    gold = jsonl(ROOT / "data/gold_references_phase2_6_candidate.jsonl")
    coverage = jsonl(ROOT / "data/phase2_8_per_bug_corpus_coverage.jsonl")
    manifest = json.loads((ROOT / "data/phase2_8_candidate_corpus_manifest.json").read_text())
    hashes = json.loads((ROOT / "data/phase2_8_prior_artifact_hashes.json").read_text())["files"]
    if len(gold) != 18 or len(coverage) != 18:
        errors.append("expected 18 Gold and coverage rows")
    if [x["bug_id"] for x in gold] != [x["bug_id"] for x in coverage]:
        errors.append("coverage IDs/order differ from Gold")
    if any(x.get("human_decision") is not None for x in coverage):
        errors.append("coverage contains a human decision")
    if any(x[t]["strictly_eligible_documents"] != 0 for x in coverage for t in ("track_a_historical_issue_pr", "track_b_immutable_commit_sensitivity")):
        errors.append("strict eligibility must remain zero under current evidence")
    if any(x[t]["verdict"] != "NOT_VERIFIABLE" for x in coverage for t in ("track_a_historical_issue_pr", "track_b_immutable_commit_sensitivity")):
        errors.append("unexpected corpus verdict")
    complete = sum(bool(x["evaluation_targets_v2"]["cspc_eligible_path_ids"]) for x in gold)
    partial = len(gold) - complete
    paths = [p for x in gold for p in x["trigger_paths"]]
    denominator_one = sum(p["tcc_denominator"] == 1 for p in paths)
    one_bugs = sum(max(p["tcc_denominator"] for p in x["trigger_paths"]) == 1 for x in gold)
    if (complete, partial, len(paths), denominator_one, one_bugs) != (11, 7, 19, 15, 14):
        errors.append(f"unexpected cohort/TCC diagnostic counts: {(complete, partial, len(paths), denominator_one, one_bugs)}")
    if manifest.get("status") != "NOT_APPROVED_NOT_INDEXABLE" or manifest.get("official_index_created") is not False or manifest.get("contains_model_input") is not False:
        errors.append("candidate manifest misstates authorization")
    restricted = {sha for x in gold for sha in x["repair_evidence_summary"]["repair_commit_shas"]}
    if any(s["document_id"].rsplit("@", 1)[-1] in restricted for s in manifest["samples"]):
        errors.append("candidate samples include a restricted repair SHA")
    for name, expected in hashes.items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"prior artifact hash changed: {name}")
    required_reports = (
        "reports/phase2_8_temporal_corpus_feasibility.md",
        "reports/phase2_8_gold_adjudication_support.md",
        "reports/test_scenario_evaluation_rubric_v3_1_PROPOSED.md",
        "reports/phase2_8_tcc_ceiling_diagnostic.md",
        "reports/phase2_8_research_go_no_go.md",
        "reports/phase2_8_completion_report.md",
    )
    for name in required_reports:
        if not (ROOT / name).exists():
            errors.append(f"missing report: {name}")
    rubric = (ROOT / "reports/test_scenario_evaluation_rubric_v3_1_PROPOSED.md").read_text()
    for phrase in ("highest TCC", "earliest scenario index", "Multidimensional SA diagnostic", "not_scorable", "B_PREVENT", "missing_output", "unsupported_all_output_count", "blinded"):
        if phrase.casefold() not in rubric.casefold():
            errors.append(f"rubric missing {phrase}")
    gold_support = (ROOT / "reports/phase2_8_gold_adjudication_support.md").read_text()
    for record in gold:
        if record["bug_id"] not in gold_support:
            errors.append(f"Gold support missing {record['bug_id']}")
    if re.search(r"Human decision:\s*(Approve|Revise|Insufficient Evidence)", gold_support, re.I):
        errors.append("Gold support invents a human decision")
    forbidden = (
        "data/gold_references_frozen.jsonl", "data/frozen_sample_manifest.json",
        "data/freeze_manifest.sha256", "data/human_review_decisions.jsonl",
        "data/phase2_8_official_bm25_index.json", "data/phase2_8_official_outputs.jsonl",
    )
    for name in forbidden:
        if (ROOT / name).exists():
            errors.append(f"forbidden artifact exists: {name}")
    result = {"status": "PASS_STRUCTURAL_AND_ENGINEERING_ONLY" if not errors else "FAIL",
              "records": len(coverage), "complete": complete, "partial": partial,
              "paths": len(paths), "denominator_one_paths": denominator_one,
              "strict_track_a_documents": sum(x["track_a_historical_issue_pr"]["strictly_eligible_documents"] for x in coverage),
              "strict_track_b_documents": sum(x["track_b_immutable_commit_sensitivity"]["strictly_eligible_documents"] for x in coverage),
              "human_decisions_recorded": 0, "semantic_correctness_claimed": False,
              "official_index_or_experiment_created": False, "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
