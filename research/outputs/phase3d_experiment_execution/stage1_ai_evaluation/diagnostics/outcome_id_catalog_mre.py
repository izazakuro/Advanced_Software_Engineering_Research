#!/usr/bin/env python3
"""Minimal offline reproduction of the missing outcome_id catalog defect."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[3]
PIPELINE_PATH = RESEARCH / "outputs/phase3c_h_ai_first_amendment/phase3c_h_evaluation_pipeline.py"


def load_module():
    spec = importlib.util.spec_from_file_location("outcome_id_mre_pipeline", PIPELINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    pipeline = load_module()
    minimal_gold = {
        "bug_id": "synthetic/project#1",
        "test_oracles": [{
            "oracle_id": "O1",
            "acceptable_expected_outcomes": [{"outcome_id": "A1", "description": "supported outcome"}],
        }],
    }
    catalog = pipeline.gold_requirement_catalog(minimal_gold)
    result = {
        "status": "DEFECT_REPRODUCED" if "O1" in catalog and "A1" not in catalog else "NOT_REPRODUCED",
        "catalog": sorted(catalog),
        "expected_present": ["O1", "A1"],
        "actual_O1_present": "O1" in catalog,
        "actual_A1_present": "A1" in catalog,
        "proposed_minimal_patch": "add outcome_id to gold_requirement_catalog.id_keys",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "DEFECT_REPRODUCED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
