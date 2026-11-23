#!/usr/bin/env python3
"""Build restricted CSPC queue blueprints without creating real anonymous IDs."""

from __future__ import annotations

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
P3C = RESEARCH / "outputs/phase3c_pre_pilot_review"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def build(schedule_path: Path) -> list[dict]:
    eligibility = json.loads((FROZEN / "gold_v1_metric_eligibility.json").read_text())
    paths = {x["bug_id"]: x["cspc_eligible_path_ids"] for x in eligibility["per_record"]}
    rows = []
    for task in jsonl(schedule_path):
        for path_id in paths[task["target_bug_id"]]:
            rows.append({
                "restricted_task_id": task["task_id"],
                "bug_id": task["target_bug_id"],
                "gold_path_id": path_id,
                "anonymous_response_id": "PENDING_SECURE_POST_GENERATION_MAPPING",
                "review_requirement": "MANDATORY_FULL_HUMAN_CSPC_VERIFICATION",
                "verification_status": "PENDING_HUMAN_VERIFICATION",
                "evaluator_visibility": "RESTRICTED_BLUEPRINT_NOT_FOR_AI_OR_HUMAN_REVIEW_UI",
            })
    return rows


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in rows))


def main() -> None:
    formal = build(P3C / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl")
    pilot = build(P3C / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl")
    if len(formal) != 108 or len(pilot) != 15:
        raise SystemExit(f"unexpected CSPC queue cardinality formal={len(formal)} pilot={len(pilot)}")
    write(HERE / "phase3c_h_formal_cspc_queue_plan_108_RESTRICTED.jsonl", formal)
    write(HERE / "phase3c_h_pilot_cspc_queue_plan_15_RESTRICTED.jsonl", pilot)
    print(json.dumps({"status": "QUEUE_BLUEPRINTS_READY_NOT_BLINDED_NOT_AUTHORIZED", "formal_items": 108, "pilot_items": 15, "real_outputs": 0}))


if __name__ == "__main__":
    main()
