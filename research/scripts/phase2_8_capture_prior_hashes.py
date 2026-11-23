#!/usr/bin/env python3
"""Capture hashes of authoritative Phase 2.6/2.7 inputs used by Phase 2.8."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "data/gold_references_phase2_6_candidate.jsonl",
    "data/phase2_7_retrieval_policy.json",
    "data/phase2_7_review_decisions_TEMPLATE.jsonl",
    "reports/phase2_7_adjudication_packet.md",
    "reports/phase2_7_bm25_leakage_audit.md",
    "reports/phase2_7_cohort_plan.md",
    "reports/phase2_7_completion_report.md",
    "reports/phase2_7_freeze_readiness.md",
    "reports/test_scenario_evaluation_rubric_v3_PROPOSED.md",
    "scripts/phase2_7_bm25_adapter.py",
    "scripts/validate_phase2_7_review_packet.py",
)


def main() -> None:
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
    result = {"purpose": "Phase 2.8 input preservation check", "files": hashes}
    (ROOT / "data/phase2_8_prior_artifact_hashes.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "CAPTURED", "files": len(hashes)}, indent=2))


if __name__ == "__main__":
    main()
