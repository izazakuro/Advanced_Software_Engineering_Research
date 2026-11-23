#!/usr/bin/env python3
"""Freeze Gold References only after explicit complete human decisions.

Expected decision JSONL fields per frozen bug:
  bug_id, decision (Approve|Insufficient Evidence), reviewer, reviewed_at,
  notes, evidence_reviewed=true, oracle_reviewed=true, leakage_reviewed=true.
For Insufficient Evidence, exclusion_reason is mandatory. `Revise` is not a
freezable decision; revise the draft, revalidate, and review again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve(); parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=here.parents[1])
    parser.add_argument("--dataset-root", type=Path, default=here.parents[2] / "OSSGameBench")
    parser.add_argument("--decisions", type=Path, default=here.parents[1] / "data" / "human_review_decisions.jsonl")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def refuse(message: str) -> int:
    print(f"REFUSED: {message}", file=sys.stderr); return 2


def main() -> int:
    args = parse_args(); root = args.research_root.resolve(); decisions_path = args.decisions.resolve()
    refined_path = root / "data" / "gold_references_refined_draft.jsonl"
    outputs = [root / "data" / "gold_references_frozen.jsonl", root / "data" / "frozen_sample_manifest.json", root / "data" / "freeze_manifest.sha256"]
    if any(path.exists() for path in outputs):
        return refuse("frozen outputs already exist; version them explicitly instead of overwriting")
    if not decisions_path.is_file():
        return refuse(f"human decision file is missing: {decisions_path}")

    validation = subprocess.run([
        sys.executable, str(root / "scripts" / "validate_refined_gold_references.py"),
        "--research-root", str(root), "--dataset-root", str(args.dataset_root.resolve()),
    ], text=True, capture_output=True)
    if validation.returncode:
        print(validation.stdout, file=sys.stderr); print(validation.stderr, file=sys.stderr)
        return refuse("refined validation did not pass")

    refined = read_jsonl(refined_path); decisions = read_jsonl(decisions_path)
    by_id = {r["bug_id"]: r for r in refined}; decision_by = {d.get("bug_id"): d for d in decisions}
    if len(decisions) != 18 or len(decision_by) != 18 or set(decision_by) != set(by_id):
        return refuse("decisions must contain exactly one explicit record for every one of the 18 frozen bug IDs")
    errors = []
    for bug_id, decision in decision_by.items():
        if decision.get("decision") not in {"Approve", "Insufficient Evidence"}:
            errors.append(f"{bug_id}: decision must be Approve or Insufficient Evidence; Revise cannot be frozen")
        for field in ("reviewer", "reviewed_at", "notes"):
            if not decision.get(field): errors.append(f"{bug_id}: missing {field}")
        for field in ("evidence_reviewed", "oracle_reviewed", "leakage_reviewed"):
            if decision.get(field) is not True: errors.append(f"{bug_id}: {field} must be explicitly true")
        if decision.get("decision") == "Insufficient Evidence" and not decision.get("exclusion_reason"):
            errors.append(f"{bug_id}: exclusion_reason is required")
    if errors:
        return refuse("incomplete human gate:\n- " + "\n- ".join(errors))

    frozen = []
    approved_ids = []; excluded = []
    for record in refined:
        item = json.loads(json.dumps(record)); decision = decision_by[item["bug_id"]]
        item["final_human_decision"] = decision["decision"]
        item["annotation_status"] = "approved" if decision["decision"] == "Approve" else "excluded_insufficient_evidence"
        item["human_review"] = {k: decision[k] for k in decision}
        frozen.append(item)
        if decision["decision"] == "Approve": approved_ids.append(item["bug_id"])
        else: excluded.append({"bug_id": item["bug_id"], "reason": decision["exclusion_reason"], "reviewer": decision["reviewer"], "reviewed_at": decision["reviewed_at"]})

    frozen_path = outputs[0]; sample_path = outputs[1]; manifest_path = outputs[2]
    frozen_path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in frozen), encoding="utf-8")
    sample = {"original_sample_size": 18, "effective_sample_size": len(approved_ids), "approved_bug_ids": approved_ids, "excluded_bugs": excluded, "substitution_performed": False}
    sample_path.write_text(json.dumps(sample, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest_inputs = [
        decisions_path, refined_path, root / "data" / "leakage_manifest.jsonl",
        root / "reports" / "test_scenario_evaluation_rubric.md", frozen_path, sample_path,
    ]
    manifest_path.write_text("".join(f"{sha256(path)}  {path.relative_to(root) if path.is_relative_to(root) else path}\n" for path in manifest_inputs), encoding="utf-8")
    print(json.dumps({"status": "FROZEN", "approved": len(approved_ids), "excluded": len(excluded), "effective_sample_size": len(approved_ids), "manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
