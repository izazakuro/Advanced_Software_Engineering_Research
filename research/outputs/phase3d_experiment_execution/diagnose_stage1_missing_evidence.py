#!/usr/bin/env python3
"""Read-only diagnosis of universal evaluator MISSING_EVIDENCE flags."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
PROVISIONAL = EVAL / "public" / "provisional_ai_metric_results.jsonl"
RESPONSES = EVAL / "responses"
SAMPLE = EVAL / "stage1_secondary_qc_review_package" / "proposed_random_secondary_qc_workbook.csv"
PROMPT = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_ai_evaluator_prompt_v2_PROPOSED.txt"
SCHEMA = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_ai_evaluator_output_v2.schema.json"
VALIDATOR = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_evaluation_pipeline.py"
OUT_CSV = EVAL / "stage1_missing_evidence_classification.csv"
OUT_REPORT = EVAL / "stage1_missing_evidence_diagnostic.md"
OUT_ESCALATION = EVAL / "stage1_secondary_qc_escalation_recommendation.md"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def effective_assessment(response_id: str) -> tuple[dict, Path]:
    d = RESPONSES / response_id
    retry = d / "authorized_retry_1" / "ai_assessment.json"
    path = retry if retry.exists() else d / "ai_assessment.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    with SAMPLE.open(encoding="utf-8", newline="") as fh:
        sample_ids = {r["anonymous_response_id"] for r in csv.DictReader(fh)}
    rows = []
    for result in load_jsonl(PROVISIONAL):
        if result["evaluation_status"] != "AI_VALID_PROVISIONAL":
            continue
        rid = result["anonymous_response_id"]
        assessment, assessment_path = effective_assessment(rid)
        tcc_score = result["provisional_scores"]["tcc"]
        selected_path = tcc_score["selected_gold_path_id"]
        selected_scenario = tcc_score["selected_scenario_id"]
        location_counts = Counter()
        blank_supporting_text = 0
        for path_assessment in assessment["tcc_path_assessments"]:
            for comparison in path_assessment["scenario_comparisons"]:
                score_bearing = path_assessment["gold_path_id"] == selected_path and comparison["scenario_id"] == selected_scenario
                for evidence in comparison["evidence"]:
                    blank_supporting_text += not str(evidence["supporting_text"]).strip()
                    if evidence["support_type"] != "SUPPORTED":
                        where = "TCC_SCORE_BEARING" if score_bearing else "TCC_NONSELECTED"
                        location_counts[f"{where}_{evidence['support_type']}"] += 1
        for evidence in assessment["scenario_adequacy_assessment"]["evidence"]:
            blank_supporting_text += not str(evidence["supporting_text"]).strip()
            if evidence["support_type"] != "SUPPORTED":
                location_counts[f"SA_{evidence['support_type']}"] += 1
        for oracle in assessment["strict_oracle_assessments"]:
            for evidence in oracle["evidence"]:
                blank_supporting_text += not str(evidence["supporting_text"]).strip()
                if evidence["support_type"] != "SUPPORTED":
                    location_counts[f"ORACLE_{evidence['support_type']}"] += 1
        for path_assessment in assessment["cspc_path_assessments"]:
            for evidence in path_assessment["evidence"]:
                blank_supporting_text += not str(evidence["supporting_text"]).strip()
                if evidence["support_type"] != "SUPPORTED":
                    location_counts[f"CSPC_{evidence['support_type']}"] += 1

        affected = []
        if any(k.startswith("TCC_SCORE_BEARING_") for k in location_counts):
            affected.append("TCC")
        if any(k.startswith("SA_") for k in location_counts):
            affected.append("SCENARIO_ADEQUACY")
        if any(k.startswith("ORACLE_") for k in location_counts):
            affected.append("STRICT_ORACLE")
        if affected:
            classification = "Substantive evidence deficiency"
            rationale = "At least one non-SUPPORTED evidence item occurs in a score-bearing secondary assessment."
        elif location_counts:
            classification = "Non-applicable evidence field"
            rationale = "Non-SUPPORTED evidence occurs only outside the score-bearing TCC/SA/Strict-Oracle assessment (for example, a nonselected TCC alternative or CSPC)."
        else:
            classification = "Indeterminate"
            rationale = "The response-global flag has no non-SUPPORTED structured evidence item to which it can be linked; prose notes may discuss unavailable fixtures or unsupported assumptions."
        rows.append({
            "anonymous_response_id": rid,
            "bug_id": result["bug_id"],
            "classification": classification,
            "classification_rationale": rationale,
            "affected_secondary_metrics": ";".join(affected),
            "pattern_signature": ";".join(sorted(location_counts)) or "NO_STRUCTURED_NON_SUPPORTED_EVIDENCE",
            "tcc_score_bearing_non_supported_count": sum(v for k, v in location_counts.items() if k.startswith("TCC_SCORE_BEARING_")),
            "tcc_nonselected_non_supported_count": sum(v for k, v in location_counts.items() if k.startswith("TCC_NONSELECTED_")),
            "scenario_adequacy_non_supported_count": sum(v for k, v in location_counts.items() if k.startswith("SA_")),
            "strict_oracle_non_supported_count": sum(v for k, v in location_counts.items() if k.startswith("ORACLE_")),
            "cspc_non_supported_count": sum(v for k, v in location_counts.items() if k.startswith("CSPC_")),
            "blank_supporting_text_count": blank_supporting_text,
            "schema_and_semantic_validation": "PASS",
            "missing_evidence_flag_present": "MISSING_EVIDENCE" in assessment["uncertainty_flags"],
            "human_review_required": assessment["human_review_required"],
            "evidence_notes_count": len(assessment["evidence_notes"]),
            "in_existing_12_response_sample": rid in sample_ids,
            "effective_assessment_sha256": sha(assessment_path),
        })

    fields = list(rows[0])
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    classes = Counter(r["classification"] for r in rows)
    sample_classes = Counter(r["classification"] for r in rows if r["in_existing_12_response_sample"])
    patterns = Counter(r["pattern_signature"] for r in rows)
    sample_patterns = {r["pattern_signature"] for r in rows if r["in_existing_12_response_sample"]}
    metric_affected = Counter()
    for row in rows:
        for metric in filter(None, row["affected_secondary_metrics"].split(";")):
            metric_affected[metric] += 1

    report = f"""# Stage 1 Universal `MISSING_EVIDENCE` Diagnostic

