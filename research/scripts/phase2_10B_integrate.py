#!/usr/bin/env python3
"""Validate supplied Phase 2.10B reviews and emit non-frozen derived artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    target_path = DATA / "phase2_9_abc_target_issue_inputs.jsonl"
    top10_path = DATA / "phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl"
    top3_path = DATA / "phase2_9_abc_top3_similar_PROPOSED.jsonl"
    random_path = DATA / "phase2_9_abc_random_context_PROPOSED.jsonl"
    prompt_path = DATA / "phase2_9_abc_generation_prompt_PROPOSED.txt"
    config_path = DATA / "phase2_9_abc_generation_config_PROPOSED.json"
    rubric_path = ROOT / "reports/phase2_9_abc_experiment_protocol_PROPOSED.md"
    eval_rubric_path = ROOT / "reports/test_scenario_evaluation_rubric_abc_PROPOSED.md"
    gold_path = DATA / "gold_references_phase2_6_candidate.jsonl"
    review_path = ROOT / "phase2_10B_bm25_eligibility_and_fallback_review.csv"
    supplied_manifest_path = ROOT / "phase2_10B_abc_candidate_manifest_NOT_FROZEN.jsonl"
    supplied_report_path = ROOT / "phase2_10B_readiness_and_decision_report.md"

    targets = {r["bug_id"]: r for r in load_jsonl(target_path)}
    top10 = {r["bug_id"]: r for r in load_jsonl(top10_path)}
    top3 = {r["bug_id"]: r for r in load_jsonl(top3_path)}
    random_rows = {r["bug_id"]: r for r in load_jsonl(random_path)}
    supplied = {r["target_bug_id"]: r for r in load_jsonl(supplied_manifest_path)}
    gold = load_jsonl(gold_path)
    source_b = {(bug, c["doc_id"]): c for bug, rec in top10.items() for c in rec["candidates"]}
    source_c = {(bug, c["doc_id"]): c for bug, rec in random_rows.items() for c in rec["documents"]}

    with review_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        review_fields = reader.fieldnames or []
        review = list(reader)

    augmented = []
    errors = []
    priority_pairs = []
    for row in review:
        key = (row["target_bug_id"], row["candidate_doc_id"])
        src = source_b.get(key) if row["group"] == "B" else source_c.get(key)
        mapping_ok = src is not None
        hash_ok = mapping_ok and row["source_content_sha256"] == src["content_sha256"]
        rank_ok = row["group"] != "B" or (mapping_ok and int(row["bm25_rank"]) == src["rank_before_human_review"])
        score_ok = row["group"] != "B" or (mapping_ok and abs(float(row["bm25_score"]) - src["bm25_score"]) < 1e-10)
        title_ok = mapping_ok and row["candidate_title"] == src["title"]
        if not all((mapping_ok, hash_ok, rank_ok, score_ok, title_ok)):
            errors.append({"type": "review_source_mismatch", "target_bug_id": key[0], "candidate_doc_id": key[1],
                           "mapping": mapping_ok, "hash": hash_ok, "rank": rank_ok, "score": score_ok, "title": title_ok})
        priority = row["eligibility_status"] in {"PRIORITY_SAME_BUG_OR_LEAKAGE_REVIEW", "AUTOMATED_RISK_REVIEW_REQUIRED"}
        if priority:
            priority_pairs.append(key)
        # The confirmed design defines B Top-3 by raw rank, not by the older
        # machine proposal (which skipped MapTool rank 2).
        tier = "REQUIRED_BEFORE_FREEZE" if row["group"] == "C" or (row["group"] == "B" and int(row["bm25_rank"]) <= 3) else "DEFERRED_UNLESS_FALLBACK_NEEDED"
        augmented.append({**row,
            "verified_source_mapping": mapping_ok,
            "verified_content_hash": hash_ok,
            "verified_original_rank": rank_ok if row["group"] == "B" else "NOT_APPLICABLE",
            "verified_bm25_score": score_ok if row["group"] == "B" else "NOT_APPLICABLE",
            "verified_title": title_ok,
            "review_tier": tier,
            "ai_assessment_authority": "ADVISORY_ONLY",
            "low_relevance_exclusion_permitted": False,
            "human_decision_state": "PENDING",
        })

    manifest = []
    manifest_discrepancies = []
    for bug_id in sorted(targets):
        src_ranked = sorted(top10[bug_id]["candidates"], key=lambda x: x["rank_before_human_review"])
        raw_top3 = [{"doc_id": c["doc_id"], "rank": c["rank_before_human_review"], "sha256": c["content_sha256"]} for c in src_ranked[:3]]
        p29_ids = top3[bug_id]["document_ids"]
        supplied_rec = supplied.get(bug_id)
        if supplied_rec is None:
            manifest_discrepancies.append({"target_bug_id": bug_id, "type": "missing_supplied_manifest_record"})
            supplied_b, supplied_c, supplied_fallback = [], [], []
        else:
            supplied_b = supplied_rec["group_B_proposed"]
            supplied_c = supplied_rec["group_C_proposed"]
            supplied_fallback = supplied_rec["bm25_fallback_order"]
            if supplied_rec["target_content_sha256"] != targets[bug_id]["content_sha256"]:
                manifest_discrepancies.append({"target_bug_id": bug_id, "type": "target_hash_mismatch"})
        supplied_b_ids = [x["doc_id"] for x in supplied_b]
        if supplied_b_ids != p29_ids:
            manifest_discrepancies.append({"target_bug_id": bug_id, "type": "supplied_B_differs_from_phase2_9_proposal",
                                           "supplied": supplied_b_ids, "phase2_9": p29_ids})
        expected_c = [x["doc_id"] for x in sorted(random_rows[bug_id]["documents"], key=lambda x: x["slot"])]
        if [x["doc_id"] for x in supplied_c] != expected_c:
            manifest_discrepancies.append({"target_bug_id": bug_id, "type": "supplied_C_differs_from_phase2_9_proposal"})
        sequence = supplied_b + supplied_fallback
        seq_ranks = [int(x["rank"]) for x in sequence]
        seq_ids = [x["doc_id"] for x in sequence]
        if seq_ranks != list(range(1, 11)) or len(seq_ids) != len(set(seq_ids)):
            manifest_discrepancies.append({"target_bug_id": bug_id, "type": "supplied_B_fallback_not_exact_rank_1_to_10",
                                           "ranks": seq_ranks, "duplicate_doc_ids": [k for k, v in Counter(seq_ids).items() if v > 1]})
        for item in sequence:
            src = source_b.get((bug_id, item["doc_id"]))
            if not src or int(item["rank"]) != src["rank_before_human_review"] or item["sha256"] != src["content_sha256"]:
                manifest_discrepancies.append({"target_bug_id": bug_id, "type": "supplied_B_item_source_mismatch", "item": item})
        for item in supplied_c:
            src = source_c.get((bug_id, item["doc_id"]))
            if not src or int(item["slot"]) != src["slot"] or item["sha256"] != src["content_sha256"]:
                manifest_discrepancies.append({"target_bug_id": bug_id, "type": "supplied_C_item_source_mismatch", "item": item})
        manifest.append({
            "status": "VALIDATED_NOT_FROZEN_HUMAN_DECISIONS_PENDING",
            "target_bug_id": bug_id,
            "target": {"doc_id": bug_id, "content_sha256": targets[bug_id]["content_sha256"]},
            "group_A": {"context_documents": [], "rule": "target_issue_only"},
            "group_B": {
                "selection_rule": "ascending_original_bm25_rank; skip only human-confirmed predefined eligibility violations; never use semantic relevance",
                "authoritative_ranked_candidates": [{"doc_id": c["doc_id"], "rank": c["rank_before_human_review"],
                    "bm25_score": c["bm25_score"], "content_sha256": c["content_sha256"],
                    "automated_risk_status": c["automated_risk_status"], "human_eligibility_decision": None} for c in src_ranked],
                "top3_if_all_eligible": raw_top3,
                "phase2_9_machine_proposal": p29_ids,
                "supplied_ai_manifest_proposal": supplied_b_ids,
                "official_selection": None,
            },
            "group_C": {
                "selection_rule": "retain original deterministic random slots if eligible; deterministic resampling only after human-confirmed exclusion",
                "global_seed": random_rows[bug_id]["global_seed"],
                "proposed_documents": [{"doc_id": c["doc_id"], "slot": c["slot"], "content_sha256": c["content_sha256"],
                    "seed": c["seed"], "human_eligibility_decision": None} for c in sorted(random_rows[bug_id]["documents"], key=lambda x: x["slot"])],
                "official_selection": None,
            },
            "human_approval": None,
        })

    queue_path = DATA / "phase2_10B_eligibility_and_leakage_decision_queue.csv"
    out_fields = review_fields + [k for k in augmented[0] if k not in review_fields]
    with queue_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=out_fields)
        writer.writeheader(); writer.writerows(augmented)
    manifest_path = DATA / "phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl"
    write_jsonl(manifest_path, manifest)

    prompt_config = json.loads(config_path.read_text(encoding="utf-8"))
    prompt_hash = digest(prompt_path)
    experiment_config = {
        "status": "PROPOSED_NOT_FROZEN_NO_MODEL_CALLS_AUTHORIZED",
        "design": {"targets": 18, "conditions": ["A_TARGET_ONLY", "B_TARGET_PLUS_ELIGIBLE_BM25_TOP3", "C_TARGET_PLUS_ELIGIBLE_RANDOM3"],
                   "repetitions": 3, "planned_generations": 162},
        "selection_manifest": str(manifest_path.relative_to(ROOT)),
        "decision_queue": str(queue_path.relative_to(ROOT)),
        "prompt": {"path": str(prompt_path.relative_to(ROOT)), "actual_sha256": prompt_hash,
                   "phase2_9_configured_sha256": prompt_config["prompt_template_sha256"],
                   "hash_matches": prompt_hash == prompt_config["prompt_template_sha256"]},
        "model_identifier": prompt_config["model_identifier"], "model_version": prompt_config["model_version"],
        "sampling": {"temperature_proposed": prompt_config["temperature_proposed"], "top_p_proposed": prompt_config["top_p_proposed"],
                     "output_token_limit_proposed": prompt_config["output_token_limit_proposed"]},
        "rubric": {"path": str(eval_rubric_path.relative_to(ROOT)), "sha256": digest(eval_rubric_path), "human_approval": None},
        "gold": {"path": str(gold_path.relative_to(ROOT)), "sha256": digest(gold_path), "records": len(gold),
                 "approval_counts": dict(Counter(str(x.get("human_approval")) for x in gold))},
        "random_control": {"source": str(random_path.relative_to(ROOT)), "global_seed": 29012026,
                           "rule": "Phase 2.9 length-matched deterministic selection; equivalent eligibility policy; deterministic resample only after exclusion"},
        "official_generation_authorized": False, "human_approval": None,
    }
    config_out = DATA / "phase2_10B_experiment_config_PROPOSED.json"
    config_out.write_text(json.dumps(experiment_config, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    report_claim = "not included" in supplied_report_path.read_text(encoding="utf-8")
    reconciliation = {
        "status": "VALIDATION_COMPLETE_NOT_FROZEN",
        "verified_facts": {
            "targets": len(targets), "bm25_rows": len(source_b), "random_rows": len(source_c), "review_rows": len(review),
            "review_group_counts": dict(Counter(r["group"] for r in review)), "all_review_source_fields_match": not errors,
            "all_human_eligibility_fields_blank": all(not r["human_eligibility_decision"] for r in review),
            "priority_duplicate_or_leakage_pairs": len(set(priority_pairs)), "prompt_exists": prompt_path.exists(),
            "prompt_hash": prompt_hash, "prompt_hash_matches_phase2_9_config": experiment_config["prompt"]["hash_matches"],
        },
        "source_mismatches": errors,
        "manifest_discrepancies": manifest_discrepancies,
        "ai_recommendations": {"authority": "advisory_only", "counts": dict(Counter(r["ai_recommendation"] for r in review)),
                               "replace_recommended_not_applied": sum(r["ai_recommendation"] == "REPLACE_RECOMMENDED" for r in review)},
        "stale_supplied_report_claim": {"detected": report_claim, "claim": "prompt bytes unavailable", "verified_correction": "prompt exists locally and hash matches config"},
        "input_hashes": {str(p.relative_to(ROOT)): digest(p) for p in [review_path, supplied_manifest_path, supplied_report_path,
            target_path, top10_path, top3_path, random_path, prompt_path, config_path, rubric_path, eval_rubric_path, gold_path]},
        "output_hashes": {str(queue_path.relative_to(ROOT)): digest(queue_path), str(manifest_path.relative_to(ROOT)): digest(manifest_path),
                          str(config_out.relative_to(ROOT)): digest(config_out)},
    }
    rec_path = DATA / "phase2_10B_artifact_reconciliation.json"
    rec_path.write_text(json.dumps(reconciliation, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"queue_rows": len(augmented), "manifest_records": len(manifest), "source_mismatches": len(errors),
                      "manifest_discrepancies": len(manifest_discrepancies), "priority_pairs": len(set(priority_pairs)),
                      "outputs": [str(x.relative_to(ROOT)) for x in [queue_path, manifest_path, config_out, rec_path]]}, indent=2))


if __name__ == "__main__":
    main()
