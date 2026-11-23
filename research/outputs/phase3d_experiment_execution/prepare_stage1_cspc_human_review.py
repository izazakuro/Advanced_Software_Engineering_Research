#!/usr/bin/env python3
"""Build a blinded, human-readable CSPC review package without changing AI data."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EVAL_ROOT = ROOT / "stage1_ai_evaluation"
SOURCE_QUEUE = EVAL_ROOT / "human_cspc_review_package" / "repeat1_cspc_human_verification_queue.jsonl"
GOLD = ROOT.parent / "gold_v1_frozen" / "gold_v1.jsonl"
RUBRIC = ROOT.parent / "gold_v1_frozen" / "strict_oracle_rubric_v1.json"
OUT = EVAL_ROOT / "stage1_cspc_human_verification_preparation"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def selected_scenario(item: dict) -> dict | None:
    scenario_id = item["ai_cspc_path_assessment"].get("matching_scenario_id")
    if scenario_id is None:
        return None
    matches = [s for s in item["generated_scenarios"] if s.get("scenario_id") == scenario_id]
    if len(matches) != 1:
        raise ValueError(f"{item['review_item_id']}: expected exactly one selected scenario {scenario_id!r}")
    return matches[0]


def condition_projection(condition: dict) -> dict:
    return {
        "condition_id": condition.get("condition_id"),
        "role": condition.get("role"),
        "description": condition.get("description"),
        "evidence_ids": condition.get("evidence_ids", []),
    }


def build_record(item: dict, gold: dict) -> dict:
    path = item["gold_path"]
    conditions = {c["condition_id"]: c for c in gold["trigger_conditions"]}
    ordered_ids = path.get("ordered_condition_ids", [])
    missing_ids = [cid for cid in ordered_ids if cid not in conditions]
    if missing_ids:
        raise ValueError(f"{item['review_item_id']}: Gold condition IDs missing: {missing_ids}")

    setup = [condition_projection(conditions[cid]) for cid in path.get("setup_condition_ids", [])]
    triggers = [condition_projection(conditions[cid]) for cid in path.get("required_condition_ids", [])]
    order = [condition_projection(conditions[cid]) for cid in ordered_ids]
    scenario = selected_scenario(item)
    assessment = item["ai_cspc_path_assessment"]

    gap_entries = [
        e for e in assessment.get("evidence", [])
        if e.get("support_type") not in {"SUPPORTED"}
    ]
    failed_checks = [
        name for name in (
            "required_setup_covered",
            "trigger_coverage_complete",
            "execution_order_valid",
            "expected_observation_covered",
            "path_complete",
        )
        if assessment.get(name) is not True
    ]
    gaps = {
        "non_supported_ai_evidence_entries": gap_entries,
        "failed_or_nonaffirmative_ai_checks": failed_checks,
        "ai_path_uncertain": item.get("ai_path_uncertain"),
        "ai_response_uncertainty_flags": item.get("ai_response_uncertainty_flags", []),
        "selected_scenario_uncertainty_or_unsupported_assumptions": (
            scenario.get("uncertainty_or_unsupported_assumptions", []) if scenario else []
        ),
    }

    ai_judgment = 1 if assessment.get("path_complete") is True else 0
    immutable = {
        "review_item_id": item["review_item_id"],
        "anonymous_response_id": item["anonymous_response_id"],
        "bug_id": item["bug_id"],
        "gold_path_id": item["gold_path_id"],
        "required_setup": setup,
        "required_triggers": triggers,
        "required_action_order": order,
        "expected_observation": {
            "expected_behavior": item["expected_behavior"],
            "gold_oracles_for_path": item.get("gold_oracles_for_path", []),
        },
        "selected_generated_scenario": scenario,
        "generated_scenarios_considered_when_no_selection": (
            item["generated_scenarios"] if scenario is None else []
        ),
        "ai_cspc_judgment": ai_judgment,
        "ai_cspc_path_assessment": assessment,
        "exact_ai_supporting_evidence": assessment.get("evidence", []),
        "missing_or_contradictory_requirements": gaps,
    }
    return {
        **immutable,
        "immutable_ai_and_evidence_sha256": canonical_hash(immutable),
        "human_decision": "",
        "human_correction_reason": "",
        "reviewer_id": "",
        "review_timestamp_utc": "",
    }


def markdown_record(index: int, r: dict) -> str:
    scenario = r["selected_generated_scenario"]
    if scenario is None:
        scenario_section = f"""The AI selected no matching scenario (`matching_scenario_id = null`).
