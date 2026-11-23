#!/usr/bin/env python3
"""Prepare pre-lock deviation, secondary-QC, and statistical-plan review artifacts."""

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
QC_DIR = EVAL / "stage1_secondary_qc_review_package"
PROVISIONAL = EVAL / "public" / "provisional_ai_metric_results.jsonl"
RESPONSES = EVAL / "responses"
SOURCE_PLAN = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_statistical_analysis_plan_v2_PROPOSED.md"
QC_POLICY = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_human_verification_and_qc_policy.md"
LOCK_POLICY = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_adjudication_and_score_lock_policy.md"
MAPPING = EVAL / "restricted" / "stage1_task_mapping.json"
SEED = 29012026
FRACTION = 0.20


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canon_sha(value: object) -> str:
    return hashlib.sha256(canon(value).encode("utf-8")).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def effective_assessment(response_id: str) -> tuple[dict, Path]:
    d = RESPONSES / response_id
    retry = d / "authorized_retry_1" / "ai_assessment.json"
    path = retry if retry.exists() else d / "ai_assessment.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def polarity_profile(scores: dict) -> str:
    tcc = "TCC_FULL" if scores["tcc"]["proportion"] == 1 else "TCC_NONFULL"
    sa = "SA_POSITIVE" if scores["scenario_adequacy"]["value"] == 1 else "SA_NEGATIVE"
    oracle = scores["strict_oracle"]
    if not oracle:
        o = "ORACLE_NA"
    elif all(x["binary_score"] == 1 for x in oracle):
        o = "ORACLE_ALL_CORRECT"
    else:
        o = "ORACLE_NONCORRECT"
    return "__".join((tcc, sa, o))


def metric_rows(record: dict, *, selection: str) -> list[dict]:
    a = record["assessment"]
    scores = record["scores"]
    global_flags = list(a.get("uncertainty_flags", []))
    result = []
    specs = [
        ("TCC", scores["tcc"], a.get("tcc_path_assessments", []), True),
        ("SCENARIO_ADEQUACY", scores["scenario_adequacy"], a.get("scenario_adequacy_assessment", {}), True),
        ("STRICT_ORACLE", scores["strict_oracle"], a.get("strict_oracle_assessments", []), bool(scores["strict_oracle"])),
    ]
    for metric, score, assessment, eligible in specs:
        if not eligible:
            continue
        flags = list(global_flags)
        if metric == "STRICT_ORACLE":
            labels = [x.get("semantic_label") for x in a.get("strict_oracle_assessments", [])]
            if "partial" in labels:
                flags.append("PARTIAL_ORACLE_MANDATORY_REVIEW")
        row_core = {
            "qc_item_id": f"SECONDARY::{record['anonymous_response_id']}::{metric}",
            "anonymous_response_id": record["anonymous_response_id"],
            "bug_id": record["bug_id"],
            "metric": metric,
            "selection_basis": selection,
            "risk_flags": sorted(set(flags)),
            "ai_score": score,
            "ai_assessment": assessment,
            "ai_uncertainty_flags": global_flags,
            "ai_evidence_notes": a.get("evidence_notes", []),
            "generated_scenarios": record["generated_scenarios"],
            "effective_assessment_sha256": record["assessment_sha256"],
        }
        result.append({
            **row_core,
            "immutable_qc_record_sha256": canon_sha(row_core),
            "human_decision": "",
            "human_corrected_value_json": "",
            "human_correction_reason": "",
            "reviewer_id": "",
            "review_timestamp_utc": "",
        })
    return result