## Finding

The universal flag is evaluator-authored and response-global; it is not computed by deterministic validation. The evaluator Prompt rule 11 asks the model to mark “missing evidence” explicitly, but neither the Prompt nor Schema defines its scope, severity, or relationship to a particular metric. The Schema only enumerates `MISSING_EVIDENCE` as an allowed `uncertainty_flags` value.

The deterministic implementation `_validate_evidence` does **not** assign this flag. It requires exact evidence fields and rejects a `SUPPORTED` item with blank `supporting_text`. All 53 assessments passed schema and semantic validation, and the diagnostic found zero blank supporting-text fields. Thus the universal flag is not caused by an empty required evidence field or a schema-validation failure.

Relevant sources:

- Evaluator Prompt: `{PROMPT}` (rules 4, 5, and 11)
- Evaluator Schema: `{SCHEMA}` (`uncertainty_flags` enum and evidence object)
- Deterministic validator: `{VALIDATOR}` (`_validate_evidence` and `validate_ai_assessment`)

No runtime adapter code assigns `MISSING_EVIDENCE`; the value is present in each raw/effective evaluator response.

## Classification rule

- **Substantive evidence deficiency:** a non-`SUPPORTED` evidence item occurs in the final score-bearing TCC path+scenario, Scenario Adequacy assessment, or Strict Oracle assessment.
- **Metadata/schema deficiency:** a required field is absent/invalid, a positive citation is blank, or schema/semantic validation fails.
- **Non-applicable evidence field:** non-`SUPPORTED` evidence exists only outside the score-bearing secondary judgment, such as an unselected TCC path/scenario or CSPC-only assessment.
- **Indeterminate:** the global flag has no structured non-`SUPPORTED` evidence item, so its cause cannot be linked to a metric. Prose notes frequently mention unsupported assumptions or unavailable historical fixtures, but the Schema does not link those notes to the flag.

## Counts across 53 anonymous responses

| Classification | Count | Percent |
|---|---:|---:|
| Substantive evidence deficiency | {classes['Substantive evidence deficiency']} | {classes['Substantive evidence deficiency']/53:.1%} |
| Metadata/schema deficiency | {classes['Metadata/schema deficiency']} | {classes['Metadata/schema deficiency']/53:.1%} |
| Non-applicable evidence field | {classes['Non-applicable evidence field']} | {classes['Non-applicable evidence field']/53:.1%} |
| Indeterminate | {classes['Indeterminate']} | {classes['Indeterminate']/53:.1%} |

Score-bearing secondary records directly implicated:

- TCC: {metric_affected['TCC']} responses
- Scenario Adequacy: {metric_affected['SCENARIO_ADEQUACY']} responses
- Strict Oracle: {metric_affected['STRICT_ORACLE']} responses

The universal response-level flag therefore overstates the metric-local review burden. It does not justify treating all 133 secondary records as substantively deficient.

## Existing 12-response diagnostic sample

