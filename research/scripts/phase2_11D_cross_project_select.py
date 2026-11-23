#!/usr/bin/env python3
"""Deterministically reconstruct cross-project B/C proposals without model calls."""

from __future__ import annotations

import csv
import hashlib
import heapq
import json
import math
import random
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from phase2_9_abc_common import tokens
from phase2_9_bm25_retrieve import B, K1, risk


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GLOBAL_SEED = 29012026
RANK_DEPTH = 500


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_project(value: object) -> str | None:
    """Return canonical owner/repository identity, or None when unresolved."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("\\", "/")
    text = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", text, flags=re.I)
    text = text.removesuffix(".git").strip("/")
    parts = [part.strip().casefold() for part in text.split("/") if part.strip()]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        return None
    return "/".join(parts)


def project_eligibility(target_project_value: object, candidate_project_value: object, target_id: str, candidate_id: str) -> str:
    """Apply only the confirmed structural project-identity rules."""
    if candidate_id == target_id:
        return "EXCLUDED_TARGET_SELF"
    target_project = canonical_project(target_project_value)
    candidate_project = canonical_project(candidate_project_value)
    if target_project is None or candidate_project is None:
        return "REVIEW_UNRESOLVED_PROJECT_IDENTITY"
    if target_project == candidate_project:
        return "EXCLUDED_SAME_PROJECT"
    return "ELIGIBLE_CROSS_PROJECT"


def seed_for(bug_id: str, slot: int) -> int:
    raw = hashlib.sha256(f"{GLOBAL_SEED}|{bug_id}|{slot}".encode()).digest()
    return int.from_bytes(raw[:8], "big")


def compute_rankings(db: sqlite3.Connection, targets: list[dict]) -> dict[str, list[tuple[float, str]]]:
    n_docs, total_len = db.execute("SELECT count(*), sum(token_count) FROM documents").fetchone()
    avg_len = total_len / n_docs
    query_terms = [set(tokens(f"{target['title']}\n{target['body']}")) for target in targets]
    term_to_queries: dict[str, list[int]] = defaultdict(list)
    for qid, terms in enumerate(query_terms):
        for term in terms:
            term_to_queries[term].append(qid)
    union_terms = set(term_to_queries)
    document_frequency = Counter()
    cursor = db.execute("SELECT normalized_text FROM documents ORDER BY doc_id")
    while batch := cursor.fetchmany(2000):
        for (text,) in batch:
            document_frequency.update(set(tokens(text)) & union_terms)
    inverse_document_frequency = {
        term: math.log(1 + (n_docs - frequency + 0.5) / (frequency + 0.5))
        for term, frequency in document_frequency.items()
    }
    heaps: list[list[tuple[float, int, str]]] = [[] for _ in targets]
    cursor = db.execute("SELECT doc_id, normalized_text, token_count FROM documents ORDER BY doc_id")
    order = 0
    while batch := cursor.fetchmany(1000):
        for doc_id, text, doc_len in batch:
            order += 1
            counts = Counter(tokens(text))
            scores: dict[int, float] = defaultdict(float)
            for term in sorted(counts.keys() & union_terms):
                tf = counts[term]
                contribution = inverse_document_frequency[term] * tf * (K1 + 1) / (
                    tf + K1 * (1 - B + B * doc_len / avg_len)
                )
                for qid in term_to_queries[term]:
                    scores[qid] += contribution
            for qid, score in scores.items():
                item = (score, -order, doc_id)
                if len(heaps[qid]) < RANK_DEPTH:
                    heapq.heappush(heaps[qid], item)
                elif item > heaps[qid][0]:
                    heapq.heapreplace(heaps[qid], item)
    return {
        target["bug_id"]: [(score, doc_id) for score, _, doc_id in sorted(heaps[qid], key=lambda x: (-x[0], -x[1], x[2]))]
        for qid, target in enumerate(targets)
    }


def document(db: sqlite3.Connection, doc_id: str) -> tuple:
    row = db.execute(
        "SELECT doc_id, project, issue_number, title, body, created_at, token_count, content_sha256 FROM documents WHERE doc_id=?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing corpus document: {doc_id}")
    return row


def compact_candidate(target: dict, row: tuple, **extra: object) -> dict:
    assessed = risk(target, row)
    return {
        "doc_id": assessed["doc_id"], "project": assessed["project"],
        "canonical_project": canonical_project(assessed["project"]), "issue_number": assessed["issue_number"],
        "title": assessed["title"], "created_at": assessed["created_at"], "token_count": assessed["token_count"],
        "content_sha256": assessed["content_sha256"], "automated_risk_status": assessed["automated_risk_status"],
        "human_leakage_decision": None, "human_approval": None, **extra,
    }


def sample_random(
    db: sqlite3.Connection, target: dict, selected_b: list[dict]
) -> tuple[list[dict], list[dict]]:
    target_project = canonical_project(target["project"])
    if target_project is None:
        return [], [{"bug_id": target["bug_id"], "reason": "UNRESOLVED_TARGET_PROJECT"}]
    blocked = {target["bug_id"], *(item["doc_id"] for item in selected_b)}
    selected: list[dict] = []
    audit: list[dict] = []
    for slot, matched in enumerate(selected_b, 1):
        desired = matched["token_count"]
        chosen = None
        for tolerance in (0.20, 0.50, 1.00):
            low, high = max(1, int(desired * (1 - tolerance))), max(1, int(desired * (1 + tolerance)))
            rows = db.execute(
                "SELECT doc_id, project, issue_number, title, body, created_at, token_count, content_sha256 "
                "FROM documents WHERE token_count BETWEEN ? AND ? ORDER BY doc_id",
                (low, high),
            ).fetchall()
            eligible = []
            unresolved = 0
            same_project = 0
            for row in rows:
                project = canonical_project(row[1])
                if project is None:
                    unresolved += 1
                elif project == target_project:
                    same_project += 1
                elif row[0] not in blocked:
                    eligible.append(row)
            if not eligible:
                audit.append({"bug_id": target["bug_id"], "group": "C", "slot": slot, "tolerance": tolerance,
                              "event": "EMPTY_ELIGIBLE_FRAME", "unresolved_project_rows": unresolved,
                              "same_project_pool_rows_excluded": same_project})
                continue
            rng = random.Random(seed_for(target["bug_id"], slot))
            row = eligible[rng.randrange(len(eligible))]
            chosen = compact_candidate(
                target, row, slot=slot, seed=seed_for(target["bug_id"], slot),
                matched_to_B_doc_id=matched["doc_id"], matched_to_B_token_count=desired,
                length_tolerance_used=tolerance, eligible_pool_size=len(eligible),
                same_project_pool_rows_excluded=same_project, unresolved_project_rows=unresolved,
                selection_method="uniform_random_offset_within_cross_project_token_length_stratum",
            )
            audit.append({"bug_id": target["bug_id"], "group": "C", "slot": slot, "event": "SELECTED",
                          "candidate_doc_id": row[0], "eligible_pool_size": len(eligible),
                          "same_project_pool_rows_excluded": same_project, "unresolved_project_rows": unresolved,
                          "tolerance": tolerance, "seed": seed_for(target["bug_id"], slot)})
            break
        if chosen is not None:
            selected.append(chosen)
            blocked.add(chosen["doc_id"])
    return selected, audit


def main() -> None:
    source_paths = [
        DATA / "phase2_9_abc_target_issue_inputs.jsonl",
        DATA / "phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl",
        DATA / "phase2_9_abc_top3_similar_PROPOSED.jsonl",
        DATA / "phase2_9_abc_random_context_PROPOSED.jsonl",
        DATA / "phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl",
        DATA / "phase2_10B_eligibility_and_leakage_decision_queue.csv",
        ROOT / "phase2_11_policy_A_adjudication_queue_AI_PROPOSED.csv",
        DATA / "phase2_9_abc_generation_prompt_PROPOSED.txt",
        ROOT / "reports/test_scenario_evaluation_rubric_abc_PROPOSED.md",
        DATA / "gold_references_phase2_6_candidate.jsonl",
    ]
    source_hashes = {str(path.relative_to(ROOT)): file_sha(path) for path in source_paths}
    targets = load_jsonl(source_paths[0])
    target_map = {row["bug_id"]: row for row in targets}
    old_top10 = {row["bug_id"]: row for row in load_jsonl(source_paths[1])}
    old_b = {row["bug_id"]: row for row in load_jsonl(source_paths[2])}
    old_c = {row["bug_id"]: row for row in load_jsonl(source_paths[3])}
    db = sqlite3.connect(DATA / "phase2_9_abc_issue_corpus.sqlite")
    rankings = compute_rankings(db, targets)

    b_records: list[dict] = []
    c_records: list[dict] = []
    manifests: list[dict] = []
    audit_rows: list[dict] = []
    comparison_rows: list[dict] = []
    ranking_mismatches: list[dict] = []
    c_sampling_audit: list[dict] = []
    structural_blockers: list[dict] = []
    selected_leakage_flags: list[dict] = []

    for target in targets:
        bug_id = target["bug_id"]
        target_project = canonical_project(target["project"])
        if target_project is None:
            structural_blockers.append({"bug_id": bug_id, "reason": "UNRESOLVED_TARGET_PROJECT_IDENTITY"})
        ranked = rankings[bug_id]
        authoritative = old_top10[bug_id]["candidates"]
        for index, expected in enumerate(authoritative):
            score, doc_id = ranked[index]
            if doc_id != expected["doc_id"] or abs(round(score, 12) - expected["bm25_score"]) > 1e-10:
                ranking_mismatches.append({"bug_id": bug_id, "rank": index + 1, "expected_doc_id": expected["doc_id"],
                                           "actual_doc_id": doc_id, "expected_score": expected["bm25_score"],
                                           "actual_score": round(score, 12)})

        selected_b: list[dict] = []
        examined: list[dict] = []
        for original_rank, (score, doc_id) in enumerate(ranked, 1):
            row = document(db, doc_id)
            project = canonical_project(row[1])
            reason = project_eligibility(target["project"], row[1], bug_id, doc_id)
            eligible = reason == "ELIGIBLE_CROSS_PROJECT"
            item = compact_candidate(target, row, original_bm25_rank=original_rank,
                                     bm25_score=round(score, 12), cross_project_eligibility=reason)
            post_position = None
            if eligible and len(selected_b) < 3:
                selected_b.append(item)
                post_position = len(selected_b)
                item["post_filter_selection_position"] = post_position
                if item["automated_risk_status"] != "LOW_AUTOMATED_RISK_REVIEW_STILL_REQUIRED":
                    selected_leakage_flags.append({"bug_id": bug_id, "group": "B", "doc_id": doc_id,
                                                   "automated_risk_status": item["automated_risk_status"]})
            examined.append(item)
            audit_rows.append({
                "target_bug_id": bug_id, "target_project": target["project"], "target_canonical_project": target_project or "",
                "group": "B", "source_role": "BM25_RANKED", "candidate_doc_id": doc_id,
                "candidate_project": row[1], "candidate_canonical_project": project or "",
                "project_identity_resolved": project is not None, "original_bm25_rank": original_rank,
                "bm25_score": round(score, 12), "eligibility_result": reason,
                "selected_position": post_position or "", "content_sha256": row[7],
                "automated_risk_status": item["automated_risk_status"], "human_leakage_decision": "",
            })
            if len(selected_b) == 3 and original_rank >= 10:
                break
        if len(selected_b) < 3:
            structural_blockers.append({"bug_id": bug_id, "reason": "FEWER_THAN_3_CROSS_PROJECT_B_WITHIN_RECOMPUTED_DEPTH",
                                        "rank_depth": RANK_DEPTH, "selected": len(selected_b)})
        b_records.append({
            "bug_id": bug_id, "status": "PROPOSED_NOT_APPROVED" if len(selected_b) == 3 else "BLOCKED",
            "method": "Cross-Project Leakage-Filtered BM25 Top-3", "bm25": {"k1": K1, "b": B,
                "query": "target Issue title+body", "corpus": "Phase 2.9 retrospective eligible Issue corpus",
                "tie_break": "score descending then doc_id ascending", "temporal_cutoff": None},
            "target_project": target["project"], "target_canonical_project": target_project,
            "selected_count": len(selected_b), "documents": selected_b, "human_approval": None,
            "fallback_beyond_top10": any(item["original_bm25_rank"] > 10 for item in selected_b),
        })

        selected_c, sample_audit = sample_random(db, target, selected_b)
        c_sampling_audit.extend(sample_audit)
        if len(selected_c) < 3:
            structural_blockers.append({"bug_id": bug_id, "reason": "FEWER_THAN_3_C_RANDOM", "selected": len(selected_c)})
        for item in selected_c:
            audit_rows.append({
                "target_bug_id": bug_id, "target_project": target["project"], "target_canonical_project": target_project or "",
                "group": "C", "source_role": "NEW_RANDOM_SELECTION", "candidate_doc_id": item["doc_id"],
                "candidate_project": item["project"], "candidate_canonical_project": item["canonical_project"] or "",
                "project_identity_resolved": item["canonical_project"] is not None, "original_bm25_rank": "",
                "bm25_score": "", "eligibility_result": "ELIGIBLE_CROSS_PROJECT",
                "selected_position": item["slot"], "content_sha256": item["content_sha256"],
                "automated_risk_status": item["automated_risk_status"], "human_leakage_decision": "",
            })
            if item["automated_risk_status"] != "LOW_AUTOMATED_RISK_REVIEW_STILL_REQUIRED":
                selected_leakage_flags.append({"bug_id": bug_id, "group": "C", "doc_id": item["doc_id"],
                                               "automated_risk_status": item["automated_risk_status"]})
        old_c_docs = sorted(old_c[bug_id]["documents"], key=lambda x: x["slot"])
        for item in old_c_docs:
            project = canonical_project(item["project"])
            result = "EXCLUDED_SAME_PROJECT" if project == target_project else "OLD_ELIGIBLE_CROSS_PROJECT_RESAMPLED_UNDER_NEW_FRAME"
            audit_rows.append({
                "target_bug_id": bug_id, "target_project": target["project"], "target_canonical_project": target_project or "",
                "group": "C", "source_role": "PHASE2_9_OLD_RANDOM", "candidate_doc_id": item["doc_id"],
                "candidate_project": item["project"], "candidate_canonical_project": project or "",
                "project_identity_resolved": project is not None, "original_bm25_rank": "", "bm25_score": "",
                "eligibility_result": result, "selected_position": "", "content_sha256": item["content_sha256"],
                "automated_risk_status": item["automated_risk_status"], "human_leakage_decision": "",
            })
        c_records.append({
            "bug_id": bug_id, "status": "PROPOSED_NOT_APPROVED" if len(selected_c) == 3 else "BLOCKED",
            "method": "deterministic length-matched random sampling from cross-project eligible corpus",
            "global_seed": GLOBAL_SEED, "target_project": target["project"],
            "target_canonical_project": target_project, "selected_count": len(selected_c),
            "documents": selected_c, "human_approval": None,
        })

        old_b_ids = old_b[bug_id]["document_ids"]
        old_c_ids = [item["doc_id"] for item in old_c_docs]
        new_b_ids = [item["doc_id"] for item in selected_b]
        new_c_ids = [item["doc_id"] for item in selected_c]
        for group, old_ids, new_ids in (("B", old_b_ids, new_b_ids), ("C", old_c_ids, new_c_ids)):
            for slot in range(3):
                comparison_rows.append({"target_bug_id": bug_id, "group": group, "slot": slot + 1,
                    "old_doc_id": old_ids[slot] if slot < len(old_ids) else "",
                    "new_doc_id": new_ids[slot] if slot < len(new_ids) else "",
                    "changed": slot >= len(old_ids) or slot >= len(new_ids) or old_ids[slot] != new_ids[slot]})
        manifests.append({
            "target_bug_id": bug_id, "target_issue": {"doc_id": bug_id, "project": target["project"],
                "canonical_project": target_project, "content_sha256": target["content_sha256"]},
            "group_A": {"context_documents": []},
            "group_B": {"documents": [{"doc_id": x["doc_id"], "project": x["project"],
                "content_sha256": x["content_sha256"], "original_bm25_rank": x["original_bm25_rank"],
                "post_filter_selection_position": x["post_filter_selection_position"]} for x in selected_b]},
            "group_C": {"documents": [{"doc_id": x["doc_id"], "project": x["project"],
                "content_sha256": x["content_sha256"], "slot": x["slot"], "seed": x["seed"]} for x in selected_c]},
            "status": "NOT_FROZEN", "human_approval": None,
        })

    db.close()
    audit_path = DATA / "phase2_11D_cross_project_eligibility_audit.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as handle:
        fields = list(audit_rows[0])
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(audit_rows)
    write_jsonl(DATA / "phase2_11D_B_cross_project_top3_PROPOSED.jsonl", b_records)
    write_jsonl(DATA / "phase2_11D_C_cross_project_random3_PROPOSED.jsonl", c_records)
    write_jsonl(DATA / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl", manifests)
    comparison_path = DATA / "phase2_11D_old_vs_new_context_comparison.csv"
    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison_rows[0])); writer.writeheader(); writer.writerows(comparison_rows)

    prompt_hash = file_sha(DATA / "phase2_9_abc_generation_prompt_PROPOSED.txt")
    b_changed = sum([x["doc_id"] for x in b_records[i]["documents"]] != old_b[b_records[i]["bug_id"]]["document_ids"] for i in range(len(b_records)))
    c_changed = sum([x["doc_id"] for x in c_records[i]["documents"]] != [d["doc_id"] for d in sorted(old_c[c_records[i]["bug_id"]]["documents"], key=lambda x: x["slot"])] for i in range(len(c_records)))
    validation = {
        "status": "PASS_STRUCTURAL_CROSS_PROJECT_SELECTION" if not structural_blockers and not ranking_mismatches else "BLOCKED_CROSS_PROJECT_SELECTION",
        "structural_validation_only": True, "official_generation_authorized": False,
        "targets": len(targets), "B_targets_with_k3": sum(x["selected_count"] == 3 for x in b_records),
        "C_targets_with_k3": sum(x["selected_count"] == 3 for x in c_records),
        "all_B_cross_project": all(x["target_canonical_project"] != d["canonical_project"] for x in b_records for d in x["documents"]),
        "all_C_cross_project": all(x["target_canonical_project"] != d["canonical_project"] for x in c_records for d in x["documents"]),
        "B_order_preserved": all([d["original_bm25_rank"] for d in x["documents"]] == sorted(d["original_bm25_rank"] for d in x["documents"]) for x in b_records),
        "B_targets_changed": b_changed, "C_targets_changed": c_changed,
        "B_targets_requiring_beyond_top10": sum(x["fallback_beyond_top10"] for x in b_records),
        "old_B_same_project_records_excluded": sum(r["group"] == "B" and r["eligibility_result"] == "EXCLUDED_SAME_PROJECT" for r in audit_rows),
        "old_C_same_project_records_excluded": sum(r["group"] == "C" and r["source_role"] == "PHASE2_9_OLD_RANDOM" and r["eligibility_result"] == "EXCLUDED_SAME_PROJECT" for r in audit_rows),
        "C_same_project_rows_removed_from_slot_sampling_frames": sum(
            event.get("same_project_pool_rows_excluded", 0) for event in c_sampling_audit if event.get("event") == "SELECTED"
        ),
        "ranking_equivalence_top10": not ranking_mismatches, "ranking_mismatches": ranking_mismatches,
        "prompt_sha256": prompt_hash, "prompt_hash_matches_required": prompt_hash == "d42621f35d0ae6d2b0dcece8291cc686c83fa318e51f2548c73cd7aa5f43206e",
        "selected_automated_leakage_flags_requiring_human_review": selected_leakage_flags,
        "unresolved_structural_blockers": structural_blockers,
        "random_sampling_audit": c_sampling_audit,
        "source_hashes_before_and_after": {name: {"before": digest, "after": file_sha(ROOT / name), "unchanged": digest == file_sha(ROOT / name)} for name, digest in source_hashes.items()},
    }
    (ROOT / "reports/phase2_11D_validation_results.json").write_text(json.dumps(validation, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: validation[k] for k in ("status", "targets", "B_targets_with_k3", "C_targets_with_k3",
        "B_targets_changed", "C_targets_changed", "B_targets_requiring_beyond_top10",
        "old_B_same_project_records_excluded", "old_C_same_project_records_excluded", "ranking_equivalence_top10",
        "prompt_hash_matches_required")}, indent=2))


if __name__ == "__main__":
    main()
