#!/usr/bin/env python3
"""Exploratory, explicitly authorized Stage 1 A/B/C analysis (pre-score-lock)."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
MAPPING = EVAL / "restricted" / "stage1_task_mapping.json"
GENERATION_MANIFEST = HERE / "phase3d_stage1_54_generation_manifest.jsonl"
PROVISIONAL = EVAL / "public" / "provisional_ai_metric_results.jsonl"
MISSINGNESS = EVAL / "public" / "stage1_evaluation_missingness.json"
CSPC = EVAL / "stage1_cspc_human_verification_preparation" / "cspc_human_verification_workbook.csv"
SECONDARY_QC = EVAL / "stage1_secondary_qc_human_review_workbook_FINAL.csv"
ORACLE = EVAL / "stage1_oracle_effective_scores_FINAL.csv"
ORACLE_SUPPLEMENT = EVAL / "stage1_oracle_supplemental_adjudication_FINAL.csv"
QC_POLICY = EVAL / "stage1_secondary_qc_policy_v1_APPROVED.json"
RUBRIC_AMENDMENT = EVAL / "stage1_strict_oracle_applicability_amendment_v1_FROZEN.json"
STAT_AMENDMENT = EVAL / "stage1_statistical_plan_oracle_amendment_v1_FROZEN.md"
READINESS = EVAL / "stage1_score_lock_readiness_validation.json"

REPORT = EVAL / "stage1_exploratory_analysis.md"
BUG_CSV = EVAL / "stage1_exploratory_bug_level.csv"
SUMMARY_CSV = EVAL / "stage1_exploratory_condition_summary.csv"
PAIRS_CSV = EVAL / "stage1_exploratory_paired_comparisons.csv"
VALIDATION = EVAL / "stage1_exploratory_analysis_validation.json"
FIGURE = EVAL / "stage1_exploratory_condition_rates.svg"

SEED = 29012026
BOOTSTRAPS = 10_000


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * probability
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def exact_sign_flip_pvalue(differences: list[float]) -> tuple[float, int, str]:
    nonzero = [abs(x) for x in differences if not math.isclose(x, 0.0, abs_tol=1e-12)]
    if not nonzero:
        return 1.0, 0, "ALL_TIES_NO_INFORMATION"
    observed = abs(sum(x for x in differences if not math.isclose(x, 0.0, abs_tol=1e-12)))
    extreme = 0
    total = 2 ** len(nonzero)
    for signs in itertools.product((-1.0, 1.0), repeat=len(nonzero)):
        value = abs(sum(sign * magnitude for sign, magnitude in zip(signs, nonzero)))
        if value >= observed - 1e-12:
            extreme += 1
    return extreme / total, len(nonzero), "EXACT_ENUMERATION"


def bootstrap_ci(differences: list[float]) -> tuple[float, float]:
    rng = random.Random(SEED)
    n = len(differences)
    samples = [sum(differences[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAPS)]
    return percentile(samples, 0.025), percentile(samples, 0.975)


def make_svg(condition_rows: list[dict]) -> None:
    metrics = ["CSPC", "TCC", "Scenario Adequacy", "Strict Oracle (response)"]
    values = {(x["metric"], x["condition"]): float(x["value"]) for x in condition_rows if x["aggregation_unit"] != "oracle_entry"}
    width, height = 920, 470
    left, top, plot_w, plot_h = 70, 45, 790, 330
    colors = {"A": "#607D8B", "B": "#1976D2", "C": "#F57C00"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="460" y="25" text-anchor="middle" font-family="sans-serif" font-size="17">Stage 1 exploratory condition rates (Repeat 1)</text>']
    for tick in range(0, 11, 2):
        y = top + plot_h - plot_h * tick / 10
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#dddddd"/>')
        parts.append(f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" font-family="sans-serif" font-size="11">{tick*10}%</text>')
    group_w = plot_w / len(metrics)
    bar_w = 36
    for i, metric in enumerate(metrics):
        center = left + group_w * (i + 0.5)
        for j, condition in enumerate("ABC"):
            value = values[(metric, condition)]
            h = plot_h * value
            x = center + (j - 1) * (bar_w + 5) - bar_w / 2
            y = top + plot_h - h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" fill="{colors[condition]}"/>')
            parts.append(f'<text x="{x+bar_w/2:.1f}" y="{max(y-5, 39):.1f}" text-anchor="middle" font-family="sans-serif" font-size="9">{value*100:.1f}</text>')
        parts.append(f'<text x="{center:.1f}" y="{top+plot_h+28}" text-anchor="middle" font-family="sans-serif" font-size="11">{metric}</text>')
    for j, condition in enumerate("ABC"):
        x = 345 + j * 90
        parts.append(f'<rect x="{x}" y="425" width="14" height="14" fill="{colors[condition]}"/>')
        parts.append(f'<text x="{x+20}" y="437" font-family="sans-serif" font-size="12">{condition}</text>')
    parts.append('<text x="460" y="462" text-anchor="middle" font-family="sans-serif" font-size="10" fill="#555">Raw rates; B has 17 rather than 18 responses for TCC/SA because one generation is infrastructure-missing.</text>')
    parts.append('</svg>')
    FIGURE.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    generation_manifest = load_jsonl(GENERATION_MANIFEST)
    generation_by_task = {x["task_id"]: x for x in generation_manifest}
    all_results = load_jsonl(PROVISIONAL)
    valid_results = [x for x in all_results if x["evaluation_status"] == "AI_VALID_PROVISIONAL"]
    valid_by_id = {x["anonymous_response_id"]: x for x in valid_results}
    missing_results = [x for x in all_results if x["evaluation_status"] != "AI_VALID_PROVISIONAL"]
    missingness = json.loads(MISSINGNESS.read_text(encoding="utf-8"))
    cspc_rows = read_csv(CSPC)
    secondary_qc = read_csv(SECONDARY_QC)
    oracle_rows = read_csv(ORACLE)

    # Mandatory join integrity checks before analysis.
    assert len(valid_results) == 53 and len(valid_by_id) == 53
    assert len(generation_manifest) == 54 and len(generation_by_task) == 54
    assert set(valid_by_id) == set(mapping)
    assert all(mapping[rid]["bug_id"] == result["bug_id"] for rid, result in valid_by_id.items())
    assert all(
        mapping[rid]["task_id"] in generation_by_task
        and generation_by_task[mapping[rid]["task_id"]]["bug_id"] == mapping[rid]["bug_id"]
        and generation_by_task[mapping[rid]["task_id"]]["condition"] == mapping[rid]["condition"]
        and generation_by_task[mapping[rid]["task_id"]]["effective_output_sha256"] == mapping[rid]["generation_output_sha256"]
        and generation_by_task[mapping[rid]["task_id"]]["effective_status"] == "STRUCTURALLY_VALID"
        for rid in mapping
    )
    assert len({(x["bug_id"], mapping[x["anonymous_response_id"]]["condition"]) for x in valid_results}) == 53
    assert Counter(mapping[rid]["condition"] for rid in mapping) == {"A": 18, "B": 17, "C": 18}
    assert len(missing_results) == 1 and missing_results[0]["generation_status"] == "INFRASTRUCTURE_MISSING"
    assert missingness["generation_infrastructure_missing"] == 1 and missingness["generation_missing_scored_as_zero"] is False
    missing_task = generation_by_task[missingness["generation_missing_task"]]
    assert missing_task["effective_status"] == "INFRASTRUCTURE_FAILURE"
    assert missing_task["effective_output_sha256"] is None
    assert missing_task["bug_id"] == missing_results[0]["bug_id"]
    assert len(secondary_qc) == 43 and Counter(x["human_decision"] for x in secondary_qc) == {"ACCEPT": 41, "CORRECT": 2}

    cspc_by_response: dict[str, list[int]] = defaultdict(list)
    cspc_paths_by_bug: dict[str, set[str]] = defaultdict(set)
    for row in cspc_rows:
        assert row["human_decision"] == "ACCEPT"
        cspc_by_response[row["anonymous_response_id"]].append(int(row["ai_cspc_judgment"]))
        cspc_paths_by_bug[row["bug_id"]].add(row["gold_path_id"])
    assert len(cspc_rows) == 36 and len(cspc_paths_by_bug) == 11 and sum(len(x) for x in cspc_paths_by_bug.values()) == 12

    oracle_by_response: dict[str, list[int]] = defaultdict(list)
    excluded_oracles = []
    for row in oracle_rows:
        if row["human_approved_applicability"] == "APPLICABLE":
            oracle_by_response[row["anonymous_response_id"]].append(int(row["effective_score"]))
        elif row["human_approved_applicability"] == "NOT_APPLICABLE":
            excluded_oracles.append(row)
        else:
            raise AssertionError("Unresolved Oracle applicability")
    assert len(oracle_rows) == 30 and len(excluded_oracles) == 3
    assert sum(sum(v) for v in oracle_by_response.values()) == 24
    assert sum(len(v) for v in oracle_by_response.values()) == 27

    response_rows = []
    for rid in sorted(valid_by_id):
        result = valid_by_id[rid]
        score = result["provisional_scores"]
        oracle_values = oracle_by_response.get(rid, [])
        response_rows.append({
            "anonymous_response_id": rid,
            "bug_id": result["bug_id"],
            "condition": mapping[rid]["condition"],
            "cspc": max(cspc_by_response[rid]) if rid in cspc_by_response else None,
            "tcc": float(score["tcc"]["proportion"]),
            "scenario_adequacy": int(score["scenario_adequacy"]["value"]),
            "oracle_response_score": sum(oracle_values) / len(oracle_values) if oracle_values else None,
            "oracle_numerator": sum(oracle_values),
            "oracle_denominator": len(oracle_values),
            "selected_scenario_id": score["tcc"]["selected_scenario_id"],
            "selected_gold_path_id": score["tcc"]["selected_gold_path_id"],
        })

    by_bug: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in response_rows:
        by_bug[row["bug_id"]][row["condition"]] = row
    missing_bug = missing_results[0]["bug_id"]
    assert len(by_bug) == 18 and sum(set(v) == {"A", "B", "C"} for v in by_bug.values()) == 17
    assert set(by_bug[missing_bug]) == {"A", "C"}

    bug_rows = []
    for bug_id in sorted(by_bug):
        row: dict[str, object] = {
            "bug_id": bug_id,
            "cspc_eligible": bug_id in cspc_paths_by_bug,
            "cspc_gold_path_ids": ";".join(sorted(cspc_paths_by_bug.get(bug_id, set()))),
            "complete_three_arm_triple": set(by_bug[bug_id]) == {"A", "B", "C"},
        }
        for condition in "ABC":
            item = by_bug[bug_id].get(condition)
            row[f"{condition}_response_id"] = item["anonymous_response_id"] if item else ""
            row[f"{condition}_status"] = "VALID" if item else "INFRASTRUCTURE_MISSING"
            for metric in ["cspc", "tcc", "scenario_adequacy", "oracle_response_score", "oracle_numerator", "oracle_denominator"]:
                row[f"{condition}_{metric}"] = "" if item is None or item[metric] is None else item[metric]
            row[f"{condition}_selected_scenario_id"] = item["selected_scenario_id"] if item else ""
            row[f"{condition}_selected_gold_path_id"] = item["selected_gold_path_id"] if item else ""
        bug_rows.append(row)
    write_csv(BUG_CSV, bug_rows)

    metric_specs = [
        ("CSPC", "cspc", "bug_response", "binary success; max across eligible Gold paths"),
        ("TCC", "tcc", "response", "selected-path trigger coverage proportion"),
        ("Scenario Adequacy", "scenario_adequacy", "response", "binary adequate"),
        ("Strict Oracle (response)", "oracle_response_score", "bug_response", "mean across applicable Oracle entries within response"),
    ]
    condition_rows = []
    for metric_name, key, unit, definition in metric_specs:
        for condition in "ABC":
            values = [float(x[key]) for x in response_rows if x["condition"] == condition and x[key] is not None]
            condition_rows.append({
                "metric": metric_name, "condition": condition, "aggregation_unit": unit,
                "numerator_or_sum": sum(values), "denominator": len(values), "value": sum(values) / len(values),
                "missing_count": (11 if key == "cspc" else 18 if key in {"tcc", "scenario_adequacy"} else 9) - len(values),
                "definition": definition,
            })
    for condition in "ABC":
        rows = [x for x in response_rows if x["condition"] == condition]
        numerator = sum(x["oracle_numerator"] for x in rows)
        denominator = sum(x["oracle_denominator"] for x in rows)
        condition_rows.append({
            "metric": "Strict Oracle (entry pooled)", "condition": condition, "aggregation_unit": "oracle_entry",
            "numerator_or_sum": numerator, "denominator": denominator, "value": numerator / denominator,
            "missing_count": 0, "definition": "applicable Oracle entries only; descriptive, not independent Bug units",
        })
    write_csv(SUMMARY_CSV, condition_rows)

    comparison_rows = []
    contrasts = [("B-A", "B", "A"), ("B-C", "B", "C"), ("C-A", "C", "A")]
    for metric_name, key, unit, _ in metric_specs:
        for contrast, first, second in contrasts:
            pairs = []
            for bug_id, conditions in by_bug.items():
                if first in conditions and second in conditions:
                    one, two = conditions[first][key], conditions[second][key]
                    if one is not None and two is not None:
                        pairs.append((bug_id, float(one) - float(two)))
            differences = [x[1] for x in pairs]
            pvalue, nonzero, test_status = exact_sign_flip_pvalue(differences)
            low, high = bootstrap_ci(differences)
            comparison_rows.append({
                "metric": metric_name, "contrast": contrast, "analysis_unit": "Bug",
                "paired_bugs": len(pairs), "nonzero_differences": nonzero,
                "wins": sum(x > 0 for x in differences), "ties": sum(math.isclose(x, 0.0, abs_tol=1e-12) for x in differences),
                "losses": sum(x < 0 for x in differences), "mean_difference": sum(differences) / len(differences),
                "median_difference": statistics.median(differences), "exact_sign_flip_pvalue_two_sided": pvalue,
                "exact_test_status": test_status, "bootstrap_samples": BOOTSTRAPS, "bootstrap_seed": SEED,
                "bootstrap_95_percentile_low": low, "bootstrap_95_percentile_high": high,
            })
    write_csv(PAIRS_CSV, comparison_rows)
    make_svg(condition_rows)

    lookup = {(x["metric"], x["condition"]): x for x in condition_rows}
    pair_lookup = {(x["metric"], x["contrast"]): x for x in comparison_rows}
    report = f"""# Stage 1 Exploratory Analysis of Cross-Project Context Effectiveness