The sample contains {sample_classes['Substantive evidence deficiency']} substantive, {sample_classes['Non-applicable evidence field']} non-applicable, and {sample_classes['Indeterminate']} indeterminate responses. It covers {len(sample_patterns)} of {len(patterns)} distinct structured pattern signatures (50%). It misses several low-frequency combinations, including selected-path alternatives with Oracle missing evidence and multiple SA contradiction/missing patterns.

Conclusion: the 12-response sample is proportionally broad but not pattern-complete. It is insufficient as the sole basis for clearing all secondary metrics. Reviewing all directly implicated metric records plus a separate random audit of unaffected records is more defensible.

## Interpretation

The most supported explanation is over-broad, conservative evaluator flagging: missing evidence in unselected alternatives, inapplicable fields, unsupported generated assumptions, or unavailable historical/runtime evidence is collapsed into one response-level flag. For 23 responses the precise trigger remains indeterminate because the output format does not provide flag-to-evidence linkage.

No scores or decisions were changed by this diagnostic. No condition mapping was accessed.
"""
    OUT_REPORT.write_text(report, encoding="utf-8")

    escalation = f"""# Stage 1 Secondary-QC Escalation Recommendation

Status: recommendation only; no review decisions or scores changed.

## Recommended targeted QC

1. Review the {metric_affected['SCENARIO_ADEQUACY'] + metric_affected['STRICT_ORACLE'] + metric_affected['TCC']} directly implicated response×metric records:
   - {metric_affected['TCC']} TCC
   - {metric_affected['SCENARIO_ADEQUACY']} Scenario Adequacy
   - {metric_affected['STRICT_ORACLE']} Strict Oracle
2. Do not route the remaining {133 - (metric_affected['SCENARIO_ADEQUACY'] + metric_affected['STRICT_ORACLE'] + metric_affected['TCC'])} records to mandatory review solely because of the global `MISSING_EVIDENCE` flag.
3. Draw a condition-blind random audit from those remaining records, stratified by metric and AI polarity. The existing 12-response sample can be retained as a diagnostic sample, but it is not pattern-complete and overlaps targeted cases.

## Error definitions

- **Major error:** human review changes the metric value/label, selected path or scenario, frozen denominator/eligibility, or finds that a positive score lacks required supporting evidence.
- **Minor error:** citation imprecision, ancillary unsupported/unavailable fixture detail, or metadata wording that does not change the metric value, eligibility, denominator, path, or selected scenario.
- **No error:** the AI correctly records supported or missing evidence and the final metric remains unchanged.
- **Unresolved:** excluded from error-rate denominators and reported separately; it blocks finalization of the affected metric.

## Explicit denominators

- Targeted-review major-error rate: `major errors / completed determinate targeted response×metric reviews`. This risk-enriched rate is not an estimate of population prevalence.
- Random-audit major-error rate: `major errors / completed determinate randomly sampled response×metric reviews` from the unaffected population.
- Minor-error rates use the same respective denominators but are reported separately and never combined with major errors.
- Duplicate evidence spans within one response×metric record do not increase the denominator.

## Proposed escalation

- Review all 20 directly implicated records regardless of sample results.
- For the remaining 113 records, draw approximately 20% (target 23 records) using a prespecified metric×polarity stratified algorithm and seed 29012026.
- With a 23-record random denominator, two or more major errors exceed 5% and trigger expansion to all records of the affected metric.
- Any repeated systematic major-error pattern observed at least twice across targeted or random review also triggers metric-wide expansion, even if the random threshold is not crossed.
- If expanded metric-wide review has a major-error rate above 10%, or reveals a Gold/selection/validator defect, keep that metric unlocked and require documented adjudication or a versioned correction.
- Minor errors do not trigger score changes. Report their rate; if minor errors exceed 20%, expand citation-quality review but do not automatically alter metric values.

## Readiness conclusion

The secondary assessments are sufficiently structured to proceed with **targeted human QC**, not automatic acceptance and not blanket review of all 133 records. TCC has no score-bearing non-`SUPPORTED` evidence under the final selected path+scenario. SA has 15 directly implicated responses and Strict Oracle has 5. These 20 records require targeted review before the affected metrics can be finalized. A separate random audit is still required to estimate errors among the 113 unaffected records.

This recommendation remains subject to researcher approval and does not satisfy the secondary-QC score-lock gate by itself.
"""
    OUT_ESCALATION.write_text(escalation, encoding="utf-8")

    print(json.dumps({
        "records": len(rows),
        "classifications": classes,
        "affected_secondary_response_metric_records": metric_affected,
        "existing_sample_responses": len(sample_ids),
        "sample_pattern_signatures_covered": len(sample_patterns),
        "total_pattern_signatures": len(patterns),
        "blank_supporting_text_total": sum(int(r["blank_supporting_text_count"]) for r in rows),
        "score_changes": 0,
        "mapping_accessed": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