def write_workbook(path: Path, rows: list[dict]) -> None:
    fields = [
        "qc_item_id", "anonymous_response_id", "bug_id", "metric", "selection_basis",
        "risk_flags_json", "ai_score_json", "ai_assessment_json", "ai_uncertainty_flags_json",
        "ai_evidence_notes_json", "generated_scenarios_json", "effective_assessment_sha256",
        "immutable_qc_record_sha256", "human_decision", "human_corrected_value_json",
        "human_correction_reason", "reviewer_id", "review_timestamp_utc",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({
                "qc_item_id": r["qc_item_id"],
                "anonymous_response_id": r["anonymous_response_id"],
                "bug_id": r["bug_id"],
                "metric": r["metric"],
                "selection_basis": r["selection_basis"],
                "risk_flags_json": canon(r["risk_flags"]),
                "ai_score_json": canon(r["ai_score"]),
                "ai_assessment_json": canon(r["ai_assessment"]),
                "ai_uncertainty_flags_json": canon(r["ai_uncertainty_flags"]),
                "ai_evidence_notes_json": canon(r["ai_evidence_notes"]),
                "generated_scenarios_json": canon(r["generated_scenarios"]),
                "effective_assessment_sha256": r["effective_assessment_sha256"],
                "immutable_qc_record_sha256": r["immutable_qc_record_sha256"],
                "human_decision": "",
                "human_corrected_value_json": "",
                "human_correction_reason": "",
                "reviewer_id": "",
                "review_timestamp_utc": "",
            })


def scan_persisted_mapping_exposure() -> list[str]:
    findings = []
    restricted = MAPPING.resolve()
    for p in HERE.rglob("*"):
        if not p.is_file() or p.resolve() == restricted or p.suffix.lower() not in {".json", ".jsonl", ".md", ".txt", ".csv", ".py", ".log"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"AR1-[0-9a-f]{24}", text) and re.search(r"[\"']condition[\"']\s*:\s*[\"'][ABC][\"']", text):
            findings.append(str(p))
    return sorted(findings)


def main() -> None:
    QC_DIR.mkdir(parents=True, exist_ok=True)
    valid = [x for x in jsonl(PROVISIONAL) if x["evaluation_status"] == "AI_VALID_PROVISIONAL"]
    if len(valid) != 53:
        raise ValueError(f"Expected 53 valid evaluations, got {len(valid)}")
    records = []
    for row in valid:
        rid = row["anonymous_response_id"]
        assessment, assessment_path = effective_assessment(rid)
        package = json.loads((RESPONSES / rid / "anonymous_evaluation_package.json").read_text(encoding="utf-8"))
        records.append({
            "anonymous_response_id": rid,
            "bug_id": row["bug_id"],
            "scores": row["provisional_scores"],
            "assessment": assessment,
            "assessment_sha256": sha(assessment_path),
            "generated_scenarios": package["generated_scenarios_untrusted_data"],
            "stratum": polarity_profile(row["provisional_scores"]),
        })

    # Every effective assessment carries MISSING_EVIDENCE, so all responses are risk-targeted.
    risk_records = [r for r in records if r["assessment"].get("uncertainty_flags")]
    risk_rows = [x for r in risk_records for x in metric_rows(r, selection="MANDATORY_RISK_REVIEW_PROPOSED")]

    # Proposed shadow sample: deterministic response-level stratification over all valid responses.
    # It is not an authorized sample because the policy says random audit otherwise-unflagged items,
    # while the current unflagged population is empty.
    strata = defaultdict(list)
    for r in records:
        strata[r["stratum"]].append(r)
    sampled = []
    sample_counts = {}
    for stratum in sorted(strata):
        rows = sorted(strata[stratum], key=lambda x: x["anonymous_response_id"])
        derived_seed = int(hashlib.sha256(f"{SEED}|{stratum}".encode()).hexdigest(), 16)
        random.Random(derived_seed).shuffle(rows)
        n = max(1, math.ceil(len(rows) * FRACTION))
        chosen = rows[:n]
        sampled.extend(chosen)
        sample_counts[stratum] = {"population": len(rows), "sample": len(chosen)}
    random_rows = [x for r in sampled for x in metric_rows(r, selection="PROPOSED_SHADOW_RANDOM_AUDIT")]

    population_path = QC_DIR / "secondary_qc_population_immutable.jsonl"
    population_path.write_text("".join(canon(r) + "\n" for r in records), encoding="utf-8")
    risk_path = QC_DIR / "risk_targeted_secondary_qc_workbook.csv"
    random_path = QC_DIR / "proposed_random_secondary_qc_workbook.csv"
    write_workbook(risk_path, risk_rows)
    write_workbook(random_path, random_rows)

    review = ["# Stage 1 secondary-metric QC review package", "",
              "Status: **PROPOSED — NO HUMAN DECISIONS POPULATED**", "",
              "The package is anonymous with respect to A/B/C. AI assessments and scores are immutable inputs.", ""]
    for i, r in enumerate(records, 1):
        a = r["assessment"]
        review.extend([
            f"## {i}. `{r['anonymous_response_id']}` — `{r['bug_id']}`", "",
            f"- Proposed risk-targeted: **yes**", f"- Risk flags: `{', '.join(a.get('uncertainty_flags', []))}`",
            f"- Proposed shadow-random sample: **{'yes' if r in sampled else 'no'}**", f"- Sampling stratum: `{r['stratum']}`", "",
            "### TCC", "", "```json", json.dumps({"score": r['scores']['tcc'], "assessment": a.get('tcc_path_assessments', [])}, ensure_ascii=False, indent=2, sort_keys=True), "```", "",
            "### Scenario Adequacy", "", "```json", json.dumps({"score": r['scores']['scenario_adequacy'], "assessment": a.get('scenario_adequacy_assessment', {})}, ensure_ascii=False, indent=2, sort_keys=True), "```", "",
            "### Strict Oracle", "", "```json", json.dumps({"score": r['scores']['strict_oracle'], "assessment": a.get('strict_oracle_assessments', [])}, ensure_ascii=False, indent=2, sort_keys=True), "```", "",
            "### Generated scenarios", "", "```json", json.dumps(r['generated_scenarios'], ensure_ascii=False, indent=2, sort_keys=True), "```", "", "---", "",
        ])
    review_path = QC_DIR / "stage1_secondary_qc_review.md"
    review_path.write_text("\n".join(review), encoding="utf-8")

    oracle_eligible_responses = sum(bool(r["scores"]["strict_oracle"]) for r in records)
    manifest = {
        "status": "PROPOSED_AWAITING_RESEARCHER_APPROVAL",
        "valid_response_population": 53,
        "risk_targeted_responses": len(risk_records),
        "risk_targeted_judgment_rows": len(risk_rows),
        "unflagged_response_population": 0,
        "proposed_shadow_random_fraction": FRACTION,
        "proposed_seed": SEED,
        "proposed_shadow_random_responses": len(sampled),
        "proposed_shadow_random_judgment_rows": len(random_rows),
        "stratum_counts": sample_counts,
        "metric_eligibility": {
            "TCC": {"eligible": 53, "missing": 1, "missing_reason": "one Repeat 1 generation is infrastructure missing"},
            "SCENARIO_ADEQUACY": {"eligible": 53, "missing": 1, "missing_reason": "one Repeat 1 generation is infrastructure missing"},
            "STRICT_ORACLE": {"eligible_responses": oracle_eligible_responses, "ineligible_or_not_applicable_responses": 53 - oracle_eligible_responses, "missing_generation": 1},
        },
        "random_sample_authorized": False,
        "risk_review_completed": False,
        "human_decisions_populated": False,
        "condition_labels_included": False,
        "overlap_warning": "All 53 responses are risk-flagged. The shadow random sample overlaps risk-targeted records and cannot be treated as the policy's otherwise-unflagged random audit without explicit approval.",
        "source_hashes": {"provisional_results": sha(PROVISIONAL), "qc_policy": sha(QC_POLICY)},
        "output_hashes": {p.name: sha(p) for p in (population_path, risk_path, random_path, review_path)},
    }
    manifest_path = QC_DIR / "secondary_qc_package_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (QC_DIR / "README.md").write_text("""# Secondary-QC review package

This package is proposed and contains no human decisions. Review `stage1_secondary_qc_review.md`.
Do not begin review until the researcher approves the risk-review scope, random-audit overlap disposition,
20% fraction, seed 29012026, and escalation thresholds. No A/B/C labels are included.
""", encoding="utf-8")

    persisted_findings = scan_persisted_mapping_exposure()
    deviation = f"""# Stage 1 mapping-access deviation audit

Version: v1  
Status: documented, methodological disposition pending

## Confirmed facts

- During the preceding score-lock preflight, the active Codex process invoked a shell/Python repository-inspection command that opened the restricted `stage1_task_mapping.json`.
- Mapping contents appeared in that command's terminal/tool output. This audit does not reproduce them.
- The researcher issued the 36/36 accept-all semantic decision before that access. Mechanical workbook recording and validation occurred afterward.
- The effective evaluator calls and immutable AI assessments predate the access.
- The CSPC review package was produced before the access and contains no condition labels, task IDs, BM25 scores, or retrieval ranks.
- A scan found {len(persisted_findings)} non-restricted persisted file(s) containing both an anonymous response identifier and an explicit A/B/C condition-value field.
- The mapping was not used to produce a score lock, group comparison, p-value, confidence interval, or figure.
- The affected Codex process had workspace write capability, but the access command was read-only. Subsequent validation found no AI/evidence overwrite and all immutable hashes matched.

## Unsupported assumptions

- It is not supported to claim that no human could see the tool output; visibility of internal terminal/tool traces in every client surface is not established by repository evidence.
- It is not supported to claim the exposure was harmless merely because no persisted mapping relation was found.
- Exact OS process ID and a durable shell transcript are unavailable in repository artifacts.

## Potential threats to blinding

- The analysis agent became aware of actual condition identities before score lock.
- The same process was technically capable of writing workspace artifacts after exposure.
- Mechanical acceptance recording happened after exposure, although the underlying researcher decision was already fixed and no semantic reconsideration is evidenced.

## Recommended methodological disposition

Treat this as a documented blinding-procedure deviation, not as proof of biased scoring and not as automatically harmless. Preserve the accepted workbook and hashes. Use a fresh analysis process for any future lock/unblinding if feasible; restrict it from semantic score editing; have the researcher explicitly approve the deviation disposition before lock; disclose it in final limitations.

## Remaining uncertainty

Repository evidence cannot prove what was visible in all user interfaces, cannot reconstruct an OS-level audit trail, and cannot establish the analyst's cognitive influence. It can establish that evaluator inputs and the prepared human review materials did not contain the mapping and that no mapping-derived results were persisted.
"""
    (EVAL / "stage1_mapping_access_deviation_audit.md").write_text(deviation, encoding="utf-8")

    qc_plan = f"""# Stage 1 Secondary-Metric QC Plan — PROPOSED

Status: **PROPOSED; RESEARCHER APPROVAL REQUIRED**

Authoritative policies: `{QC_POLICY}` and `{LOCK_POLICY}`. Existing policy parameters remain pending; this document does not approve them.

## Scope and units

- Metrics: TCC, Scenario Adequacy, and Strict Oracle Correctness.
- Valid evaluated response population: 53 anonymous Repeat 1 responses.
- Sampling unit for the proposed random audit: anonymous response. Every eligible secondary metric for a selected response is reviewed; paths/oracles are nested evidence, not independent sampling units.
- Risk-review unit: anonymous response×eligible metric judgment.
- No A/B/C condition labels, ranks, retrieval scores, or restricted task IDs are used.

## Eligibility and missingness

- TCC and SA: 53 evaluated responses; one infrastructure-missing generation remains missing, never zero.
- Strict Oracle: only responses with frozen eligible Oracle entries are reviewed; ineligible/not-applicable entries remain excluded, not zero.
- Batch 2 and Batch 3 are excluded.

## Risk-targeted review

Route uncertainty, missing evidence, contradiction, invalid reference, ambiguous match, structural failure, prompt-injection risk, inconsistent selection, `partial` Oracle, suspected false positive, and other flagged cases to mandatory review. All 53 current assessments carry `MISSING_EVIDENCE`; therefore the conservative risk-targeted response population is 53.

## Proposed random audit

- Fraction: 20% per response-level composite polarity stratum, rounded up, minimum one per nonempty stratum — **PROPOSED**.
- Seed: `{SEED}` — **PROPOSED**.
- Strata: TCC full/nonfull × SA positive/negative × Strict Oracle all-correct/noncorrect/not-applicable.
- Algorithm: sort anonymous IDs; derive SHA-256 seed from `seed|stratum`; Python MT19937 shuffle; select `max(1, ceil(n×0.20))`.
- Resulting shadow sample: {len(sampled)} of 53 responses ({len(sampled)/53:.1%}).
- Because no response is currently unflagged, this shadow sample overlaps the risk population and is not an authorized “otherwise-unflagged” audit. Researcher must approve overlap, revise risk semantics, or waive the redundant random layer with justification.

## Review criteria

- TCC: frozen denominator, selected scenario/path, covered and missing trigger IDs, coherent single-scenario evidence, no cross-scenario union.
- SA: selected scenario, setup coverage, sequence coherence, practical specificity, observation specificity, and evidence spans.
- Strict Oracle: frozen eligibility, selected scenario consistency, applicable Oracle IDs, semantic label, exact evidence, and binary conversion (`partial` remains zero).
- Decisions: ACCEPT, OVERRIDE with corrected value and reason, or UNRESOLVED. AI assessment is immutable.

## Escalation thresholds — PROPOSED

- Warning: material-error rate >5% in the approved random audit.
- Expand review: >10% material error or any repeated systematic over-credit pattern.
- Full metric review: expanded sample remains above threshold or a critical Gold/selection defect is found.
- Any Gold defect suspends all arms for that Bug and follows the versioned Gold-amendment process.

No threshold, fraction, seed, overlap rule, or sample is approved by creating this document.
"""
    (EVAL / "stage1_secondary_qc_plan_PROPOSED.md").write_text(qc_plan, encoding="utf-8")

    source_hash = sha(SOURCE_PLAN)
    stat_candidate = f"""# Stage 1 Repeat 1 Statistical Analysis Plan — APPROVAL CANDIDATE

Status: **NOT APPROVED; NOT FROZEN; NO ANALYSIS AUTHORIZED**  
Source plan preserved unchanged: `{SOURCE_PLAN}`  
Source SHA-256: `{source_hash}`

## Design and estimand

This is an exploratory Stage 1 analysis using Repeat 1 only. The original planned design contains three repeats per Bug×condition; Repeat 2 and Repeat 3 have not been collected and are not imputed. No between-repeat variability, repeat-averaged reliability, or three-repeat estimand is claimed.

Primary endpoint: response-level CSPC, defined as the maximum final human-verified value across eligible Gold Paths for the same response. The Bug is the inferential unit. Paths from the same Bug are nested and never treated as independent observations.

Primary contrast: paired Bug-level B−A. Exploratory contrasts: B−C and C−A. A/B/C labels may be released only after a valid score lock.

## Eligible cohort and pairing

Use only frozen CSPC-eligible Bugs and Repeat 1. Report exact condition denominators and exact paired denominators for each contrast. Preserve each missing observation. Complete-case filtering is contrast-specific: a Bug enters B−A only when both A and B are observed, and analogously for B−C and C−A.

The infrastructure-missing `P3-025-B-R1` remains missing, never zero. It is outside the frozen CSPC-eligible cohort but affects eligible secondary metrics and their relevant paired denominators. Batch 2 and Batch 3 are excluded.

## Primary inference — candidate for approval

- Two-sided exact paired sign-flip/randomization test of the mean Bug-level B−A difference.
- Enumerate all sign assignments over nonzero paired differences; zero differences remain ties. Report the exact implementation and denominator.
- Paired Bug-level nonparametric bootstrap of the mean difference: 10,000 resamples, seed `29012026`.
- 95% percentile confidence interval using the 2.5th and 97.5th percentiles.
- Resample Bugs with replacement and carry both members of each pair together.

## Reporting

Report A/B/C CSPC numerators, denominators and rates; per-Bug scores; paired differences; positive/negative discordant counts; ties; mean and median effects; exact p-value; bootstrap interval; and missingness. Label inference exploratory due Repeat 1-only design and small sample.

Secondary metrics may be reported only after their QC is complete and scores are separately finalized. Provisional TCC, SA, Strict Oracle, unsupported-assumption, or context-transfer values must not be substituted for final scores.

## Deviations from original plan

1. One repeat rather than three; no within-Bug repeat averaging.
2. No between-repeat variability analysis.
3. Smaller effective information and limited power.
4. One infrastructure-missing B generation remains missing.
5. Human CSPC review used AI-assisted single-researcher acceptance, not two independent human evaluators plus a third adjudicator.
6. Restricted mapping was inspected pre-lock by the active process; no group analysis followed, but the deviation requires disclosure and disposition.

Explicit researcher approval and a versioned freeze record are required before score lock or analysis.
"""
    (EVAL / "stage1_statistical_plan_approval_candidate.md").write_text(stat_candidate, encoding="utf-8")

    checklist = """# Stage 1 Pre-Lock Approval Checklist

No box below is automatically approved.

## Mapping-access deviation

- [ ] Accept the deviation report as factually adequate.
- [ ] Choose disposition: continue with disclosure / require independent re-review / invalidate affected review / other documented decision.
- [ ] Decide whether a fresh, score-read-only analysis process is required.

## Secondary QC

- [ ] Approve or revise the conservative risk-targeted scope.
- [ ] Approve or revise the 20% fraction.
- [ ] Approve or revise seed 29012026.
- [ ] Decide whether the overlapping 12-response shadow sample is allowed, redundant, or must be replaced after risk review.
- [ ] Approve or revise >5% warning, >10% expansion, and full-review triggers.
- [ ] Complete the approved risk and random reviews; resolve every override/unresolved record.

## Statistical plan

- [ ] Approve Repeat 1-only exploratory estimand and deviations.
- [ ] Approve CSPC as primary and B−A as primary contrast.
- [ ] Approve B−C and C−A as exploratory.
- [ ] Approve exact two-sided paired sign-flip test.
- [ ] Approve paired 10,000-resample percentile bootstrap with seed 29012026.
- [ ] Approve contrast-specific missingness handling and exact denominators.
- [ ] Create and hash a versioned `APPROVED/FROZEN` plan; do not edit the proposal in place.

## Final gate

- [ ] Re-run human CSPC validation.
- [ ] Verify secondary QC complete.
- [ ] Verify zero unresolved decisions.
- [ ] Verify all manifest/provenance hashes.
- [ ] Run score-lock gate and require `score_locked=true` before mapping release or group analysis.
"""
    (EVAL / "stage1_prelock_approval_checklist.md").write_text(checklist, encoding="utf-8")

    print(json.dumps({
        "valid_responses": 53,
        "risk_targeted_responses": len(risk_records),
        "risk_judgment_rows": len(risk_rows),
        "shadow_random_responses": len(sampled),
        "shadow_random_judgment_rows": len(random_rows),
        "persisted_mapping_relation_findings_outside_restricted_file": len(persisted_findings),
        "score_lock_performed": False,
        "unblinding_performed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
