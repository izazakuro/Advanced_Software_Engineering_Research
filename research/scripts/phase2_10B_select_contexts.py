#!/usr/bin/env python3
"""Deterministic Phase 2.10B context selection; relevance fields are never read."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GLOBAL_SEED = 29012026
ELIGIBLE = {"ELIGIBLE", "INCLUDE", "APPROVE", "APPROVED"}
EXCLUDED = {"EXCLUDE", "EXCLUDED", "INELIGIBLE"}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def decisions(path: Path) -> dict[tuple[str, str, str], str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {(r["target_bug_id"], r["group"], r["candidate_doc_id"]): r["human_eligibility_decision"].strip().upper()
                for r in csv.DictReader(handle)}


def select_b(record: dict, decision_map: dict, simulate_all_eligible: bool = False) -> dict:
    chosen, unresolved, excluded = [], [], []
    for candidate in record["group_B"]["authoritative_ranked_candidates"]:
        decision = "ELIGIBLE" if simulate_all_eligible else decision_map.get((record["target_bug_id"], "B", candidate["doc_id"]), "")
        if decision in EXCLUDED:
            excluded.append({"doc_id": candidate["doc_id"], "rank": candidate["rank"]})
            continue
        if decision in ELIGIBLE:
            if len(chosen) < 3:
                chosen.append({"doc_id": candidate["doc_id"], "rank": candidate["rank"], "content_sha256": candidate["content_sha256"]})
            continue
        if len(chosen) < 3:
            unresolved.append({"doc_id": candidate["doc_id"], "rank": candidate["rank"]})
    status = "SELECTED_HYPOTHETICAL_ALL_ELIGIBLE" if simulate_all_eligible else ("SELECTED" if len(chosen) == 3 and not unresolved else "BLOCKED_PENDING_HUMAN_DECISION")
    if len(chosen) < 3 and not unresolved:
        status = "SHORTAGE_TOP10_EXHAUSTED"
    return {"status": status, "selected": chosen, "excluded": excluded, "unresolved_that_can_affect_top3": unresolved}


def replacement_seed(bug_id: str, slot: int, replacement_index: int) -> int:
    raw = hashlib.sha256(f"{GLOBAL_SEED}|{bug_id}|{slot}|replacement|{replacement_index}".encode()).digest()
    return int.from_bytes(raw[:8], "big")


def propose_c_replacement(db: sqlite3.Connection, bug_id: str, slot_doc: dict, blocked: set[str], replacement_index: int = 1) -> dict | None:
    desired = slot_doc["matched_to_similar_token_count"]
    for tolerance in (0.20, 0.50, 1.00):
        low, high = max(1, int(desired * (1 - tolerance))), max(1, int(desired * (1 + tolerance)))
        ids = sorted(blocked)
        placeholders = ",".join("?" for _ in ids)
        clause = f"token_count BETWEEN ? AND ? AND doc_id NOT IN ({placeholders})" if ids else "token_count BETWEEN ? AND ?"
        params = [low, high, *ids]
        rows = db.execute(f"SELECT doc_id, content_sha256, token_count FROM documents WHERE {clause} ORDER BY doc_id", params).fetchall()
        if not rows:
            continue
        rng = random.Random(replacement_seed(bug_id, slot_doc["slot"], replacement_index))
        doc_id, sha, tokens = rows[rng.randrange(len(rows))]
        return {"doc_id": doc_id, "content_sha256": sha, "token_count": tokens, "slot": slot_doc["slot"],
                "seed": replacement_seed(bug_id, slot_doc["slot"], replacement_index), "length_tolerance_used": tolerance,
                "status": "REPLACEMENT_PROPOSAL_REQUIRES_SAME_ELIGIBILITY_REVIEW"}
    return None


def select_c(record: dict, source_random: dict, decision_map: dict, db: sqlite3.Connection, simulate_all_eligible: bool = False) -> dict:
    selected, excluded, unresolved, replacements = [], [], [], []
    originals = sorted(source_random["documents"], key=lambda x: x["slot"])
    blocked = {x["doc_id"] for x in originals} | {x["doc_id"] for x in record["group_B"]["authoritative_ranked_candidates"]}
    for doc in originals:
        decision = "ELIGIBLE" if simulate_all_eligible else decision_map.get((record["target_bug_id"], "C", doc["doc_id"]), "")
        compact = {"doc_id": doc["doc_id"], "slot": doc["slot"], "content_sha256": doc["content_sha256"]}
        if decision in ELIGIBLE:
            selected.append(compact)
        elif decision in EXCLUDED:
            excluded.append(compact)
            proposal = propose_c_replacement(db, record["target_bug_id"], doc, blocked)
            if proposal:
                replacements.append(proposal); blocked.add(proposal["doc_id"])
        else:
            unresolved.append(compact)
    status = "SELECTED_HYPOTHETICAL_ALL_ELIGIBLE" if simulate_all_eligible else ("SELECTED" if len(selected) == 3 else "BLOCKED_PENDING_HUMAN_DECISION")
    return {"status": status, "selected": selected, "excluded": excluded, "unresolved": unresolved,
            "replacement_proposals_not_approved": replacements}


def run(queue: Path, simulate: bool) -> list[dict]:
    manifest = load_jsonl(ROOT / "data/phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl")
    random_source = {x["bug_id"]: x for x in load_jsonl(ROOT / "data/phase2_9_abc_random_context_PROPOSED.jsonl")}
    decision_map = decisions(queue)
    db = sqlite3.connect(ROOT / "data/phase2_9_abc_issue_corpus.sqlite")
    out = []
    for record in manifest:
        out.append({"target_bug_id": record["target_bug_id"], "group_B": select_b(record, decision_map, simulate),
                    "group_C": select_c(record, random_source[record["target_bug_id"]], decision_map, db, simulate),
                    "official": False if simulate else None})
    db.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=ROOT / "data/phase2_10B_eligibility_and_leakage_decision_queue.csv")
    parser.add_argument("--simulate-all-eligible", action="store_true", help="Synthetic engineering mode only; never an approval")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.queue, args.simulate_all_eligible)
    text = "".join(json.dumps(x, sort_keys=True) + "\n" for x in result)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    summary = {"targets": len(result), "simulate_all_eligible": args.simulate_all_eligible,
               "B_status": {s: sum(x["group_B"]["status"] == s for x in result) for s in sorted({x["group_B"]["status"] for x in result})},
               "C_status": {s: sum(x["group_C"]["status"] == s for x in result) for s in sorted({x["group_C"]["status"] for x in result})}}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