Status: **exploratory, pre-score-lock, researcher-authorized unblinding**. Repeat 1 only.

## Key numerical results

| Metric | A | B | C |
|---|---:|---:|---:|
| CSPC Bug success | 10/11 (90.9%) | 10/11 (90.9%) | 10/11 (90.9%) |
| TCC response mean | 18/18 (100.0%) | 17/17 (100.0%) | 18/18 (100.0%) |
| Scenario Adequacy | 17/18 (94.4%) | 16/17 (94.1%) | 17/18 (94.4%) |
| Strict Oracle response mean | 8/9 (88.9%) | 8/9 (88.9%) | 8/9 (88.9%) |
| Strict Oracle entry pooled | 8/9 (88.9%) | 8/9 (88.9%) | 8/9 (88.9%) |

The slightly different unpaired Scenario Adequacy percentages are caused only by the single infrastructure-missing B response: on paired Bugs, every A/B/C score is tied.

![Condition rates](stage1_exploratory_condition_rates.svg)

## CSPC primary endpoint

All 11 CSPC-eligible Bugs have complete A/B/C observations. Each condition succeeds on the same 10 Bugs and fails on the same one Bug.

| Contrast | Paired Bugs | Mean difference | Win/Tie/Loss | Nonzero | Exact p | Bootstrap 95% CI |
|---|---:|---:|---:|---:|---:|---:|
| B−A | 11 | 0.000 | 0/11/0 | 0 | 1.000 | [0.000, 0.000] |
| B−C | 11 | 0.000 | 0/11/0 | 0 | 1.000 | [0.000, 0.000] |
| C−A | 11 | 0.000 | 0/11/0 | 0 | 1.000 | [0.000, 0.000] |

