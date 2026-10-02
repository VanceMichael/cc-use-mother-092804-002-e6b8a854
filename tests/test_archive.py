"""校验元青花文物主题发行档案的结构、业务规则与查询。"""

import json
import unittest
from pathlib import Path

from src.archive import (
    find_conflicts,
    load_archive,
    public_view,
    stock_after,
    trace_material,
    validate_archive,
)

AS_OF = "2026-10-02"
FIXTURE = Path("fixtures/archive.json")


def load_fixture() -> dict:
    return load_archive(FIXTURE)


class StructureTest(unittest.TestCase):
    def test_fixture_loads_and_has_no_violations(self) -> None:
        archive = load_fixture()
        self.assertEqual(validate_archive(archive, AS_OF), [])

    def test_wrong_domain_is_rejected(self) -> None:
        raw = FIXTURE.read_text(encoding="utf-8").replace("yuan-porcelain-stamp", "other-domain", 1)
        temporary = Path("fixtures/.invalid-archive.json")
        temporary.write_text(raw, encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "领域标识不一致"):
                load_archive(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def test_missing_collection_is_rejected(self) -> None:
        value = json.loads(FIXTURE.read_text(encoding="utf-8"))
        del value["holds"]
        temporary = Path("fixtures/.invalid-archive.json")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "档案字段不完整"):
                load_archive(temporary)
        finally:
            temporary.unlink(missing_ok=True)


class LicenseAndPauseTest(unittest.TestCase):
    def test_completed_batch_keeps_basis_after_license_expiry(self) -> None:
        archive = load_fixture()
        errors = validate_archive(archive, AS_OF)
        self.assertFalse(any("B6" in error for error in errors))
        batch = next(item for item in archive["batches"] if item["id"] == "B6")
        basis = {use["design_id"]: use["license_id"] for use in batch["basis"]["uses"]}
        self.assertEqual(basis["D2"], "L2")

    def test_pending_batch_with_expired_license_must_pause(self) -> None:
        archive = load_fixture()
        for batch in archive["batches"]:
            if batch["id"] == "B2":
                batch["status"] = "印制中"
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("B2" in error and "暂停" in error for error in errors))

    def test_expired_license_needs_hold_record(self) -> None:
        archive = load_fixture()
        archive["holds"] = [hold for hold in archive["holds"] if hold["id"] != "H1"]
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("B2" in error and "L2" in error for error in errors))

    def test_open_hold_must_pause_batch(self) -> None:
        archive = load_fixture()
        for batch in archive["batches"]:
            if batch["id"] == "B4":
                batch["status"] = "印制中"
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("H3" in error and "B4" in error for error in errors))

    def test_license_must_be_granted_by_holder(self) -> None:
        archive = load_fixture()
        for license_ in archive["licenses"]:
            if license_["id"] == "L2":
                license_["granted_by"] = "M1"
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("L2" in error and "收藏机构" in error for error in errors))

    def test_approval_requires_valid_license(self) -> None:
        archive = load_fixture()
        for design in archive["designs"]:
            if design["id"] == "D2":
                design["history"][0]["at"] = "2027-01-01"
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("D2" in error and "L2" in error for error in errors))


class FreezeAndScopeTest(unittest.TestCase):
    def test_completed_batch_requires_frozen_material(self) -> None:
        archive = load_fixture()
        for material in archive["materials"]:
            if material["id"] == "S1":
                material["frozen"] = False
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("S1" in error and "冻结" in error for error in errors))

    def test_material_kind_must_be_in_license_scope(self) -> None:
        archive = load_fixture()
        for material in archive["materials"]:
            if material["id"] == "P1":
                material["uses"].append("D6")
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("P1" in error and "范围" in error for error in errors))


class ReceiptTest(unittest.TestCase):
    def test_duplicate_receipt_deducts_once(self) -> None:
        archive = load_fixture()
        receipts = archive["receipts"] + [dict(archive["receipts"][0])]
        stock = stock_after({"S1": 1500}, archive, receipts)
        self.assertEqual(stock["S1"], 500)

    def test_receipts_cannot_exceed_batch_quantity(self) -> None:
        archive = load_fixture()
        archive["receipts"].append({"id": "R9", "batch_id": "B1", "quantity": 1})
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("B1" in error and "超量" in error for error in errors))


class ReviewTest(unittest.TestCase):
    def test_decoration_conflict_is_detected(self) -> None:
        archive = load_fixture()
        self.assertIn("decoration:D3", find_conflicts(archive))

    def test_conflict_must_enter_review(self) -> None:
        archive = load_fixture()
        archive["reviews"] = []
        errors = validate_archive(archive, AS_OF)
        self.assertTrue(any("decoration:D3" in error for error in errors))


class PublicViewTest(unittest.TestCase):
    def test_public_view_hides_internal_records(self) -> None:
        view = public_view(load_fixture())
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("internal_note", blob)
        self.assertNotIn("库藏编号", blob)
        self.assertNotIn("A6", blob)
        self.assertNotIn("Z1", blob)
        self.assertIn("青花缠枝牡丹纹梅瓶", blob)
        self.assertIn("甲博物馆", blob)


class TraceTest(unittest.TestCase):
    def test_trace_material_restores_evidence(self) -> None:
        trace = trace_material(load_fixture(), "S1")
        self.assertEqual(trace["artifacts"][0]["name"], "青花缠枝牡丹纹梅瓶")
        self.assertEqual(trace["institutions"][0]["name"], "甲博物馆")
        self.assertEqual(trace["licenses"][0]["id"], "L1")
        self.assertEqual(len(trace["designs"][0]["approvals"]), 1)
        self.assertEqual(trace["designs"][0]["approvals"][0]["decision"], "批准")
        self.assertEqual(trace["batches"][0]["id"], "B1")
        self.assertEqual(trace["batches"][0]["basis"]["uses"][0]["license_id"], "L1")

    def test_trace_unknown_material_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "未找到物料"):
            trace_material(load_fixture(), "XX")


if __name__ == "__main__":
    unittest.main()
