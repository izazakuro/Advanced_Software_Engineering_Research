#!/usr/bin/env python3
"""Prepare the 43-record blinded Stage 1 secondary-QC human review package."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
RESPONSES = EVAL / "responses"
PROVISIONAL = EVAL / "public" / "provisional_ai_metric_results.jsonl"
CLASSIFICATION = EVAL / "stage1_missing_evidence_classification.csv"
GOLD = HERE.parent / "gold_v1_frozen" / "gold_v1.jsonl"
QC_POLICY = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_human_verification_and_qc_policy.md"
SEED = 29012026
RANDOM_N = 23

QUEUE = EVAL / "stage1_secondary_qc_review_queue_immutable.jsonl"
REVIEW = EVAL / "stage1_secondary_qc_human_review.md"
WORKBOOK = EVAL / "stage1_secondary_qc_human_review_workbook.csv"
SUMMARY = EVAL / "stage1_secondary_qc_review_summary.md"
MANIFEST = EVAL / "stage1_secondary_qc_review_manifest.json"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canon_sha(value: object) -> str:
    return hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def effective_assessment(response_id: str) -> tuple[dict, Path]:
    d = RESPONSES / response_id
    retry = d / "authorized_retry_1" / "ai_assessment.json"
    path = retry if retry.exists() else d / "ai_assessment.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def read_classification() -> dict[str, dict]:
    with CLASSIFICATION.open(encoding="utf-8", newline="") as fh:
        return {r["anonymous_response_id"]: r for r in csv.DictReader(fh)}


def metric_polarity(metric: str, score: object) -> str:
    if metric == "TCC":
        value = score["proportion"]
        return "FULL" if value == 1 else ("ZERO" if value == 0 else "PARTIAL")
    if metric == "SCENARIO_ADEQUACY":
        return "POSITIVE" if score["value"] == 1 else "NEGATIVE"
    assert metric == "STRICT_ORACLE"
    return "ALL_CORRECT" if all(x["binary_score"] == 1 for x in score) else "HAS_NONCORRECT"


def gold_path_projection(gold: dict, path: dict) -> dict:
    conditions = {x["condition_id"]: x for x in gold["trigger_conditions"]}
    path_id = path["path_id"]
    oracles = [x for x in gold.get("test_oracles", []) if x.get("trigger_path_id") == path_id]
    return {
        "path_id": path_id,
        "completeness": path.get("completeness"),
        "cspc_eligibility": path.get("cspc_eligibility"),
        "material_setup_conditions": [conditions[x] for x in path.get("setup_condition_ids", [])],
        "required_trigger_conditions": [conditions[x] for x in path.get("required_condition_ids", [])],
        "required_action_order": [conditions[x] for x in path.get("ordered_condition_ids", [])],
        "verification_actions": [conditions[x] for x in path.get("verification_action_ids", []) if x in conditions],
        "expected_observable_behavior": gold["expected_behavior"],
        "applicable_oracles": oracles,
        "runtime_reproduction_verified": path.get("runtime_reproduction_verified", False),
        "known_limitations": gold.get("known_limitations", []),
    }


def selected_details(metric: str, scores: dict, assessment: dict, gold: dict) -> tuple[list[str], list[str], object, object]:
    if metric == "TCC":
        score = scores["tcc"]
        path_ids = [score["selected_gold_path_id"]]
        scenario_ids = [score["selected_scenario_id"]]
        path_assessment = next(x for x in assessment["tcc_path_assessments"] if x["gold_path_id"] == path_ids[0])
        selected = next(x for x in path_assessment["scenario_comparisons"] if x["scenario_id"] == scenario_ids[0])
        alternatives = assessment["tcc_path_assessments"]
        return path_ids, scenario_ids, score, {"selected_comparison": selected, "all_path_scenario_comparisons": alternatives}
    if metric == "SCENARIO_ADEQUACY":
        a = assessment["scenario_adequacy_assessment"]
        return [a["gold_path_id"]], [a["selected_scenario_id"]], scores["scenario_adequacy"], a
    oracle_assessments = assessment["strict_oracle_assessments"]
    oracle_by_id = {x["oracle_id"]: x for x in gold.get("test_oracles", [])}
    path_ids = sorted({oracle_by_id[x["oracle_id"]]["trigger_path_id"] for x in oracle_assessments})
    scenario_ids = sorted({x["selected_scenario_id"] for x in oracle_assessments})
    return path_ids, scenario_ids, scores["strict_oracle"], oracle_assessments


def build_population() -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    classifications = read_classification()
    gold_by_bug = {x["bug_id"]: x for x in load_jsonl(GOLD)}
    results = [x for x in load_jsonl(PROVISIONAL) if x["evaluation_status"] == "AI_VALID_PROVISIONAL"]
    if len(results) != 53:
        raise ValueError(f"Expected 53 valid responses, got {len(results)}")
    population = []
    for result in results:
        rid = result["anonymous_response_id"]
        assessment, assessment_path = effective_assessment(rid)
        package = json.loads((RESPONSES / rid / "anonymous_evaluation_package.json").read_text(encoding="utf-8"))
        scenarios = package["generated_scenarios_untrusted_data"]
        scores = result["provisional_scores"]
        gold = gold_by_bug[result["bug_id"]]
        metric_specs = [
            ("TCC", scores["tcc"]),
            ("SCENARIO_ADEQUACY", scores["scenario_adequacy"]),
        ]
        if scores["strict_oracle"]:
            metric_specs.append(("STRICT_ORACLE", scores["strict_oracle"]))
        for metric, _ in metric_specs:
            path_ids, scenario_ids, score, metric_assessment = selected_details(metric, scores, assessment, gold)
            selected_scenarios = [x for x in scenarios if x["scenario_id"] in scenario_ids]
            if len(selected_scenarios) != len(scenario_ids):
                raise ValueError(f"{rid}/{metric}: selected scenario missing")
            selected_paths = [x for x in gold["trigger_paths"] if x["path_id"] in path_ids]
            if len(selected_paths) != len(path_ids):
                raise ValueError(f"{rid}/{metric}: selected Gold path missing")
            cls = classifications[rid]
            implicated_metrics = set(filter(None, cls["affected_secondary_metrics"].split(";")))
            directly_implicated = metric in implicated_metrics
            relevant_gold = {
                "bug_id": gold["bug_id"],
                "affected_component": gold["affected_component"],
                "observed_behavior": gold["observed_behavior"],
                "expected_behavior": gold["expected_behavior"],
                "selected_paths": [gold_path_projection(gold, x) for x in selected_paths],
                "alternative_paths_not_merged": [gold_path_projection(gold, x) for x in gold["trigger_paths"] if x["path_id"] not in path_ids],
                "oracle_eligibility_summary": gold.get("evaluation_targets_v2", {}),
                "known_limitations": gold.get("known_limitations", []),
            }
            source = {
                "review_id": f"SECONDARY::{rid}::{metric}",
                "anonymous_response_id": rid,
                "bug_id": result["bug_id"],
                "metric": metric,
                "metric_polarity": metric_polarity(metric, score),
                "gold_path_ids": path_ids,
                "ai_selected_scenario_ids": scenario_ids,
                "original_ai_score_or_label": score,
                "original_metric_assessment_and_evidence": metric_assessment,
                "uncertainty_flags": assessment["uncertainty_flags"],
                "evidence_notes": assessment["evidence_notes"],
                "risk_classification": cls["classification"],
                "risk_pattern_signature": cls["pattern_signature"],
                "identified_metric_evidence_deficiency": (
                    cls["pattern_signature"] if directly_implicated else "NO_DIRECT_METRIC_LOCAL_DEFICIENCY_IDENTIFIED"
                ),
                "directly_implicated": directly_implicated,
                "gold_reference": relevant_gold,
                "selected_generated_scenarios_full": selected_scenarios,
                "selection_alternatives_appendix": (
                    scenarios if metric in {"TCC", "SCENARIO_ADEQUACY"} else []
                ),
                "effective_assessment_sha256": sha(assessment_path),
                "generated_scenarios_source_sha256": canon_sha(scenarios),
            }
            population.append(source)
    return population, classifications, gold_by_bug


def allocate_random(strata: dict[str, list[dict]], total: int) -> dict[str, int]:
    n = sum(len(x) for x in strata.values())
    quotas = {k: len(v) * total / n for k, v in strata.items()}
    allocation = {k: math.floor(q) for k, q in quotas.items()}
    remaining = total - sum(allocation.values())
    order = sorted(strata, key=lambda k: (-(quotas[k] - allocation[k]), k))
    for k in order[:remaining]:
        allocation[k] += 1
    if any(allocation[k] > len(strata[k]) for k in strata):
        raise ValueError("random allocation exceeds stratum")
    return allocation


def build_queue() -> tuple[list[dict], dict]:
    population, _, _ = build_population()
    if len(population) != 133:
        raise ValueError(f"Expected 133 response×metric records, got {len(population)}")
    targeted = [x for x in population if x["directly_implicated"]]
    if Counter(x["metric"] for x in targeted) != {"SCENARIO_ADEQUACY": 15, "STRICT_ORACLE": 5}:
        raise ValueError("Targeted metric counts do not reconcile")
    targeted_ids = {x["review_id"] for x in targeted}
    unaffected = [x for x in population if x["review_id"] not in targeted_ids]
    if len(unaffected) != 113:
        raise ValueError(f"Expected 113 unaffected records, got {len(unaffected)}")
    strata = defaultdict(list)
    for x in unaffected:
        strata[f"{x['metric']}::{x['metric_polarity']}"] .append(x)
    allocation = allocate_random(strata, RANDOM_N)
    random_rows = []
    for stratum in sorted(strata):
        candidates = sorted(strata[stratum], key=lambda x: (x["anonymous_response_id"], x["review_id"]))
        derived = int(hashlib.sha256(f"{SEED}|{stratum}".encode()).hexdigest(), 16)
        random.Random(derived).shuffle(candidates)
        random_rows.extend(candidates[:allocation[stratum]])
    if len(random_rows) != RANDOM_N:
        raise ValueError("Random sample is not 23")
    if targeted_ids & {x["review_id"] for x in random_rows}:
        raise ValueError("Targeted/random overlap")
    queue = []
    for category, records in (("TARGETED", sorted(targeted, key=lambda x: x["review_id"])), ("RANDOM", sorted(random_rows, key=lambda x: x["review_id"]))):
        for source in records:
            immutable = {**source, "qc_category": category}
            queue.append({
                **immutable,
                "immutable_review_record_sha256": canon_sha(immutable),
                "human_decision": "",
                "human_corrected_score_or_label_json": "",
                "human_correction_reason": "",
                "human_evidence_reference": "",
                "reviewer_id": "",
                "review_timestamp_utc": "",
            })
    sampling = {
        "status": "PROPOSED_REQUIRES_RESEARCHER_APPROVAL",
        "sampling_unit": "response_x_metric_record",
        "frame_size_after_targeted_exclusion": len(unaffected),
        "sample_size": len(random_rows),
        "seed": SEED,
        "random_generator": "Python random.Random (MT19937), independently seeded per stratum with integer SHA-256(seed|stratum)",
        "pre_sampling_sort": "anonymous_response_id, then review_id",
        "strata": {k: {"population": len(strata[k]), "selected": allocation[k]} for k in sorted(strata)},
        "allocation": "proportional largest remainder to an exact total of 23",
    }
    return queue, sampling


QUESTIONS = {
    "TCC": [
        "Are all credited triggers supported by the selected scenario?",
        "Is the denominator correct?",
        "Was the correct scenario/path selected?",
        "Were OR alternatives handled coherently without merging?",
    ],
    "SCENARIO_ADEQUACY": [
        "Are all material setup requirements present?",
        "Is the action sequence coherent?",
        "Is the scenario practically actionable?",
        "Does it contain a concrete observable check?",
    ],
    "STRICT_ORACLE": [
        "Does the expected result match the approved Gold?",
        "Does it distinguish faulty from correct behavior?",
        "Is the Oracle complete enough to score under frozen eligibility?",
        "Are any expected outcomes contradictory or unsupported?",
    ],
}


def render_review(queue: list[dict], sampling: dict) -> str:
    lines = [
        "# Stage 1 Secondary QC Human Review", "",
        "Status: **PREPARED; HUMAN DECISIONS BLANK; RANDOM POLICY PROPOSED**", "",
        "Review 43 anonymous response×metric records: 20 TARGETED and 23 RANDOM. Do not edit immutable content; enter decisions only in the CSV workbook.", "",
        f"Random sampling: seed `{sampling['seed']}`, condition-blind metric×polarity strata, exact frame 113, exact sample 23. Researcher approval is still required.", "",
    ]
    for index, r in enumerate(queue, 1):
        lines += [
            f"## {index}. `{r['review_id']}`", "",
            f"- Anonymous response ID: `{r['anonymous_response_id']}`",
            f"- Metric: `{r['metric']}`",
            f"- QC category: **{r['qc_category']}**",
            f"- Gold Path ID(s): `{', '.join(r['gold_path_ids'])}`",
            f"- AI-selected Scenario ID(s): `{', '.join(r['ai_selected_scenario_ids'])}`",
            f"- Risk classification: `{r['risk_classification']}`",
            f"- Evidence-deficiency pattern: `{r['identified_metric_evidence_deficiency']}`",
            f"- Immutable record hash: `{r['immutable_review_record_sha256']}`", "",
            "### Complete applicable Gold Reference", "", "```json",
            json.dumps(r["gold_reference"], ensure_ascii=False, indent=2, sort_keys=True), "```", "",
            "### Full selected generated scenario(s)", "", "```json",
            json.dumps(r["selected_generated_scenarios_full"], ensure_ascii=False, indent=2, sort_keys=True), "```", "",
            "### Original AI evaluation", "", "```json",
            json.dumps({
                "score_or_label": r["original_ai_score_or_label"],
                "assessment_and_supporting_evidence": r["original_metric_assessment_and_evidence"],
                "uncertainty_flags": r["uncertainty_flags"],
                "evidence_notes": r["evidence_notes"],
            }, ensure_ascii=False, indent=2, sort_keys=True), "```", "",
            "### Metric-specific review questions", "",
        ]
        lines += [f"- [ ] {q}" for q in QUESTIONS[r["metric"]]]
        if r["selection_alternatives_appendix"]:
            lines += ["", "### Selection alternatives appendix", "", "The complete generated alternatives are retained to audit best-of-three/path selection; they must not be merged.", "", "```json", json.dumps(r["selection_alternatives_appendix"], ensure_ascii=False, indent=2, sort_keys=True), "```"]
        lines += [
            "", "### Researcher decision — complete in CSV", "",
            "- Decision (`ACCEPT`, `CORRECT`, `UNRESOLVED`): **[blank]**",
            "- Corrected score or label: **[blank]**",
            "- Correction reason: **[blank]**",
            "- Evidence reference: **[blank]**",
            "- Reviewer identifier: **[blank]**",
            "- Review timestamp: **[blank]**", "", "---", "",
        ]
    return "\n".join(lines)


def write_workbook(queue: list[dict]) -> None:
    fields = [
        "review_id", "anonymous_response_id", "metric", "qc_category",
        "original_ai_score_json", "original_selected_scenario_ids_json", "original_gold_path_ids_json",
        "risk_classification", "risk_pattern_signature", "identified_metric_evidence_deficiency",
        "original_ai_assessment_and_evidence_json", "immutable_review_record_sha256",
        "human_decision", "human_corrected_score_or_label_json", "human_correction_reason",
        "human_evidence_reference", "reviewer_id", "review_timestamp_utc",
    ]
    with WORKBOOK.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in queue:
            writer.writerow({
                "review_id": r["review_id"],
                "anonymous_response_id": r["anonymous_response_id"],
                "metric": r["metric"],
                "qc_category": r["qc_category"],
                "original_ai_score_json": canon(r["original_ai_score_or_label"]),
                "original_selected_scenario_ids_json": canon(r["ai_selected_scenario_ids"]),
                "original_gold_path_ids_json": canon(r["gold_path_ids"]),
                "risk_classification": r["risk_classification"],
                "risk_pattern_signature": r["risk_pattern_signature"],
                "identified_metric_evidence_deficiency": r["identified_metric_evidence_deficiency"],
                "original_ai_assessment_and_evidence_json": canon(r["original_metric_assessment_and_evidence"]),
                "immutable_review_record_sha256": r["immutable_review_record_sha256"],
                "human_decision": "",
                "human_corrected_score_or_label_json": "",
                "human_correction_reason": "",
                "human_evidence_reference": "",
                "reviewer_id": "",
                "review_timestamp_utc": "",
            })


def main() -> None:
    queue, sampling = build_queue()
    QUEUE.write_text("".join(canon(x) + "\n" for x in queue), encoding="utf-8")
    REVIEW.write_text(render_review(queue, sampling), encoding="utf-8")
    write_workbook(queue)

    category_counts = Counter(x["qc_category"] for x in queue)
    metric_counts = Counter((x["qc_category"], x["metric"]) for x in queue)
    pattern_counts = Counter(x["risk_pattern_signature"] for x in queue if x["qc_category"] == "TARGETED")
    summary = f"""# Stage 1 Secondary QC Review Summary