All complete generated scenarios considered by the AI are retained below:

```json
{pretty(r['generated_scenarios_considered_when_no_selection'])}
```"""
    else:
        steps = "\n".join(
            f"   {step.get('step')}. {step.get('action')}"
            for step in scenario.get("ordered_execution_steps", [])
        ) or "   (none)"
        scenario_section = f"""- Preconditions: {pretty(scenario.get('preconditions', []))}
- Trigger conditions: {pretty(scenario.get('trigger_conditions', []))}
- Environment assumptions: {pretty(scenario.get('environment_assumptions', []))}
- Ordered execution steps:
{steps}
- Expected observable behavior: {scenario.get('expected_observable_behavior')}
- Test oracle: {scenario.get('test_oracle')}
- Uncertainty/unsupported assumptions: {pretty(scenario.get('uncertainty_or_unsupported_assumptions', []))}"""
    return f"""## {index}. {r['review_item_id']}

- Anonymous response: `{r['anonymous_response_id']}`
- Bug: `{r['bug_id']}`
- Gold Path: `{r['gold_path_id']}`
- AI CSPC judgment: **{r['ai_cspc_judgment']}** (immutable)
- Immutable record hash: `{r['immutable_ai_and_evidence_sha256']}`

### Gold requirements

Required setup:

```json
{pretty(r['required_setup'])}
```

Required triggers:

```json
{pretty(r['required_triggers'])}
```

Required action order:

```json
{pretty(r['required_action_order'])}
```

Expected observation:

```json
{pretty(r['expected_observation'])}
```

### Complete AI-selected generated scenario `{scenario.get('scenario_id') if scenario else 'NONE'}`

{scenario_section}

### Exact AI supporting evidence

```json
{pretty(r['exact_ai_supporting_evidence'])}
```

### Missing or contradictory requirements

```json
{pretty(r['missing_or_contradictory_requirements'])}
```

### Human decision — enter in CSV workbook

- Allowed decision: `ACCEPT`, `OVERRIDE_TO_0`, `OVERRIDE_TO_1`, or `UNRESOLVED`
- Human decision: **[enter in CSV]**
- Human correction reason: **[required for overrides; explain unresolved cases]**