Because all differences are zero, the exact sign-flip procedure has no nonzero signs to test. The reported p=1 and degenerate [0,0] interval express no observed contrast, not evidence of equivalence.

## Secondary metrics and aggregation

- TCC is response-level selected-path trigger coverage. Every valid response scores 1.0. B comparisons use 17 paired Bugs because one B generation is missing; C−A uses 18.
- Scenario Adequacy is response-level binary. Its single failure occurs for the same Bug under A, B, and C. Every paired contrast is entirely tied.
- Strict Oracle is first aggregated within each response across applicable Oracle entries, then compared at Bug level. Nine Bugs per condition are eligible; all contrasts are tied. Entry-pooled reporting is shown only descriptively and does not treat entries as independent Bugs.
- The three approved NOT_APPLICABLE Oracle entries are excluded. The three applicable zero entries remain in their denominators. Effective Oracle entries reconcile to 24/27 overall and 8/9 in each condition.

For every secondary metric and contrast, mean and median differences are 0, win/tie/loss contains only ties, exact p is 1, and the paired-bootstrap interval is [0,0]. Exact paired denominators are in `stage1_exploratory_paired_comparisons.csv`.

## Mechanism diagnosis

There are **no Bugs where B differs from A or C** on CSPC, TCC, Scenario Adequacy, or response-level Strict Oracle. Consequently, this dataset contains no metric-linked case in which BM25 context can be shown to supply a relevant trigger, repair a missed path, or cause deterioration.

