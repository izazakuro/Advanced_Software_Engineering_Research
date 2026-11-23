#!/usr/bin/env python3
"""Synthetic/non-study engineering tests for Phase 2.7 BM25 safeguards."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from phase2_7_bm25_adapter import content_sha256, search


ROOT = Path(__file__).resolve().parents[1]
CUTOFF = "2020-01-01T00:00:00Z"


def document(doc_id: str, content: str, timestamp: str = "2019-01-01T00:00:00Z", **overrides):
    value = {"document_id": doc_id, "source": "synthetic_fixture", "content": content,
             "content_timestamp": timestamp, "content_timestamp_verified": True,
             "snapshot_verified": True, "linked_bug_ids": [], "linked_repair_ids": [],
             "repair_only": False}
    value.update(overrides)
    return value


def main() -> int:
    target_text = "synthetic target crash"
    docs = [
        document("eligible-a", "alpha renderer alpha crash"),
        document("eligible-b", "alpha renderer fallback"),
        document("post-cutoff", "alpha future", "2021-01-01T00:00:00Z"),
        document("at-cutoff", "alpha boundary", CUTOFF),
        document("linked-target", "alpha linked", linked_bug_ids=["synthetic/project#1"]),
        document("linked-repair", "alpha repair", linked_repair_ids=["synthetic/pr#2"]),
        document("repair-only", "alpha patch detail", repair_only=True),
        document("unverified-time", "alpha uncertain", content_timestamp_verified=False),
        document("unverified-snapshot", "alpha edited later", snapshot_verified=False),
        document("target-duplicate", target_text),
    ]
    args = {"target_bug_id": "synthetic/project#1", "cutoff": CUTOFF,
            "target_content_hashes": {content_sha256(target_text)}, "top_k": 2}
    first = search(docs, "alpha renderer crash", **args)
    second = search(list(reversed(docs)), "alpha renderer crash", **args)
    reasons = {row["document_id"]: row["filter_reason"] for row in first["screened"]}
    checks = {
        "strictly_pre_cutoff_only": reasons["post-cutoff"] == reasons["at-cutoff"] == "not_strictly_pre_cutoff",
        "target_link_excluded": reasons["linked-target"] == "target_bug_linked",
        "linked_repair_excluded": reasons["linked-repair"] == "repair_record_linked",
        "repair_only_excluded": reasons["repair-only"] == "repair_only",
        "unverified_time_excluded": reasons["unverified-time"] == "content_timestamp_unverified",
        "future_edit_snapshot_excluded": reasons["unverified-snapshot"] == "historical_snapshot_unverified",
        "exact_target_duplicate_excluded": reasons["target-duplicate"] == "exact_target_content_duplicate",
        "only_expected_docs_eligible": first["eligible_count"] == 2,
        "deterministic_top_k": first["results"] == second["results"],
        "per_result_provenance": all({"document_id", "source", "content_timestamp", "cutoff", "filter_reason", "bm25_score", "sha256"} <= set(r) for r in first["results"]),
    }
    output = {"fixture_scope": "synthetic/non-study only", "status": "PASS" if all(checks.values()) else "FAIL",
              "checks": checks, "top_k_results": first["results"], "screened": first["screened"]}
    (ROOT / "data/phase2_7_bm25_synthetic_test_results.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