Status: prepared for human review; no decisions populated; no scores finalized.

## Population and workload

- Valid anonymous responses: 53
- Eligible response×metric population: 133 (53 TCC, 53 SA, 27 Strict Oracle)
- Directly implicated TARGETED records: 20 (15 SA, 5 Strict Oracle, 0 TCC)
- Remaining unaffected sampling frame: 113
- RANDOM records: 23 (11 TCC, 8 SA, 4 Strict Oracle)
- Total review workload: 43 unique records

## Evidence-risk distinctions

- **Missing supporting evidence:** structured non-`SUPPORTED` evidence in a score-bearing secondary assessment; these define the 20 TARGETED records.
- **Unavailable historical fixtures:** runtime/archive availability limitations described in evidence notes; these do not automatically invalidate textual metric support.
- **Unsupported assumptions:** generated claims not supported by approved Gold; review whether they materially change the metric rather than treating every assumption as missing trigger evidence.
- **Incomplete Gold Oracle:** frozen Oracle insufficiency or ineligibility; retain N/A/exclusion semantics and never turn it into model error zero.
- **Missing optional metadata:** none was identified as a structural cause of the universal flag; all 53 assessments passed schema/semantic validation and had nonblank positive citations.

## Targeted patterns

```json
{json.dumps(pattern_counts, ensure_ascii=False, indent=2, sort_keys=True)}
```

