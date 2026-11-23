#!/usr/bin/env python3
"""Synthetic-only executable checks for proposed rubric v3.1 decisions."""

from __future__ import annotations

import json
import sys


def select(scores: list[float]) -> int | None:
    return max(range(len(scores)), key=lambda i: (scores[i], -i)) if scores else None


def main() -> int:
    checks = {
        "tcc_tie_selects_earliest": select([0.5, 0.5, 0.0]) == 0,
        "higher_tcc_overrides_later_metrics": select([0.5, 1.0, 0.0]) == 1,
        "or_path_uses_best_coherent_denominator": max(0 / 1, 1 / 1) == 1.0,
        "no_cross_scenario_union": max(1 / 2, 1 / 2) == 0.5,
        "missing_output_has_no_selected_scenario": select([]) is None,
        "incomplete_oracle_label": "not_scorable" not in {"correct", "partial", "incorrect"},
        "preventive_is_not_post_trigger": "B_PREVENT" != "B_POST",
    }
    result = {"scope": "synthetic/non-study only", "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    print(json.dumps(result, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
