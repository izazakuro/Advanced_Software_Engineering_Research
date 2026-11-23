#!/usr/bin/env python3
"""Select deterministic length-matched random Issue context for Group C."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from pathlib import Path

from phase2_9_bm25_retrieve import risk


ROOT = Path(__file__).resolve().parents[1]
GLOBAL_SEED = 29012026


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def seed_for(bug_id: str, slot: int) -> int:
    raw = hashlib.sha256(f"{GLOBAL_SEED}|{bug_id}|{slot}".encode()).digest()
    return int.from_bytes(raw[:8], "big")


def main() -> None:
    targets = {x["bug_id"]: x for x in jsonl(ROOT / "data/phase2_9_abc_target_issue_inputs.jsonl")}
    similar = jsonl(ROOT / "data/phase2_9_abc_top3_similar_PROPOSED.jsonl")
    db = sqlite3.connect(ROOT / "data/phase2_9_abc_issue_corpus.sqlite")
    records = []
    for top3 in similar:
        bug_id = top3["bug_id"]
        target = targets[bug_id]
        blocked = set(top3["document_ids"])
        selected = []
        for slot, similar_doc in enumerate(top3["documents"], 1):
            desired = similar_doc["token_count"]
            chosen = None
            for tolerance in (0.20, 0.50, 1.00):
                low = max(1, int(desired * (1 - tolerance)))
                high = max(low, int(desired * (1 + tolerance)))
                blocked_now = sorted(blocked | {x["doc_id"] for x in selected})
                placeholders = ",".join("?" for _ in blocked_now)
                where = f"token_count BETWEEN ? AND ? AND doc_id NOT IN ({placeholders})"
                params = [low, high, *blocked_now]
                frame = db.execute(f"SELECT count(*) FROM documents WHERE {where}", params).fetchone()[0]
                if not frame:
                    continue
                rng = random.Random(seed_for(bug_id, slot))
                tried = set()
                for _ in range(min(200, frame)):
                    offset = rng.randrange(frame)
                    if offset in tried:
                        continue
                    tried.add(offset)
                    row = db.execute(
                        f"SELECT doc_id, project, issue_number, title, body, created_at, token_count, content_sha256 FROM documents WHERE {where} ORDER BY doc_id LIMIT 1 OFFSET ?",
                        [*params, offset],
                    ).fetchone()
                    candidate = risk(target, row)
                    if candidate["automated_risk_status"] != "LOW_AUTOMATED_RISK_REVIEW_STILL_REQUIRED":
                        continue
                    candidate.update({"slot": slot, "seed": seed_for(bug_id, slot),
                                      "matched_to_similar_doc_id": similar_doc["doc_id"],
                                      "matched_to_similar_token_count": desired,
                                      "length_tolerance_used": tolerance,
                                      "sampling_frame_count": frame,
                                      "selection_method": "uniform_random_offset_within_token_length_stratum",
                                      "human_approval": None})
                    chosen = candidate
                    break
                if chosen:
                    break
            if chosen:
                selected.append(chosen)
        records.append({"bug_id": bug_id, "global_seed": GLOBAL_SEED, "requested_k": 3,
                        "selected_count": len(selected),
                        "status": "PROPOSED_MACHINE_NOT_HUMAN_APPROVED" if len(selected) == 3 else "RANDOM_CONTEXT_INCOMPLETE",
                        "documents": selected, "human_approval": None})
    output = ROOT / "data/phase2_9_abc_random_context_PROPOSED.jsonl"
    output.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in records), encoding="utf-8")
    print(json.dumps({"targets": len(records), "targets_with_k3": sum(x["selected_count"] == 3 for x in records),
                      "incomplete_targets": sum(x["selected_count"] < 3 for x in records),
                      "global_seed": GLOBAL_SEED, "human_approved": 0}, indent=2))


if __name__ == "__main__":
    main()
