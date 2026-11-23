#!/usr/bin/env python3
"""Synthetic tests for rank-preserving Phase 2.10B selection."""

from __future__ import annotations

import unittest

from phase2_10B_select_contexts import replacement_seed, select_b


def record() -> dict:
    return {"target_bug_id": "p/t#1", "group_B": {"authoritative_ranked_candidates": [
        {"doc_id": f"p/c#{i}", "rank": i, "content_sha256": str(i), "bm25_score": 11-i,
         "automated_risk_status": "ANY"} for i in range(1, 11)]}}


class SelectionTests(unittest.TestCase):
    def test_all_eligible_is_original_top3(self) -> None:
        d = {("p/t#1", "B", f"p/c#{i}"): "ELIGIBLE" for i in range(1, 11)}
        self.assertEqual([x["rank"] for x in select_b(record(), d)["selected"]], [1, 2, 3])

    def test_exclusion_uses_next_original_rank(self) -> None:
        d = {("p/t#1", "B", f"p/c#{i}"): "ELIGIBLE" for i in range(1, 11)}
        d[("p/t#1", "B", "p/c#2")] = "EXCLUDED"
        self.assertEqual([x["rank"] for x in select_b(record(), d)["selected"]], [1, 3, 4])

    def test_pending_decision_blocks(self) -> None:
        d = {("p/t#1", "B", "p/c#1"): "ELIGIBLE"}
        self.assertEqual(select_b(record(), d)["status"], "BLOCKED_PENDING_HUMAN_DECISION")

    def test_relevance_is_not_an_input(self) -> None:
        rec = record()
        for i, item in enumerate(rec["group_B"]["authoritative_ranked_candidates"]):
            item["ai_relevance_0_3"] = 0 if i < 3 else 3
        d = {("p/t#1", "B", f"p/c#{i}"): "ELIGIBLE" for i in range(1, 11)}
        self.assertEqual([x["rank"] for x in select_b(rec, d)["selected"]], [1, 2, 3])

    def test_replacement_seed_is_deterministic_and_slot_specific(self) -> None:
        self.assertEqual(replacement_seed("p/t#1", 1, 1), replacement_seed("p/t#1", 1, 1))
        self.assertNotEqual(replacement_seed("p/t#1", 1, 1), replacement_seed("p/t#1", 2, 1))


if __name__ == "__main__":
    unittest.main()