---
"""


def main() -> None:
    queue = load_jsonl(SOURCE_QUEUE)
    gold_rows = {r["bug_id"]: r for r in load_jsonl(GOLD)}
    if len(queue) != 36 or len({r["review_item_id"] for r in queue}) != 36:
        raise ValueError("Source queue must contain exactly 36 unique items")
    records = []
    for item in queue:
        if item.get("condition_visible") is not False or item.get("restricted_task_id_visible") is not False:
            raise ValueError(f"{item['review_item_id']}: source queue is not blinded")
        records.append(build_record(item, gold_rows[item["bug_id"]]))

    OUT.mkdir(parents=True, exist_ok=True)
    baseline = OUT / "cspc_review_baseline_immutable.jsonl"
    baseline.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")

    fieldnames = [
        "review_item_id", "anonymous_response_id", "bug_id", "gold_path_id",
        "required_setup_json", "required_triggers_json", "required_action_order_json",
        "expected_observation_json", "selected_generated_scenario_json",
        "generated_scenarios_considered_when_no_selection_json",
        "ai_cspc_judgment", "exact_ai_supporting_evidence_json",
        "missing_or_contradictory_requirements_json", "immutable_ai_and_evidence_sha256",
        "human_decision", "human_correction_reason", "reviewer_id", "review_timestamp_utc",
    ]
    workbook = OUT / "cspc_human_verification_workbook.csv"
    with workbook.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "review_item_id": r["review_item_id"],
                "anonymous_response_id": r["anonymous_response_id"],
                "bug_id": r["bug_id"],
                "gold_path_id": r["gold_path_id"],
                "required_setup_json": compact(r["required_setup"]),
                "required_triggers_json": compact(r["required_triggers"]),
                "required_action_order_json": compact(r["required_action_order"]),
                "expected_observation_json": compact(r["expected_observation"]),
                "selected_generated_scenario_json": compact(r["selected_generated_scenario"]),
                "generated_scenarios_considered_when_no_selection_json": compact(r["generated_scenarios_considered_when_no_selection"]),
                "ai_cspc_judgment": r["ai_cspc_judgment"],
                "exact_ai_supporting_evidence_json": compact(r["exact_ai_supporting_evidence"]),
                "missing_or_contradictory_requirements_json": compact(r["missing_or_contradictory_requirements"]),
                "immutable_ai_and_evidence_sha256": r["immutable_ai_and_evidence_sha256"],
                "human_decision": "",
                "human_correction_reason": "",
                "reviewer_id": "",
                "review_timestamp_utc": "",
            })

    md = OUT / "cspc_human_verification_review.md"
    header = """# Stage 1 Human CSPC Verification Review

Review all 36 Bug–Path judgments. The AI judgment and cited evidence are immutable.
Enter decisions only in `cspc_human_verification_workbook.csv`.

Allowed decisions: `ACCEPT`, `OVERRIDE_TO_0`, `OVERRIDE_TO_1`, `UNRESOLVED`.
Every override requires a correction reason. `UNRESOLVED` prevents finalization.
No condition arm, BM25 score, or retrieval-rank information is included.

"""
    md.write_text(header + "\n".join(markdown_record(i, r) for i, r in enumerate(records, 1)), encoding="utf-8")

    readme = OUT / "README.md"
    readme.write_text("""# Stage 1 CSPC human verification workflow

1. Read `cspc_human_verification_review.md` for the compact evidence display.
2. Edit only these columns in `cspc_human_verification_workbook.csv`:
   - `human_decision`: `ACCEPT`, `OVERRIDE_TO_0`, `OVERRIDE_TO_1`, or `UNRESOLVED`
   - `human_correction_reason`: mandatory for either override; document unresolved cases
   - `reviewer_id`
   - `review_timestamp_utc`
3. Do not change any other column. The validator compares all immutable fields with the baseline.
4. From the research project root, run:

   `python3 -B outputs/phase3d_experiment_execution/validate_stage1_cspc_human_review.py`

The validator exits nonzero until all 36 decisions are explicit, every override has a reason,
there are no unresolved items, AI data remains unchanged, and no condition/retrieval metadata is exposed.
Passing validation does not lock scores or unblind experimental arms.
""", encoding="utf-8")

    manifest = {
        "status": "PREPARED_AWAITING_HUMAN_DECISIONS",
        "record_count": len(records),
        "source_queue": str(SOURCE_QUEUE),
        "source_queue_sha256": hashlib.sha256(SOURCE_QUEUE.read_bytes()).hexdigest(),
        "frozen_gold": str(GOLD),
        "frozen_gold_sha256": hashlib.sha256(GOLD.read_bytes()).hexdigest(),
        "approved_rubric": str(RUBRIC),
        "approved_rubric_sha256": hashlib.sha256(RUBRIC.read_bytes()).hexdigest(),
        "outputs": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (baseline, workbook, md, readme)
        },
        "condition_mapping_exposed": False,
        "bm25_or_retrieval_metadata_exposed": False,
        "ai_assessments_modified": False,
        "human_decisions_prefilled": False,
        "score_locked": False,
        "unblinded": False,
    }
    (OUT / "review_package_manifest.json").write_text(pretty(manifest) + "\n", encoding="utf-8")
    print(pretty(manifest))


if __name__ == "__main__":
    main()