## Random sampling

The random frame excludes all 20 targeted review IDs. Sampling is condition-blind and uses metric×AI-polarity strata. Stable IDs are sorted first; each stratum is shuffled with Python `random.Random` seeded by integer SHA-256(`29012026|stratum`); proportional largest-remainder allocation produces exactly 23 records.

```json
{json.dumps(sampling, ensure_ascii=False, indent=2, sort_keys=True)}
```

This policy is **PROPOSED**, not approved. The researcher must approve the fraction/sample size, seed, strata, and algorithm before the RANDOM queue is treated as finalized.

## Proposed escalation

- Major error denominator: completed determinate reviewed response×metric records, reported separately for TARGETED and RANDOM.
- Minor errors never silently change scores and are reported separately.
- In the 23-record random audit, two major errors exceed 5% and trigger metric-wide expansion.
- A repeated systematic major error at least twice also triggers metric-wide expansion.
- Expanded major-error rate above 10%, or a Gold/validator defect, keeps the affected metric unlocked.

## Remaining approvals

1. Approve/revise the proposed 23-record random policy and realized sample.
2. Complete all 43 human decisions; corrections require corrected value, reason, and evidence.
3. Resolve every UNRESOLVED record.
4. Apply escalation rules and complete any expanded review.
5. Revalidate immutable fields before any secondary score finalization.

