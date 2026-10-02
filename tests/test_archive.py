"""元青花文物邮票发行档案的领域规则测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.stamp_archive import (
    Approval,
    Archive,
    Artifact,
    ArtifactOrigin,
    Batch,
    Design,
    Event,
    HoldReason,
    Image,
    IssuanceHalted,
    License,
    Material,
    MaterialKind,
    Museum,
    PermissionDenied,
    Role,
    ValidationError,
    actor_role,
)

MANAGER = "manager:stamp-office:发行岗"
MUSEUM_A = "museum:m-a:保管岗"
MUSEUM_B = "museum:m-b:保管岗"
DESIGNER = "designer:studio:主设岗"
PRINTER = "printer:press:跟单岗"


class ArchiveFixture:
    """构造一条已经走完建档—授权—设计—审批—冻结—开印的最小可用档案。"""

    def __init__(self, *, today: str = "2025-06-01T00:00:00Z") -> None:
        self.ticks = iter(self._timestamps())
        self.archive = Archive(
            clock=lambda: next(self.ticks), today=lambda: today,
        )
        a = self.archive
        a.register_museum(MANAGER, Museum("m-a", "甲馆"))
        a.register_museum(MANAGER, Museum("m-b", "乙馆"))
        a.register_artifact(MANAGER, Artifact(
            "art-1", "m-a", "青花龙纹罐", "元代",
            ArtifactOrigin.EXCAVATED, "南方某城出土", "三爪龙戏珠",
            "面向公众的器物说明", "库架 A-07 机密",
        ))
        a.add_image(MUSEUM_A, Image("img-1", "art-1", 1, "2025-01-01", "初版底图"))
        a.grant_license(MANAGER, License(
            "lic-1", "img-1", "m-a", "stamp,first_day_cover",
            "2025-01-01", "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        ))
        a.create_design(DESIGNER, Design(
            "des-1", "art-1", "龙罐", 1, "img-1", "lic-1",
            DESIGNER, "三爪龙戏珠",
        ))
        a.approve_design(MANAGER, Approval(
            "ap-1", "des-1", 1, "2025-02-01T09:00:00Z", MANAGER,
            "approved", "通过", ("img-1", "lic-1"),
        ))
        a.create_material(MANAGER, "mat-1", MaterialKind.STAMP, "des-1")
        a.freeze_material(MANAGER, "mat-1", "frz-1")
        a.open_batch(MANAGER, "bat-1", "mat-1", 1000, "影写版六色")

    @staticmethod
    def _timestamps():
        month = 1
        day = 1
        while True:
            yield f"2025-{month:02d}-{day:02d}T09:00:00Z"
            day += 1
            if day > 28:
                day = 1
                month += 1


def fresh(**kwargs) -> ArchiveFixture:
    return ArchiveFixture(**kwargs)


class PermissionTest(unittest.TestCase):
    def test_only_manager_registers_museum(self) -> None:
        fx = fresh()
        with self.assertRaises(PermissionDenied):
            fx.archive.register_museum(MUSEUM_A, Museum("m-c", "丙馆"))

    def test_museum_only_sees_own_collection(self) -> None:
        fx = fresh()
        fx.archive.register_artifact(MANAGER, Artifact(
            "art-2", "m-b", "凤纹瓶", "元代",
            ArtifactOrigin.HANDED_DOWN, "传世", "凤纹", "公开说明",
        ))
        view_a = fx.archive.museum_view(MUSEUM_A)
        self.assertEqual([a["id"] for a in view_a["artifacts"]], ["art-1"])
        self.assertIn("internal_note", view_a["artifacts"][0])
        with self.assertRaises(PermissionDenied):
            fx.archive.register_artifact(MUSEUM_B, Artifact(
                "art-x", "m-a", "越权器", "元代",
                ArtifactOrigin.HANDED_DOWN, "传世", "纹", "公开",
            ))
        with self.assertRaises(PermissionDenied):
            fx.archive.correct_artifact(MUSEUM_B, "art-1", 1, {"ornament": "改"})

    def test_museum_cannot_view_other_house(self) -> None:
        fx = fresh()
        with self.assertRaises(PermissionDenied):
            fx.archive.museum_view("museum:m-unknown:岗")

    def test_public_view_hides_internal_notes_and_holds(self) -> None:
        fx = fresh()
        view = fx.archive.public_view()
        artifact = next(a for a in view["artifacts"] if a["id"] == "art-1")
        self.assertNotIn("internal_note", artifact)
        self.assertEqual(artifact["ornament"], "三爪龙戏珠")
        self.assertTrue(all("frozen" not in s for s in view["stamps"]))
        self.assertTrue(
            all(actor_role(e.actor) is not Role.PUBLIC for e in fx.archive.events)
        )

    def test_designer_only_sees_approved_and_licensed(self) -> None:
        fx = fresh()
        fx.archive.register_artifact(MANAGER, Artifact(
            "art-2", "m-a", "莲纹盘", "元代",
            ArtifactOrigin.HANDED_DOWN, "传世", "莲纹", "公开",
        ))
        fx.archive.add_image(MUSEUM_A, Image("img-2", "art-2", 1, "2025-01-01", "图"))
        fx.archive.grant_license(MANAGER, License(
            "lic-2", "img-2", "m-a", "stamp",
            "2025-01-01", "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        ))
        fx.archive.create_design(DESIGNER, Design(
            "des-2", "art-2", "莲盘", 1, "img-2", "lic-2", DESIGNER, "莲纹",
        ))
        view = fx.archive.designer_view(DESIGNER)
        ids = {d["design_id"] for d in view["usable_designs"]}
        self.assertEqual(ids, {"des-1"})  # des-2 未审批，不可用

    def test_designer_cannot_freeze_or_approve(self) -> None:
        fx = fresh()
        with self.assertRaises(PermissionDenied):
            fx.archive.approve_design(DESIGNER, Approval(
                "ap-x", "des-1", 1, "t", DESIGNER, "approved", "x", (),
            ))
        with self.assertRaises(PermissionDenied):
            fx.archive.freeze_material(DESIGNER, "mat-x", "frz-x")


class EvidenceAndFreezeTest(unittest.TestCase):
    def test_freeze_requires_approval(self) -> None:
        fx = fresh()
        fx.archive.create_design(DESIGNER, Design(
            "des-2", "art-1", "未批稿", 1, "img-1", "lic-1", DESIGNER, "纹",
        ))
        fx.archive.create_material(MANAGER, "mat-x", MaterialKind.STAMP, "des-2")
        with self.assertRaisesRegex(ValidationError, "未获批准"):
            fx.archive.freeze_material(MANAGER, "mat-x", "frz-x")

    def test_freeze_is_one_shot(self) -> None:
        fx = fresh()
        with self.assertRaisesRegex(ValidationError, "已冻结"):
            fx.archive.freeze_material(MANAGER, "mat-1", "frz-2")

    def test_artifact_correction_invalidates_old_approval_for_new_freeze(self) -> None:
        fx = fresh()
        fx.archive.correct_artifact(MUSEUM_A, "art-1", 1, {"ornament": "三爪龙戏珠并云气"})
        fx.archive.create_material(
            MANAGER, "mat-2", MaterialKind.FIRST_DAY_COVER, "des-1",
        )
        with self.assertRaisesRegex(ValidationError, "版本已变化"):
            fx.archive.freeze_material(MANAGER, "mat-2", "frz-2")

    def test_stale_freeze_cannot_open_new_batch(self) -> None:
        fx = fresh()
        fx.archive.correct_image(MANAGER, Image(
            "img-2", "art-1", 2, "2025-05-01", "换版", "img-1",
        ))
        fx.archive.grant_license(MANAGER, License(
            "lic-2", "img-2", "m-a", "stamp",
            "2025-05-01", "2025-05-01T00:00:00Z", "2026-05-01T00:00:00Z",
        ))
        fx.archive.revise_design(DESIGNER, "des-1", "img-2", "lic-2")
        fx.archive.approve_design(MANAGER, Approval(
            "ap-2", "des-1", 2, "2025-05-02T09:00:00Z", MANAGER,
            "approved", "采用新图", ("img-2",),
        ))
        # 旧物料冻结仍停留在 img-1，新批次必须换版。
        with self.assertRaisesRegex(ValidationError, "冻结依据已过时"):
            fx.archive.open_batch(MANAGER, "bat-old", "mat-1", 10, "影写版")

    def test_reissue_chain_preserves_old_basis(self) -> None:
        fx = fresh()
        fx.archive.correct_image(MANAGER, Image(
            "img-2", "art-1", 2, "2025-05-01", "换版", "img-1",
        ))
        fx.archive.grant_license(MANAGER, License(
            "lic-2", "img-2", "m-a", "stamp",
            "2025-05-01", "2025-05-01T00:00:00Z", "2026-05-01T00:00:00Z",
        ))
        fx.archive.revise_design(DESIGNER, "des-1", "img-2", "lic-2")
        fx.archive.approve_design(MANAGER, Approval(
            "ap-2", "des-1", 2, "2025-05-02T09:00:00Z", MANAGER,
            "approved", "新图", ("img-2",),
        ))
        fx.archive.create_material(
            MANAGER, "mat-2", MaterialKind.STAMP, "des-1",
            series="s-1", reissue_of="mat-1",
        )
        fx.archive.freeze_material(MANAGER, "mat-2", "frz-2")
        fx.archive.open_batch(MANAGER, "bat-2", "mat-2", 500, "影写版")
        old = fx.archive.trace_stamp("mat-1")
        new = fx.archive.trace_stamp("mat-2")
        self.assertEqual(old["frozen"]["image_id"], "img-1")
        self.assertEqual(new["frozen"]["image_id"], "img-2")
        self.assertEqual(new["reissue_of"], "mat-1")


class ApprovalChainTest(unittest.TestCase):
    def test_trace_stamp_lists_every_approval_with_evidence(self) -> None:
        fx = fresh()
        fx.archive.complete_batch(PRINTER, "bat-1")
        trace = fx.archive.trace_stamp("mat-1")
        self.assertEqual(len(trace["approvals"]), 1)
        approval = trace["approvals"][0]
        self.assertEqual(approval["evidence_versions"]["artifact"], 1)
        self.assertEqual(approval["evidence_versions"]["image"], 1)
        self.assertEqual(trace["frozen"]["approval_digests"], [
            fx.archive._hash(
                fx.archive.state.designs["des-1"].approvals[0].__dict__
            )
        ])

    def test_approval_revision_must_advance(self) -> None:
        fx = fresh()
        with self.assertRaisesRegex(ValidationError, "修订版本"):
            fx.archive.approve_design(MANAGER, Approval(
                "ap-x", "des-1", 1, "2025-03-01T09:00:00Z", MANAGER,
                "approved", "重复批准", (),
            ))

    def test_rejected_design_does_not_block_later_approval(self) -> None:
        fx = fresh()
        fx.archive.revise_design(DESIGNER, "des-1", "img-1", "lic-1")
        fx.archive.approve_design(MANAGER, Approval(
            "ap-2r", "des-1", 2, "2025-03-01T09:00:00Z", MANAGER,
            "rejected", "版式需调整", (),
        ))
        fx.archive.revise_design(DESIGNER, "des-1", "img-1", "lic-1")
        fx.archive.approve_design(MANAGER, Approval(
            "ap-3", "des-1", 3, "2025-03-05T09:00:00Z", MANAGER,
            "approved", "修订后通过", (),
        ))
        decisions = [
            a.decision for a in
            fx.archive.state.designs["des-1"].approvals
        ]
        self.assertEqual(decisions, ["approved", "rejected", "approved"])

    def test_approval_blocked_when_license_expired(self) -> None:
        fx = fresh()
        fx.archive._today = lambda: "2026-06-01T00:00:00Z"
        fx.archive.revise_design(DESIGNER, "des-1", "img-1", "lic-1")
        with self.assertRaises(IssuanceHalted) as caught:
            fx.archive.approve_design(MANAGER, Approval(
                "ap-2", "des-1", 2, "2026-06-02T09:00:00Z", MANAGER,
                "approved", "试图在授权失效后批准", (),
            ))
        self.assertEqual(caught.exception.holds[0].reason, HoldReason.LICENSE_EXPIRY)


class HoldFlowTest(unittest.TestCase):
    def test_image_correction_halts_new_batch_until_reapproved(self) -> None:
        fx = fresh()
        fx.archive.correct_image(MANAGER, Image(
            "img-2", "art-1", 2, "2025-05-01", "更正", "img-1",
        ))
        with self.assertRaises(IssuanceHalted) as caught:
            fx.archive.open_batch(MANAGER, "bat-x", "mat-1", 10, "影写版")
        self.assertEqual(caught.exception.holds[0].reason, HoldReason.IMAGE_CORRECTION)
        # 已开印批次不受影响，仍可按原依据完成。
        fx.archive.complete_batch(PRINTER, "bat-1")

    def test_license_expiry_then_renewal_reopens(self) -> None:
        fx = fresh()
        fx.archive.expire_license(MANAGER, "lic-1")
        with self.assertRaises(IssuanceHalted):
            fx.archive.open_batch(MANAGER, "bat-x", "mat-1", 10, "影写版")
        fx.archive.renew_license(
            MANAGER, "lic-1", "2025-07-01T00:00:00Z", "2027-07-01T00:00:00Z",
        )
        fx.archive.open_batch(MANAGER, "bat-2", "mat-1", 100, "影写版")
        self.assertEqual(fx.archive.state.batches["bat-2"].quantity, 100)

    def test_batch_anomaly_blocks_same_material_only(self) -> None:
        fx = fresh()
        fx.archive.create_material(
            MANAGER, "mat-2", MaterialKind.BROCHURE, "des-1",
        )
        fx.archive.freeze_material(MANAGER, "mat-2", "frz-2")
        fx.archive.open_batch(MANAGER, "bat-other", "mat-2", 10, "胶印")
        fx.archive.report_batch_anomaly(
            PRINTER, "bat-1", "色差超限",
        )
        with self.assertRaises(IssuanceHalted):
            fx.archive.open_batch(MANAGER, "bat-1b", "mat-1", 10, "影写版")
        # 不同物料不受牵连。
        fx.archive.complete_batch(PRINTER, "bat-other")

    def test_conflicting_fact_forces_review_and_halts(self) -> None:
        fx = fresh()
        fx.archive.submit_conflicting_fact(
            MUSEUM_A, "rev-1", "art-1", "出土时间两处记载不一", ["档案甲", "档案乙"],
        )
        self.assertEqual(fx.archive.state.reviews["rev-1"].status.value, "open")
        with self.assertRaises(IssuanceHalted):
            fx.archive.open_batch(MANAGER, "bat-x", "mat-1", 10, "影写版")
        fx.archive.resolve_review(MANAGER, "rev-1", "以馆方正式档案为准")
        fx.archive.open_batch(MANAGER, "bat-2", "mat-1", 10, "影写版")

    def test_conflicting_fact_never_overwrites_existing_data(self) -> None:
        fx = fresh()
        fx.archive.submit_conflicting_fact(
            MUSEUM_A, "rev-1", "art-1", "纹饰名称异说", ["甲", "乙"],
        )
        self.assertEqual(
            fx.archive.state.artifacts["art-1"].ornament, "三爪龙戏珠"
        )


class InventoryTest(unittest.TestCase):
    def test_duplicate_receipt_does_not_deduct_twice(self) -> None:
        fx = fresh()
        first = fx.archive.receive_receipt(PRINTER, "bat-1", "rc-1", 300)
        duplicate = fx.archive.receive_receipt(PRINTER, "bat-1", "rc-1", 300)
        self.assertEqual(first.action, "batch.receipt_received")
        self.assertEqual(duplicate.action, "batch.receipt_duplicate")
        batch = fx.archive.state.batches["bat-1"]
        self.assertEqual(batch.issued, 300)
        self.assertEqual(batch.remaining, 700)
        self.assertEqual(batch.receipts, frozenset({"rc-1"}))

    def test_receipt_cannot_exceed_remaining(self) -> None:
        fx = fresh()
        with self.assertRaisesRegex(ValidationError, "库存守恒"):
            fx.archive.receive_receipt(PRINTER, "bat-1", "rc-big", 1001)

    def test_different_batches_with_same_receipt_code_both_deduct(self) -> None:
        fx = fresh()
        fx.archive.open_batch(MANAGER, "bat-2", "mat-1", 500, "影写版")
        fx.archive.receive_receipt(PRINTER, "bat-1", "rc-x", 10)
        fx.archive.receive_receipt(PRINTER, "bat-2", "rc-x", 20)
        self.assertEqual(fx.archive.state.batches["bat-1"].issued, 10)
        self.assertEqual(fx.archive.state.batches["bat-2"].issued, 20)


class AppendOnlyAndPersistenceTest(unittest.TestCase):
    def test_events_are_sequential_and_replayable(self) -> None:
        fx = fresh()
        for index, event in enumerate(fx.archive.events, start=1):
            self.assertEqual(event.seq, index)
        replayed = Archive(fx.archive.events)
        self.assertEqual(
            [(e.action, e.payload) for e in replayed.events],
            [(e.action, e.payload) for e in fx.archive.events],
        )
        self.assertEqual(
            replayed.state.artifacts["art-1"].ornament,
            fx.archive.state.artifacts["art-1"].ornament,
        )

    def test_save_and_load_round_trip_with_digest_check(self) -> None:
        fx = fresh()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            fx.archive.save(path)
            loaded = Archive.load(path)
            self.assertEqual(len(loaded.events), len(fx.archive.events))
            for original, restored in zip(fx.archive.events, loaded.events):
                self.assertEqual(original.digest(), restored.digest())

    def test_tampered_payload_is_rejected_on_load(self) -> None:
        fx = fresh()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            fx.archive.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["events"][2]["payload"]["note"] = "被篡改"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "摘要不一致"):
                Archive.load(path)

    def test_gap_in_sequence_is_rejected(self) -> None:
        bad = Event(
            seq=99, at="2025-01-01T00:00:00Z", actor=MANAGER,
            action="museum.registered", payload={"id": "x", "name": "X"},
        )
        with self.assertRaisesRegex(ValidationError, "序号"):
            Archive([bad])


class ArtifactCorrectionTest(unittest.TestCase):
    def test_correction_requires_expected_version(self) -> None:
        fx = fresh()
        fx.archive.correct_artifact(MUSEUM_A, "art-1", 1, {"ornament": "云气龙"})
        with self.assertRaisesRegex(ValidationError, "版本已变化"):
            fx.archive.correct_artifact(MUSEUM_A, "art-1", 1, {"ornament": "再改"})
        self.assertEqual(fx.archive.state.artifact_versions["art-1"], 2)

    def test_correction_rejects_reassignment_to_other_museum(self) -> None:
        fx = fresh()
        with self.assertRaises(ValidationError):
            fx.archive.correct_artifact(
                MUSEUM_A, "art-1", 1, {"museum_id": "m-b"},
            )


class FixtureFileTest(unittest.TestCase):
    """对脚本生成的示例档案做端到端校验。"""

    PATH = Path("fixtures/archive.json")

    def setUp(self) -> None:
        self.archive = Archive.load(
            self.PATH, today=lambda: "2025-09-01T00:00:00Z",
        )

    def test_counts(self) -> None:
        state = self.archive.state
        self.assertEqual(len(state.museums), 5)
        self.assertEqual(len(state.artifacts), 6)
        kinds = [m.kind for m in state.materials.values()]
        self.assertIn(MaterialKind.STAMP, kinds)
        self.assertIn(MaterialKind.SOUVENIR_SHEET, kinds)
        self.assertIn(MaterialKind.FIRST_DAY_COVER, kinds)
        self.assertIn(MaterialKind.BROCHURE, kinds)

    def test_public_view_has_no_internal_fields(self) -> None:
        view = self.archive.public_view()
        for artifact in view["artifacts"]:
            self.assertNotIn("internal_note", artifact)

    def test_every_material_trace_is_self_contained(self) -> None:
        for material in self.archive.state.materials.values():
            trace = self.archive.trace_stamp(material.id)
            self.assertTrue(trace["approvals"])
            self.assertIsNotNone(trace["frozen"])
            self.assertEqual(
                trace["frozen"]["artifact_version"] >= 1, True,
            )

    def test_inventory_conservation_on_first_batch(self) -> None:
        batch = self.archive.state.batches["batch-stamp1-01"]
        self.assertEqual(batch.issued + batch.remaining, batch.quantity)

    def test_all_holds_and_reviews_closed_at_fixture_end(self) -> None:
        self.assertFalse(
            [h for h in self.archive.state.holds if h.resolved_at is None]
        )
        self.assertFalse(
            [r for r in self.archive.state.reviews.values() if r.status.value == "open"]
        )


if __name__ == "__main__":
    unittest.main()
