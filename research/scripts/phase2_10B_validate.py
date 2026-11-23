#!/usr/bin/env python3
"""Structural validation for Phase 2.10B outputs; makes no semantic correctness claim."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    errors = []
    rec = json.loads((ROOT / "data/phase2_10B_artifact_reconciliation.json").read_text())
    manifest = jsonl(ROOT / "data/phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl")
    with (ROOT / "data/phase2_10B_eligibility_and_leakage_decision_queue.csv").open(encoding="utf-8", newline="") as f:
        queue = list(csv.DictReader(f))
    config = json.loads((ROOT / "data/phase2_10B_experiment_config_PROPOSED.json").read_text())
    dry = json.loads((ROOT / "data/phase2_10B_dry_run_results.json").read_text())
    if len(manifest) != 18 or len({x["target_bug_id"] for x in manifest}) != 18:
        errors.append("validated manifest must have 18 unique targets")
    if len(queue) != 234 or sum(x["group"] == "B" for x in queue) != 180 or sum(x["group"] == "C" for x in queue) != 54:
        errors.append("decision queue counts mismatch")
    if any(x["human_eligibility_decision"] for x in queue):
        errors.append("human decisions were populated")
    if any(x["low_relevance_exclusion_permitted"] != "False" for x in queue):
        errors.append("queue permits low-relevance exclusion")
    if not rec["verified_facts"]["all_review_source_fields_match"] or rec["source_mismatches"]:
        errors.append("source reconciliation mismatch")
    expected_discrepancy = rec["manifest_discrepancies"]
    if len(expected_discrepancy) != 1 or expected_discrepancy[0]["target_bug_id"] != "rptools/maptool#2416":
        errors.append("unexpected supplied-manifest discrepancy set")
    for item in manifest:
        ranks = [x["rank"] for x in item["group_B"]["authoritative_ranked_candidates"]]
        if ranks != list(range(1, 11)) or item["human_approval"] is not None or item["group_B"]["official_selection"] is not None:
            errors.append(f"{item['target_bug_id']}: rank/approval/freeze invariant failed")
    prompt = ROOT / config["prompt"]["path"]
    if hashlib.sha256(prompt.read_bytes()).hexdigest() != config["prompt"]["actual_sha256"] or not config["prompt"]["hash_matches"]:
        errors.append("prompt hash mismatch")
    if config["official_generation_authorized"] or config["human_approval"] is not None:
        errors.append("experiment was authorized/frozen")
    if dry["planned_records"] != 162 or dry["official_model_calls"] != 0 or not dry["prompt_hash_verified"]:
        errors.append("dry-run count/call/hash invariant failed")
    result = {"status": "PASS_STRUCTURAL_ENGINEERING_ONLY" if not errors else "FAIL", "targets": len(manifest),
              "decision_queue_rows": len(queue), "priority_human_cases": rec["verified_facts"]["priority_duplicate_or_leakage_pairs"],
              "planned_generations": dry["planned_records"], "official_model_calls": dry["official_model_calls"],
              "semantic_correctness_claimed": False, "experiment_frozen": False, "errors": errors}
    path = ROOT / "data/phase2_10B_validation_results.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