Two representative shared patterns explain the ties:

1. `gameservermanagers/linuxgsm#4251` has CSPC=0 and applicable Strict Oracle=0 in all three conditions. Across A/B/C, generated scenarios cover trigger-level actions but fail to test the full expected observation, particularly the installed-RPM recognition/backend and inappropriate apt-recommendation behavior. BM25 context did not close that common Oracle/path gap.
2. `lichess-org/lila#12907` has Scenario Adequacy=0 in all three conditions. Each selected scenario observes an already-completed game stream rather than establishing the required controlled in-progress subscription setup. The B response also notes fixture uncertainty, but this does not change the shared outcome.

These are evidence-supported descriptions of generated scenarios and Gold requirements. They do not establish that retrieved context caused any behavior. Whether a retrieved issue was semantically relevant or copied into a scenario is not inferred merely from condition membership.

## Interpretation

1. **Is B better than A?** No observed improvement: all paired differences are zero.
2. **Is C better than A?** No observed improvement: all paired differences are zero.
3. **Is B better than C?** No observed improvement: all paired differences are zero.
4. **Are improvements consistent across metrics?** There are no observed improvements on any finalized metric. High rates and universal TCC create substantial ceiling effects.
5. **Is the evidence strong enough to justify continuing?** It is not strong enough to claim a cross-project-context or BM25-specific benefit. Continuation is defensible only as further exploratory work aimed at reducing ceiling effects, collecting the planned repeats, increasing discriminating cases, and adding direct context-use/relevance diagnostics.

