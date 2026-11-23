#!/usr/bin/env python3
"""Final structural/engineering validation for Phase 2.9 preparation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    errors: list[str] = []
    build = json.loads((ROOT / "data/phase2_9_abc_corpus_build_summary.json").read_text())
    targets = jsonl(ROOT / "data/phase2_9_abc_target_issue_inputs.jsonl")
    top10 = jsonl(ROOT / "data/phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl")
    top3 = jsonl(ROOT / "data/phase2_9_abc_top3_similar_PROPOSED.jsonl")
    random_context = jsonl(ROOT / "data/phase2_9_abc_random_context_PROPOSED.jsonl")
    feasibility = jsonl(ROOT / "data/phase2_9_abc_retrieval_feasibility.jsonl")
    prior = json.loads((ROOT / "data/phase2_9_prior_artifact_hashes.json").read_text())["files"]
    ids = [x["bug_id"] for x in targets]
    if len(ids) != 18 or len(set(ids)) != 18:
        errors.append("target inputs must contain 18 unique bugs")
    for records, label in ((top10, "top10"), (top3, "top3"), (random_context, "random"), (feasibility, "feasibility")):
        if [x["bug_id"] for x in records] != ids:
            errors.append(f"{label} IDs/order mismatch")
    if build != {"source_rows": 326182, "target_rows": 18, "eligible_documents": 318615,
                 "excluded_documents": 7567, "exclusion_counts": {"exact_duplicate_of": 7473, "normalized_duplicate_of": 76, "selected_target_issue": 18},
                 "model_input_sources": ["issue.parquet:title", "issue.parquet:body"], "pr_commit_model_inputs": 0, "official_index": False}:
        errors.append("unexpected corpus build summary")
    db = sqlite3.connect(ROOT / "data/phase2_9_abc_issue_corpus.sqlite")
    count, distinct_hash = db.execute("SELECT count(*), count(DISTINCT normalized_sha256) FROM documents").fetchone()
    if (count, distinct_hash) != (318615, 318615):
        errors.append(f"corpus count/hash uniqueness mismatch: {(count, distinct_hash)}")
    target_in_db = db.execute(f"SELECT count(*) FROM documents WHERE doc_id IN ({','.join('?' for _ in ids)})", ids).fetchone()[0]
    if target_in_db:
        errors.append("selected targets present in additional-context corpus")
    eligible_ids = {row[0] for row in db.execute("SELECT doc_id FROM documents")}
    for ten, three, rnd in zip(top10, top3, random_context):
        if len(ten["candidates"]) != 10:
            errors.append(f"{ten['bug_id']}: Top-10 count wrong")
        if three["proposed_count"] != 3 or len(three["document_ids"]) != 3 or three["human_approval"] is not None:
            errors.append(f"{three['bug_id']}: proposed Top-3 gate wrong")
        if any(x["automated_risk_status"] != "LOW_AUTOMATED_RISK_REVIEW_STILL_REQUIRED" for x in three["documents"]):
            errors.append(f"{three['bug_id']}: high-risk candidate entered Top-3")
        if rnd["selected_count"] != 3 or rnd["human_approval"] is not None:
            errors.append(f"{rnd['bug_id']}: random K/approval wrong")
        b_ids, c_ids = set(three["document_ids"]), {x["doc_id"] for x in rnd["documents"]}
        if b_ids & c_ids:
            errors.append(f"{three['bug_id']}: B/C overlap")
        if not (b_ids | c_ids) <= eligible_ids:
            errors.append(f"{three['bug_id']}: context outside eligible Issue corpus")
        if any(x["length_tolerance_used"] != 0.2 for x in rnd["documents"]):
            errors.append(f"{three['bug_id']}: random length tolerance fallback used")
        if any(x["human_relevance_decision"] is not None or x["human_leakage_decision"] is not None for x in ten["candidates"]):
            errors.append(f"{ten['bug_id']}: fabricated candidate review")
    prompt = ROOT / "data/phase2_9_abc_generation_prompt_PROPOSED.txt"
    config = json.loads((ROOT / "data/phase2_9_abc_generation_config_PROPOSED.json").read_text())
    if hashlib.sha256(prompt.read_bytes()).hexdigest() != config["prompt_template_sha256"]:
        errors.append("prompt hash mismatch")
    if config["official_generation_authorized"] is not False or config["human_approval"] is not None or config["model_identifier"] is not None:
        errors.append("generation config invents authorization/model decision")
    workbook = (ROOT / "reports/phase2_9_abc_gold_adjudication_workbook.md").read_text()
    if workbook.count("Human decision (Approve / Revise / Insufficient Evidence): ______") != 18:
        errors.append("Gold workbook does not contain 18 blank decisions")
    for name, expected in prior.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            errors.append(f"prior artifact modified: {name}")
    required_reports = (
        "phase2_9_abc_research_design_PROPOSED.md", "phase2_9_abc_retrieval_feasibility.md",
        "phase2_9_abc_leakage_review.md", "phase2_9_abc_gold_adjudication_workbook.md",
        "test_scenario_evaluation_rubric_abc_PROPOSED.md", "phase2_9_abc_statistical_analysis_PROPOSED.md",
        "phase2_9_abc_experiment_protocol_PROPOSED.md", "phase2_9_abc_freeze_readiness.md",
        "phase2_9_abc_completion_report.md",
    )
    for name in required_reports:
        if not (ROOT / "reports" / name).exists():
            errors.append(f"missing report: {name}")
    forbidden = ("data/gold_references_frozen.jsonl", "data/freeze_manifest.sha256",
                 "data/phase2_9_abc_official_outputs.jsonl", "data/phase2_9_abc_official_scores.jsonl",
                 "data/phase2_9_abc_frozen_manifest.json")
    for name in forbidden:
        if (ROOT / name).exists():
            errors.append(f"forbidden artifact exists: {name}")
    result = {"status": "PASS_STRUCTURAL_AND_ENGINEERING_ONLY" if not errors else "FAIL",
              "source_issue_rows": build["source_rows"], "usable_issue_documents": count,
              "targets": len(ids), "top10_targets": sum(len(x["candidates"]) == 10 for x in top10),
              "targets_with_proposed_top3": sum(x["proposed_count"] == 3 for x in top3),
              "targets_with_random_k3": sum(x["selected_count"] == 3 for x in random_context),
              "targets_requiring_manual_review": sum(x["manual_leakage_review_required"] for x in feasibility),
              "human_approved_context_documents": 0, "gold_decisions_recorded": 0,
              "official_generation_or_scoring": False, "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
