#!/usr/bin/env python3
"""Offline tests for Phase 2.11D cross-project selection artifacts."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from phase2_11D_cross_project_select import (
    DATA,
    canonical_project,
    load_jsonl,
    project_eligibility,
    sample_random,
    seed_for,
)


class CrossProjectSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.targets = {x["bug_id"]: x for x in load_jsonl(DATA / "phase2_9_abc_target_issue_inputs.jsonl")}
        cls.b = load_jsonl(DATA / "phase2_11D_B_cross_project_top3_PROPOSED.jsonl")
        cls.c = load_jsonl(DATA / "phase2_11D_C_cross_project_random3_PROPOSED.jsonl")
        cls.validation = json.loads((DATA.parent / "reports/phase2_11D_validation_results.json").read_text())

    def test_same_project_exclusion_and_normalization(self) -> None:
        self.assertEqual(canonical_project("https://github.com/Owner/Repo.git"), "owner/repo")
        self.assertEqual(project_eligibility("Owner/Repo", "owner/repo", "Owner/Repo#1", "owner/repo#2"), "EXCLUDED_SAME_PROJECT")

    def test_cross_project_low_relevance_is_retained(self) -> None:
        # Eligibility has no relevance input: a cross-project identity is retained.
        self.assertEqual(project_eligibility("a/target", "b/unrelated", "a/target#1", "b/unrelated#9"), "ELIGIBLE_CROSS_PROJECT")

    def test_ranking_preservation_and_top10_fallback(self) -> None:
        for record in self.b:
            ranks = [x["original_bm25_rank"] for x in record["documents"]]
            self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(sum(x["fallback_beyond_top10"] for x in self.b), 14)
        self.assertTrue(self.validation["ranking_equivalence_top10"])

    def test_random_seed_and_sampling_reproducibility(self) -> None:
        self.assertEqual(seed_for("a/b#1", 1), seed_for("a/b#1", 1))
        record = self.b[0]
        db = sqlite3.connect(DATA / "phase2_9_abc_issue_corpus.sqlite")
        first, _ = sample_random(db, self.targets[record["bug_id"]], record["documents"])
        second, _ = sample_random(db, self.targets[record["bug_id"]], record["documents"])
        db.close()
        self.assertEqual([x["doc_id"] for x in first], [x["doc_id"] for x in second])

    def test_unresolved_project_identity_is_fail_closed(self) -> None:
        self.assertIsNone(canonical_project("not-a-repository"))
        self.assertEqual(project_eligibility("a/b", "not-a-repository", "a/b#1", "unknown#2"),
                         "REVIEW_UNRESOLVED_PROJECT_IDENTITY")

    def test_no_duplicate_or_target_context(self) -> None:
        c_map = {x["bug_id"]: x for x in self.c}
        for b_record in self.b:
            bug_id = b_record["bug_id"]
            b_ids = [x["doc_id"] for x in b_record["documents"]]
            c_ids = [x["doc_id"] for x in c_map[bug_id]["documents"]]
            self.assertEqual(len(b_ids), len(set(b_ids)))
            self.assertEqual(len(c_ids), len(set(c_ids)))
            self.assertFalse(set(b_ids) & set(c_ids))
            self.assertNotIn(bug_id, b_ids + c_ids)

    def test_every_selection_is_cross_project_and_hash_present(self) -> None:
        for collection in (self.b, self.c):
            for record in collection:
                target_project = canonical_project(self.targets[record["bug_id"]]["project"])
                self.assertEqual(len(record["documents"]), 3)
                for item in record["documents"]:
                    self.assertNotEqual(target_project, canonical_project(item["project"]))
                    self.assertRegex(item["content_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
