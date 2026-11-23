#!/usr/bin/env python3
"""Apply the researcher's explicit accept-all decision to the 36 CSPC rows."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "stage1_ai_evaluation" / "stage1_cspc_human_verification_preparation"
WORKBOOK = PACKAGE / "cspc_human_verification_workbook.csv"
BASELINE = PACKAGE / "cspc_review_baseline_immutable.jsonl"
RECORD = PACKAGE / "human_cspc_accept_all_application_record.json"
REVIEWER_ID = "RESEARCHER_ACCEPT_ALL_36"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    before = sha(WORKBOOK)
    with WORKBOOK.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if len(rows) != 36 or len({r["review_item_id"] for r in rows}) != 36:
        raise ValueError("Expected exactly 36 unique CSPC review rows")
    if any((r.get("human_decision") or "").strip() for r in rows):
        raise ValueError("Workbook already contains a human decision; refusing in-place reapplication")

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    for row in rows:
        row["human_decision"] = "ACCEPT"
        row["human_correction_reason"] = ""
        row["reviewer_id"] = REVIEWER_ID
        row["review_timestamp_utc"] = timestamp

    temporary = WORKBOOK.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(WORKBOOK)

    record = {
        "phase": "Phase 3D Stage 1 Human CSPC Verification",
        "authorization": "Researcher explicitly accepted all 36 AI CSPC judgments without overrides.",
        "review_process": "AI-assisted review followed by a single researcher accept-all decision.",
        "independent_human_evaluators_claimed": False,
        "independent_blinded_adjudication_claimed": False,
        "reviewer_id": REVIEWER_ID,
        "recording_timestamp_utc": timestamp,
        "record_count": 36,
        "decisions": {"ACCEPT": 36, "OVERRIDE_TO_0": 0, "OVERRIDE_TO_1": 0, "UNRESOLVED": 0},
        "correction_reasons_nonempty": 0,
        "immutable_baseline_sha256": sha(BASELINE),
        "workbook_sha256_before": before,
        "workbook_sha256_after": sha(WORKBOOK),
        "score_locked": False,
        "unblinded": False,
    }
    RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
