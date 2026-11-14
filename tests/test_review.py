"""Synthetic record validation tests. These do not certify visual quality."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts" / "review.py"
spec = importlib.util.spec_from_file_location("review", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.master = self.root / "master.svg"
        self.master.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>TEST</text></svg>')
        self.view = self.root / "view.svg"
        self.view.write_bytes(self.master.read_bytes())
        self.lib = self.root / "history.json"
        self.lib.write_text(json.dumps([{"id": "E001", "error": "old case rotated",
                                        "correct": "180 degrees only in the old case"}]))
        self.job = {"job_id": "synthetic", "masters": [{"id": "master", "path": str(self.master)}],
                    "panels": [{"id": "lid", "master_id": "master", "baseline_status": "confirmed",
                                "baseline_evidence": "synthetic test specification",
                                "reading_direction": "left-to-right", "placement_rule": "panel center"}],
                    "views": [{"id": "top", "path": str(self.view), "panels": ["lid"],
                               "master_ids": ["master"]}], "protected": ["brand"],
                    "error_ids": ["E001"], "library_path": str(self.lib)}

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self):
        p = self.root / "job.json"
        p.write_text(json.dumps(self.job), encoding="utf-8")
        self.out = self.root / "result"
        return mod.prepare(p, self.out)

    def review(self):
        return mod.load_json(self.out / "review.json")

    def save(self, data):
        (self.out / "review.json").write_text(json.dumps(data), encoding="utf-8")

    def filled(self):
        data = self.review()
        for row in data["checks"]:
            row.update(status="pass", reviewer="synthetic-test-only",
                       reviewed_at="2026-11-13T00:00:00Z",
                       observation="Synthetic schema fixture; not a real visual inspection",
                       evidence=[{"path": str(self.view), "sha256": mod.digest(self.view)}])
        return data

    def test_starts_unverified(self):
        result = self.prepare()
        self.assertEqual(result["checks"], 7)
        report, code = mod.check(self.out / "review.json")
        self.assertEqual(code, 2)
        self.assertEqual(len(report["unverified_checks"]), 7)

    def test_complete_record_is_not_visual_verdict(self):
        self.prepare()
        self.save(self.filled())
        report, code = mod.check(self.out / "review.json")
        self.assertEqual(code, 0)
        self.assertEqual(report["visual_verdict"], "not_automatically_evaluated")
        self.assertFalse(report["production_approval"])

    def test_no_overwrite(self):
        self.prepare()
        before = (self.out / "review.json").read_bytes()
        with self.assertRaises(ValueError):
            mod.prepare(self.root / "job.json", self.out)
        self.assertEqual(before, (self.out / "review.json").read_bytes())

    def test_source_preserved(self):
        before = self.master.read_bytes(), self.view.read_bytes(), self.lib.read_bytes()
        self.prepare()
        self.assertEqual(before, (self.master.read_bytes(), self.view.read_bytes(), self.lib.read_bytes()))

    def test_missing_baseline_rejected(self):
        self.job["panels"][0]["master_id"] = "missing"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_unknown_baseline_never_complete(self):
        self.job["panels"][0]["baseline_status"] = "unknown"
        self.prepare()
        self.save(self.filled())
        self.assertEqual(mod.check(self.out / "review.json")[1], 2)

    def test_historical_rotation_not_applied(self):
        self.prepare()
        plan = mod.load_json(self.out / "plan.json")
        self.assertEqual(plan["panels"][0]["reading_direction"], "left-to-right")

    def test_missing_view_check_rejected(self):
        self.prepare()
        data = self.filled()
        data["checks"].pop()
        self.save(data)
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")

    def test_duplicate_check_rejected(self):
        self.prepare()
        data = self.filled()
        data["checks"].append(copy.deepcopy(data["checks"][0]))
        self.save(data)
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")

    def test_stale_source_rejected(self):
        self.prepare()
        self.view.write_text("changed")
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")

    def test_missing_evidence_rejected(self):
        self.prepare()
        data = self.filled()
        data["checks"][0]["evidence"] = []
        self.save(data)
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")

    def test_fail_prevents_complete(self):
        self.prepare()
        data = self.filled()
        data["checks"][0]["status"] = "fail"
        self.save(data)
        self.assertEqual(mod.check(self.out / "review.json")[1], 2)

    def test_core_gate_cannot_be_skipped(self):
        self.prepare()
        data = self.filled()
        data["checks"][0]["status"] = "na"
        self.save(data)
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")

    def test_missing_history_id_rejected(self):
        self.job["error_ids"] = ["E999"]
        with self.assertRaises(ValueError):
            self.prepare()

    def test_optional_missing_library_is_disclosed(self):
        self.job["error_ids"] = []
        self.job["library_path"] = str(self.root / "absent.json")
        result = self.prepare()
        self.assertTrue(result["warnings"])

    def test_network_rejected(self):
        self.job["masters"][0]["path"] = "https://example.com/image.png"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_multiview_coverage(self):
        self.job["views"].append({"id": "front", "path": str(self.view),
                                  "panels": ["lid"], "master_ids": ["master"]})
        self.assertEqual(self.prepare()["checks"], 13)

    def test_duplicate_view_rejected(self):
        self.job["views"].append(copy.deepcopy(self.job["views"][0]))
        with self.assertRaises(ValueError):
            self.prepare()

    def test_view_master_binding_required(self):
        self.job["masters"].append({"id": "other", "path": str(self.master)})
        self.job["views"][0]["master_ids"] = ["other"]
        with self.assertRaises(ValueError):
            self.prepare()

    def test_history_change_requires_reconcile(self):
        self.prepare()
        self.lib.write_text("[]")
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")

    def test_network_output_rejected(self):
        p = self.root / "job.json"
        p.write_text(json.dumps(self.job), encoding="utf-8")
        with self.assertRaises(ValueError):
            mod.prepare(p, Path("//server/share/review-output"))

    def test_network_spec_rejected(self):
        with self.assertRaises(ValueError):
            mod.prepare(Path("//server/share/job.json"), self.root / "result")

    def test_network_review_rejected(self):
        with self.assertRaises(ValueError):
            mod.check(Path("//server/share/review.json"))
    def test_plan_tamper_rejected(self):
        self.prepare()
        p = self.out / "plan.json"
        p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaises(ValueError):
            mod.check(self.out / "review.json")


if __name__ == "__main__":
    unittest.main()