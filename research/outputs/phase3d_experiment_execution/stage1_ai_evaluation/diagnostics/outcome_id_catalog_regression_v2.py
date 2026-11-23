#!/usr/bin/env python3
"""Confirm the scoped outcome_id catalog correction and arbitrary-ID rejection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[3]
PIPELINE = RESEARCH / "outputs/phase3c_h_ai_first_amendment/phase3c_h_evaluation_pipeline.py"


spec = importlib.util.spec_from_file_location("outcome_id_regression_v2", PIPELINE)
if spec is None or spec.loader is None:
    raise RuntimeError(PIPELINE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

gold = {
    "bug_id": "synthetic/project#1",
    "test_oracles": [{
        "oracle_id": "O1",
        "acceptable_expected_outcomes": [{"outcome_id": "A1"}],
        "other": {"outcome_id": "ARBITRARY_NESTED"},
    }],
    "known_limitations": [{"outcome_id": "ARBITRARY_TOP"}],
}
catalog = module.gold_requirement_catalog(gold)
result = {
    "status": "PASS" if {"O1", "A1"} <= catalog and not ({"ARBITRARY_NESTED", "ARBITRARY_TOP"} & catalog) else "FAIL",
    "catalog": sorted(catalog),
    "legitimate_A1_accepted": "A1" in catalog,
    "arbitrary_nested_rejected": "ARBITRARY_NESTED" not in catalog,
    "arbitrary_top_rejected": "ARBITRARY_TOP" not in catalog,
}
print(json.dumps(result, indent=2, sort_keys=True))
if result["status"] != "PASS":
    raise SystemExit(1)