**Conclusion classification: No clear preliminary benefit.** The data also do not demonstrate harm: they are dominated by ties and have limited power.

## Integrity, missingness, and limitations

- Planned design: three repeats; analyzed design: Repeat 1 only.
- 18 Bugs, 54 planned generations, 53 valid responses, and 17 complete three-arm triples.
- One B generation is infrastructure-missing and remains missing, never zero. Contrast-specific paired denominators are used.
- Primary CSPC inference uses only 11 eligible Bugs and shows a strong ceiling (10/11 in every condition).
- There is no stochastic repeatability estimate and no between-repeat analysis.
- Human review is single-researcher and AI-assisted, not independent multi-reviewer adjudication.
- A previous score-lock attempt was blocked after condition information was exposed. The researcher explicitly authorized this exploratory unblinding; formal score lock was not completed.
- These p-values are exploratory. This is not preregistered confirmatory inference and supports no broad causal or generalization claim.
"""
    REPORT.write_text(report, encoding="utf-8")

    checks = []
    def ck(name: str, condition: bool, detail: object) -> None:
        checks.append({"check": name, "status": "PASS" if condition else "FAIL", "detail": detail})
    ck("unique_response_condition_mapping", len(mapping) == 53 and set(mapping) == set(valid_by_id), len(mapping))
    ck("mapping_matches_frozen_generation_manifest", len(generation_manifest) == 54 and all(
        generation_by_task[mapping[rid]["task_id"]]["bug_id"] == mapping[rid]["bug_id"]
        and generation_by_task[mapping[rid]["task_id"]]["condition"] == mapping[rid]["condition"]
        and generation_by_task[mapping[rid]["task_id"]]["effective_output_sha256"] == mapping[rid]["generation_output_sha256"]
        for rid in mapping
    ), {"mapped_valid": len(mapping), "planned_tasks": len(generation_manifest)})
    ck("bug_identifiers_match", all(mapping[rid]["bug_id"] == valid_by_id[rid]["bug_id"] for rid in mapping), None)
    ck("no_duplicate_bug_condition", len({(x["bug_id"], x["condition"]) for x in response_rows}) == 53, 53)
    ck("valid_and_missing_counts", len(response_rows) == 53 and len(missing_results) == 1, {"valid": len(response_rows), "missing": len(missing_results)})
    ck("complete_triples", sum(x["complete_three_arm_triple"] is True for x in bug_rows) == 17, sum(x["complete_three_arm_triple"] is True for x in bug_rows))
    ck("cspc_population", len(cspc_rows) == 36 and len(cspc_paths_by_bug) == 11 and sum(len(x) for x in cspc_paths_by_bug.values()) == 12, {"judgments": len(cspc_rows), "bugs": len(cspc_paths_by_bug), "paths": sum(len(x) for x in cspc_paths_by_bug.values())})
    ck("oracle_reconciliation", len(excluded_oracles) == 3 and sum(sum(v) for v in oracle_by_response.values()) == 24 and sum(len(v) for v in oracle_by_response.values()) == 27, {"excluded": len(excluded_oracles), "numerator": sum(sum(v) for v in oracle_by_response.values()), "denominator": sum(len(v) for v in oracle_by_response.values())})
    ck("missing_never_zero", missingness["generation_missing_scored_as_zero"] is False and by_bug[missing_bug].get("B") is None, missing_bug)
    ck("paired_results_all_ties", all(x["nonzero_differences"] == 0 and x["wins"] == 0 and x["losses"] == 0 for x in comparison_rows), len(comparison_rows))
    ck("expected_output_dimensions", len(bug_rows) == 18 and len(condition_rows) == 15 and len(comparison_rows) == 12, {"bugs": len(bug_rows), "summaries": len(condition_rows), "comparisons": len(comparison_rows)})
    passed = all(x["status"] == "PASS" for x in checks)
    source_paths = [MAPPING, GENERATION_MANIFEST, PROVISIONAL, MISSINGNESS, CSPC, SECONDARY_QC, ORACLE, ORACLE_SUPPLEMENT, QC_POLICY, RUBRIC_AMENDMENT, STAT_AMENDMENT, READINESS]
    output_paths = [REPORT, BUG_CSV, SUMMARY_CSV, PAIRS_CSV, FIGURE]
    validation = {
        "artifact_version": "stage1-exploratory-analysis-validation-v1",
        "status": "PASS" if passed else "FAIL",
        "checks_passed": sum(x["status"] == "PASS" for x in checks),
        "checks_failed": sum(x["status"] == "FAIL" for x in checks),
        "checks": checks,
        "source_hashes": {str(x.relative_to(HERE.parent.parent)): sha(x) for x in source_paths},
        "output_hashes": {x.name: sha(x) for x in output_paths},
        "score_lock_required": False,
        "score_lock_completed": False,
        "researcher_authorized_exploratory_unblinding": True,
        "condition_mapping_accessed": True,
        "llm_calls_performed": False,
        "original_artifacts_modified": False,
        "bootstrap": {"samples": BOOTSTRAPS, "seed": SEED, "interval": "95% percentile"},
    }
    VALIDATION.write_text(json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not passed:
        raise SystemExit("Exploratory analysis validation failed")
    print(json.dumps({"status": validation["status"], "checks_passed": validation["checks_passed"], "checks_failed": validation["checks_failed"], "conclusion": "No clear preliminary benefit"}, indent=2))


if __name__ == "__main__":
    main()
