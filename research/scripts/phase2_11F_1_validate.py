#!/usr/bin/env python3
"""Independent read-only validation for the Phase 2.11F-1 package."""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "data/phase2_11F_1_gold_evidence"
ARCHIVE = ROOT / "phase2_11F_1_gold_reference_evidence_package.zip"
HANDOFF = ROOT / "phase2_11E_9_codex_handoff"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    errors: list[str] = []
    evidence = jsonl(EXPORT / "phase2_11F_1_gold_evidence.jsonl")
    validation = json.loads((EXPORT / "phase2_11F_1_validation.json").read_text(encoding="utf-8"))
    with (EXPORT / "phase2_11F_1_gold_review_queue.csv").open(encoding="utf-8", newline="") as handle:
        queue = list(csv.DictReader(handle))
    manifest = jsonl(HANDOFF / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl")
    expected_ids = [row["target_bug_id"] for row in manifest]
    if len(evidence) != 18 or [row["bug_id"] for row in evidence] != expected_ids:
        errors.append("Gold evidence target count/order mismatch")
    if len(queue) != 18 or [row["bug_id"] for row in queue] != expected_ids:
        errors.append("review queue target count/order mismatch")
    if any(row["human_decision"] or row["human_reason"] or row["reviewer_name"] or row["review_timestamp"] for row in queue):
        errors.append("review queue contains fabricated human review")
    candidates = {row["bug_id"]: row for row in jsonl(ROOT / "data/gold_references_phase2_6_candidate.jsonl")}
    targets = {row["doc_id"]: row for row in jsonl(ROOT / "data/phase2_11E_evidence/phase2_11E_target_issues.jsonl")}
    supporting_count = 0
    source_count = 0
    for row in evidence:
        bug_id = row["bug_id"]
        if row["human_decision"] is not None or row["gold_frozen"]:
            errors.append(f"{bug_id}: approval/freeze invariant failed")
        if not row["traceability_audit"]["traceability_passed"]:
            errors.append(f"{bug_id}: traceability failed")
        if json.loads(row["authoritative_gold_source"]["raw_json_line_verbatim"]) != candidates[bug_id]:
            errors.append(f"{bug_id}: authoritative raw Gold mismatch")
        if canonical_digest(candidates[bug_id]) != row["authoritative_gold_source"]["record_sha256"]:
            errors.append(f"{bug_id}: authoritative record hash mismatch")
        target = targets[bug_id]
        if row["target_issue"]["title"] != target["title"] or row["target_issue"]["body"] != target["body"]:
            errors.append(f"{bug_id}: target text mismatch")
        supporting_count += len(row["supporting_gold_evidence"])
        source_count += len(row["supporting_source_records"])
    if supporting_count != 135 or source_count != 44:
        errors.append("supporting evidence/source-record counts mismatch")
    with (HANDOFF / "approved_pairs_108.csv").open(encoding="utf-8", newline="") as handle:
        approvals = list(csv.DictReader(handle))
    if len(approvals) != 108 or any(row["human_decision"] != "APPROVE" for row in approvals):
        errors.append("E-9 final eligibility approvals changed or incomplete")
    current_changed = []
    for name, item in validation["source_integrity"].items():
        if sha(ROOT / name) != item["before"]:
            current_changed.append(name)
    if current_changed:
        errors.append(f"source artifacts changed after export: {current_changed}")
    names = sorted(path.name for path in EXPORT.iterdir() if path.is_file())
    with zipfile.ZipFile(ARCHIVE) as archive:
        if archive.testzip():
            errors.append("ZIP CRC failure")
        if sorted(Path(name).name for name in archive.namelist()) != names:
            errors.append("ZIP entry set mismatch")
        for name in archive.namelist():
            if archive.read(name) != (EXPORT / Path(name).name).read_bytes():
                errors.append(f"ZIP bytes mismatch: {name}")
    governance = validation["governance"]
    if governance != {"ABC_manifest_frozen": False, "gold_frozen": False, "human_gold_approvals_recorded": 0,
                      "official_generation_authorized": False, "official_model_calls": 0, "rubric_approved": False}:
        errors.append("governance invariant mismatch")
    result = {"status": "PASS_GOLD_EVIDENCE_EXPORT" if not errors else "BLOCKED_GOLD_EVIDENCE_EXPORT",
              "gold_records": len(evidence), "queue_rows": len(queue), "supporting_evidence_items": supporting_count,
              "supporting_source_records": source_count,
              "recommendations": dict(__import__("collections").Counter(row["ai_review"]["recommendation"] for row in evidence)),
              "E9_approved_pairs_preserved": len(approvals) == 108 and all(row["human_decision"] == "APPROVE" for row in approvals),
              "zip_entries": len(names), "zip_sha256": sha(ARCHIVE), "official_model_calls": 0, "errors": errors}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