No A/B/C labels, BM25 values, retrieval ranks, or restricted mapping data were used.
"""
    SUMMARY.write_text(summary, encoding="utf-8")

    manifest = {
        "artifact_version": "stage1-secondary-qc-human-review-v1",
        "status": "PASS_PREPARED_AWAITING_HUMAN_REVIEW_AND_RANDOM_POLICY_APPROVAL",
        "counts": {
            "valid_responses": 53,
            "eligible_response_metric_records": 133,
            "targeted": category_counts["TARGETED"],
            "random": category_counts["RANDOM"],
            "total_review": len(queue),
            "targeted_by_metric": {m: metric_counts[("TARGETED", m)] for m in ("TCC", "SCENARIO_ADEQUACY", "STRICT_ORACLE")},
            "random_by_metric": {m: metric_counts[("RANDOM", m)] for m in ("TCC", "SCENARIO_ADEQUACY", "STRICT_ORACLE")},
        },
        "sampling": sampling,
        "human_decisions_populated": False,
        "score_lock_performed": False,
        "unblinding_performed": False,
        "condition_mapping_accessed": False,
        "input_hashes": {
            "provisional_results": sha(PROVISIONAL),
            "missing_evidence_classification": sha(CLASSIFICATION),
            "frozen_gold": sha(GOLD),
            "qc_policy": sha(QC_POLICY),
        },
        "output_hashes": {
            "immutable_queue": sha(QUEUE),
            "human_review_markdown": sha(REVIEW),
            "workbook": sha(WORKBOOK),
            "summary": sha(SUMMARY),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    main()
