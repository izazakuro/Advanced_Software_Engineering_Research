from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]
RESEARCH = HERE.parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


ADAPTER = load_module("phase3_adapter", HERE / "phase3_codex_task_adapter.py")
EVALUATOR = load_module("phase3_evaluator", HERE / "phase3_offline_evaluator.py")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class Phase3OfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.formal = jsonl(HERE / "phase3_formal_162_task_schedule.jsonl")
        cls.payloads = jsonl(HERE / "phase3_formal_162_task_payloads_NOT_AUTHORIZED.jsonl")
        cls.pilot = jsonl(HERE / "phase3_pilot_18_task_schedule.jsonl")
        cls.integrity = json.loads((HERE / "phase3_approved_context_integrity.json").read_text())
        cls.eligibility = json.loads((RESEARCH / "outputs/gold_v1_frozen/gold_v1_metric_eligibility.json").read_text())

    def test_authoritative_context_integrity(self):
        self.assertEqual(self.integrity["status"], "PASS")
        self.assertEqual(self.integrity["targets"], 18)
        self.assertEqual(self.integrity["approved_total_assignments"], 108)
        self.assertEqual(self.integrity["approved_B_assignments"], 54)
        self.assertEqual(self.integrity["approved_C_assignments"], 54)
        self.assertTrue(self.integrity["selection_preserved"])
        self.assertEqual(self.integrity["errors"], [])

    def test_formal_schedule_is_balanced(self):
        self.assertEqual(len(self.formal), 162)
        self.assertEqual(len({x["task_id"] for x in self.formal}), 162)
        self.assertEqual([x["planned_execution_order"] for x in self.formal], list(range(1, 163)))
        bugs = {x["target_bug_id"] for x in self.formal}
        self.assertEqual(len(bugs), 18)
        for bug in bugs:
            rows = [x for x in self.formal if x["target_bug_id"] == bug]
            self.assertEqual(len(rows), 9)
            for condition in "ABC":
                self.assertEqual(sum(x["condition"] == condition for x in rows), 3)
            for repeat in (1, 2, 3):
                self.assertEqual({x["condition"] for x in rows if x["repeat_index"] == repeat}, set("ABC"))

    def test_pilot_schedule_is_six_by_three(self):
        self.assertEqual(len(self.pilot), 18)
        self.assertEqual(len({x["target_bug_id"] for x in self.pilot}), 6)
        for bug in {x["target_bug_id"] for x in self.pilot}:
            self.assertEqual({x["condition"] for x in self.pilot if x["target_bug_id"] == bug}, set("ABC"))
        self.assertTrue(all(x["execution_status"] == "ENGINEERING_PILOT_NOT_AUTHORIZED" for x in self.pilot))

    def test_context_counts_and_no_reselection(self):
        for row in self.formal:
            expected = 0 if row["condition"] == "A" else 3
            self.assertEqual(len(row["context_document_ids"]), expected)
            self.assertEqual(len(row["context_content_sha256"]), expected)
        source = {x["target_bug_id"]: x for x in self.integrity["records"]}
        for row in self.formal:
            expected = [] if row["condition"] == "A" else [x["doc_id"] for x in source[row["target_bug_id"]][row["condition"]]]
            self.assertEqual(row["context_document_ids"], expected)

    def test_payload_boundary_and_hashes(self):
        self.assertEqual(len(self.payloads), 162)
        for payload in self.payloads:
            self.assertEqual(ADAPTER.validate_task_payload(payload), [])
            prompt = payload["rendered_prompt"]
            for marker in ("{{BUG_ID}}", "{{TARGET_TITLE}}", "{{TARGET_BODY}}", "{{ADDITIONAL_CONTEXT_OR_NONE}}"):
                self.assertNotIn(marker, prompt)
            self.assertNotIn("gold_reference", prompt.casefold())
            self.assertNotIn("repair_commit", prompt.casefold())
            self.assertNotIn("repair_diff", prompt.casefold())
            if payload["condition"] == "A":
                self.assertIn("Additional historical Issue context\nNone.", prompt)

    def test_adapter_fails_closed(self):
        payload = dict(self.payloads[0])
        payload["prompt_sha256"] = "0" * 64
        self.assertIn("rendered prompt hash mismatch", ADAPTER.validate_task_payload(payload))
        payload = dict(self.payloads[0])
        payload["gold_reference"] = "forbidden"
        self.assertTrue(ADAPTER.validate_task_payload(payload))
        payload = json.loads(json.dumps(next(x for x in self.payloads if x["condition"] == "B")))
        payload["context_documents"][0]["doc_id"] = "unapproved/project#1"
        payload["rendered_prompt"] += " altered"
        payload["prompt_sha256"] = ADAPTER.sha256_bytes(payload["rendered_prompt"].encode())
        errors = ADAPTER.validate_task_payload(payload)
        self.assertIn("context manifest mismatch", errors)
        self.assertIn("task payload differs from the approved offline schedule", errors)

    def test_immutable_export_and_import(self):
        payload = self.payloads[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = ADAPTER.export_task(payload, root / "tasks")
            ADAPTER.export_task(payload, root / "tasks")
            response = root / "response.json"
            response.write_text(json.dumps({"bug_id": payload["bug_id"], "scenarios": []}))
            record = ADAPTER.import_response(task_file, response, root / "imports")
            self.assertEqual(record["model_identifier"], "unavailable")
            ADAPTER.import_response(task_file, response, root / "imports")
            response.write_text("different")
            with self.assertRaises(FileExistsError):
                ADAPTER.import_response(task_file, response, root / "imports")

    def test_mock_structural_statuses(self):
        fixtures = jsonl(HERE / "phase3_mock_fixtures.jsonl")
        for fixture in fixtures:
            if "expected_validation" not in fixture:
                continue
            raw = fixture.get("raw_response")
            result = EVALUATOR.validate_raw_response(None if raw is None else raw.encode(), "synthetic/project#1")
            self.assertEqual(result["status"], fixture["expected_validation"], fixture["fixture_id"])

    def test_duplicate_response_detection(self):
        raw = next(x["raw_response"] for x in jsonl(HERE / "phase3_mock_fixtures.jsonl") if x["fixture_id"] == "full_tcc").encode()
        seen = set()
        self.assertEqual(EVALUATOR.validate_raw_response(raw, "synthetic/project#1", seen)["status"], "STRUCTURALLY_VALID")
        self.assertEqual(EVALUATOR.validate_raw_response(raw, "synthetic/project#1", seen)["status"], "DUPLICATE_RESPONSE")

    def test_frozen_metric_denominators(self):
        scopes = self.eligibility["frozen_metric_scopes"]
        self.assertEqual(scopes["tcc_bugs"], 18)
        self.assertEqual(scopes["tcc_paths"], 19)
        self.assertEqual(scopes["tcc_required_condition_denominator"], 24)
        self.assertEqual(scopes["cspc_bugs"], 11)
        self.assertEqual(scopes["cspc_paths"], 12)
        self.assertEqual(scopes["strict_oracle_entries"], 10)
        self.assertEqual(scopes["strict_oracle_bugs"], 9)
        self.assertEqual(scopes["strict_pending_excluded"], 2)
        self.assertEqual(scopes["strict_ineligible_incomplete_excluded"], 7)
        self.assertEqual(scopes["scenario_adequacy_bugs"], 18)
        self.assertEqual(set(self.eligibility["pending_strict_oracles"]), {"tmodloader/tmodloader#2644::O1", "vcmi/vcmi#5462::O1"})

    def test_scenario_selection_uses_tcc_only_then_earliest(self):
        judgments = [
            {"scenario_id": "S2", "metric": "tcc", "numerator": 1, "denominator": 1},
            {"scenario_id": "S1", "metric": "tcc", "numerator": 2, "denominator": 2},
            {"scenario_id": "S3", "metric": "tcc", "numerator": 0, "denominator": 1},
            {"scenario_id": "S2", "metric": "strict_oracle", "numerator": 0, "denominator": 1},
            {"scenario_id": "S1", "metric": "strict_oracle", "numerator": 1, "denominator": 1},
        ]
        self.assertEqual(EVALUATOR.select_scenario(judgments), "S1")

    def test_metric_interfaces_preserve_frozen_eligibility(self):
        tcc = EVALUATOR.evaluate_tcc(self.eligibility, "CorsixTH/CorsixTH#1799", "P1", ["T1A", "T2"])
        self.assertEqual((tcc["numerator"], tcc["denominator"]), (2, 3))
        cspc = EVALUATOR.evaluate_cspc(self.eligibility, "CorsixTH/CorsixTH#2881", "P1", True)
        self.assertEqual(cspc["status"], "NA_FROZEN_INELIGIBLE")
        pending = EVALUATOR.evaluate_strict_oracle(self.eligibility, "tmodloader/tmodloader#2644", "O1", True)
        self.assertEqual(pending["status"], "EXCLUDED_PENDING")
        included = EVALUATOR.evaluate_strict_oracle(self.eligibility, "rptools/maptool#2416", "O2", True)
        self.assertEqual(included["score"], 1.0)
        sa = EVALUATOR.evaluate_scenario_adequacy(self.eligibility, "vcmi/vcmi#5462", False)
        self.assertEqual(sa["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
