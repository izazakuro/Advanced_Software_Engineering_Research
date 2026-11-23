from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
RESEARCH = HERE.parents[1]
AB = RESEARCH / "outputs/phase3_ab_offline_preparation"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


EVAL = load("phase3c_eval_test", HERE / "phase3c_offline_evaluator.py")
ADAPTER = load("phase3c_adapter_test", HERE / "phase3c_task_adapter.py")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def valid_response(bug: str) -> dict:
    scenarios = []
    for i in range(1, 4):
        scenarios.append({
            "scenario_id": f"S{i}", "preconditions": [], "environment_assumptions": [],
            "trigger_conditions": [f"trigger {i}"],
            "ordered_execution_steps": [{"step": 1, "action": "perform supported action"}],
            "expected_observable_behavior": "observe supported result", "test_oracle": "pass when supported result is observed",
            "uncertainty_or_unsupported_assumptions": [],
        })
    return {"bug_id": bug, "scenarios": scenarios}


class Phase3CTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.formal = jsonl(HERE / "phase3c_formal_162_task_schedule_v2_NOT_AUTHORIZED.jsonl")
        cls.pilot = jsonl(HERE / "phase3c_pilot_18_task_schedule_v2_NOT_AUTHORIZED.jsonl")
        cls.payloads = jsonl(HERE / "phase3c_formal_162_task_payloads_v2_NOT_AUTHORIZED.jsonl")
        cls.eligibility = json.loads((FROZEN / "gold_v1_metric_eligibility.json").read_text())

    def test_frozen_hashes(self):
        self.assertEqual(hashlib.sha256((FROZEN / "gold_v1.jsonl").read_bytes()).hexdigest(), "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66")
        self.assertEqual(hashlib.sha256((FROZEN / "gold_v1_freeze_manifest.json").read_bytes()).hexdigest(), "0886571b6390ab461b1f72ee43ff4628c39cfc76275378207b0546026c51abdc")

    def test_prompt_v2_refinements(self):
        prompt = (HERE / "phase3_generation_prompt_v2_PROPOSED.txt").read_text()
        for phrase in ("self-contained", "must not depend", "Do not invent", "missing prerequisites", "Return one JSON object only", "no prose outside"):
            self.assertIn(phrase, prompt)
        self.assertNotIn("Gold", prompt)
        self.assertNotIn("Rubric", prompt)

    def test_schema_fixes_ids_and_exact_count(self):
        schema = json.loads((HERE / "phase3_generation_output_v2_PROPOSED.schema.json").read_text())
        array = schema["properties"]["scenarios"]
        self.assertEqual((array["minItems"], array["maxItems"], array["items"]), (3, 3, False))
        self.assertEqual([x["$ref"] for x in array["prefixItems"]], ["#/$defs/scenario_s1", "#/$defs/scenario_s2", "#/$defs/scenario_s3"])
        self.assertEqual(schema["$defs"]["scenario_s1"]["allOf"][1]["properties"]["scenario_id"]["const"], "S1")

    def test_versioned_schedule_preserves_design(self):
        self.assertEqual(len(self.formal), 162)
        self.assertEqual(len(self.pilot), 18)
        self.assertEqual(len({x["target_bug_id"] for x in self.formal}), 18)
        self.assertEqual(len({x["target_bug_id"] for x in self.pilot}), 6)
        old = jsonl(AB / "phase3_formal_162_task_schedule.jsonl")
        for a, b in zip(old, self.formal):
            self.assertEqual((a["task_id"], a["target_bug_id"], a["condition"], a["repeat_index"], a["planned_execution_order"], a["context_document_ids"]), (b["task_id"], b["target_bug_id"], b["condition"], b["repeat_index"], b["planned_execution_order"], b["context_document_ids"]))
            self.assertNotEqual(a["prompt_sha256"], b["prompt_sha256"])
            self.assertEqual(b["execution_status"], "VERSIONED_V2_PLANNED_NOT_AUTHORIZED")

    def test_fail_closed_payloads(self):
        for payload in self.payloads:
            self.assertEqual(ADAPTER.validate_payload(payload), [])
        changed = json.loads(json.dumps(next(x for x in self.payloads if x["condition"] == "B")))
        changed["context_documents"][0]["doc_id"] = "wrong/project#1"
        self.assertIn("approved context manifest mismatch", ADAPTER.validate_payload(changed))
        changed = dict(self.payloads[0]); changed["prompt_sha256"] = "0" * 64
        self.assertTrue(ADAPTER.validate_payload(changed))

    def test_synthetic_isolation_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_file = ADAPTER.export_payload(self.payloads[0], Path(tmp))
            files = sorted(x.name for x in task_file.parent.iterdir())
            self.assertEqual(files, ["OUTPUT_SCHEMA.json", "PROMPT.txt", "task.json"])
            self.assertTrue(all(not x.is_symlink() for x in task_file.parent.iterdir()))
            self.assertFalse(any("gold" in x.name.casefold() or "rubric" in x.name.casefold() for x in task_file.parent.iterdir()))

    def test_structural_and_semantic_classification(self):
        bug = "synthetic/project#1"
        raw = json.dumps(valid_response(bug)).encode()
        self.assertEqual(EVAL.classify_attempt(invocation_started=True, invocation_completed=True, raw=raw, expected_bug_id=bug)["status"], "STRUCTURALLY_VALID")
        empty_trigger = valid_response(bug); empty_trigger["scenarios"][0]["trigger_conditions"] = []
        self.assertEqual(EVAL.classify_attempt(invocation_started=True, invocation_completed=True, raw=json.dumps(empty_trigger).encode(), expected_bug_id=bug)["status"], "SEMANTICALLY_INCOMPLETE")
        bad_step = valid_response(bug); bad_step["scenarios"][0]["ordered_execution_steps"][0]["step"] = 2
        self.assertEqual(EVAL.classify_attempt(invocation_started=True, invocation_completed=True, raw=json.dumps(bad_step).encode(), expected_bug_id=bug)["status"], "SCHEMA_FAILURE")
        whitespace = valid_response(bug); whitespace["scenarios"][0]["test_oracle"] = "  "
        self.assertEqual(EVAL.classify_attempt(invocation_started=True, invocation_completed=True, raw=json.dumps(whitespace).encode(), expected_bug_id=bug)["status"], "SCHEMA_FAILURE")

    def test_missing_output_vs_infrastructure(self):
        model_failure = EVAL.classify_attempt(invocation_started=True, invocation_completed=True, raw=b"", expected_bug_id="x#1")
        self.assertEqual((model_failure["failure_class"], model_failure["retry_eligible"]), ("MODEL_GENERATION_FAILURE", False))
        infra = EVAL.classify_attempt(invocation_started=False, invocation_completed=False, raw=None, expected_bug_id="x#1", process_error="auth")
        self.assertEqual((infra["status"], infra["retry_eligible"]), ("INFRASTRUCTURE_FAILURE", True))

    def test_tcc_best_of_three_and_no_union(self):
        reviews = [
            {"scenario_id": "S1", "path_id": "P1", "coherent": True, "covered_required_condition_ids": ["T1A"]},
            {"scenario_id": "S2", "path_id": "P1", "coherent": True, "covered_required_condition_ids": ["T2", "T3"]},
            {"scenario_id": "S3", "path_id": "P1", "coherent": True, "covered_required_condition_ids": []},
        ]
        result = EVAL.select_best_tcc(self.eligibility, "CorsixTH/CorsixTH#1799", reviews)
        self.assertEqual((result["scenario_id"], result["tcc_numerator"], result["tcc_denominator"]), ("S2", 2, 3))

    def test_tcc_tie_earliest_scenario(self):
        reviews = [
            {"scenario_id": "S2", "path_id": "P1", "coherent": True, "covered_required_condition_ids": ["T2"]},
            {"scenario_id": "S1", "path_id": "P1", "coherent": True, "covered_required_condition_ids": ["T2"]},
        ]
        self.assertEqual(EVAL.select_best_tcc(self.eligibility, "flame-engine/flame#1003", reviews)["scenario_id"], "S1")

    def test_cspc_path_results_retained(self):
        reviews = [
            {"path_id": "P1", "matching_scenario_id": "S2", "required_setup_covered": True, "trigger_coverage_complete": True, "execution_order_valid": True, "observable_result_covered": True},
            {"path_id": "P2", "matching_scenario_id": "S3", "required_setup_covered": True, "trigger_coverage_complete": False, "execution_order_valid": True, "observable_result_covered": True},
        ]
        result = EVAL.score_cspc_paths(self.eligibility, "rptools/maptool#2416", reviews)
        self.assertEqual(len(result["path_results"]), 2)
        self.assertEqual([x["cspc"] for x in result["path_results"]], [1, 0])
        self.assertEqual(result["bug_response_cspc"], 1)

    def test_oracle_scope_and_generation_failure_scores(self):
        pending = EVAL.score_strict_oracle(self.eligibility, "tmodloader/tmodloader#2644", "O1", "S1", "correct")
        self.assertEqual((pending["status"], pending["score"]), ("EXCLUDED_PENDING", None))
        included = EVAL.score_strict_oracle(self.eligibility, "rptools/maptool#2416", "O2", "S2", "partial")
        self.assertEqual(included["score"], 0)
        failure = EVAL.generation_failure_scores(self.eligibility, "rptools/maptool#2416")
        self.assertEqual([x["score"] for x in failure["strict_oracle"]], [0, 0])

    def test_raw_response_import_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = ADAPTER.export_payload(self.payloads[0], root / "workspace")
            response = root / "response.json"; response.write_text("first")
            metadata = {"attempt_index": 1, "process_status": "completed"}
            ADAPTER.import_attempt(task, response, root / "imports", metadata)
            response.write_text("changed")
            with self.assertRaises(FileExistsError):
                ADAPTER.import_attempt(task, response, root / "imports", metadata)


if __name__ == "__main__":
    unittest.main()
