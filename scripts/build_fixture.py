"""构建示例发行档案 fixtures/archive.json。

时间线（均为虚构示例日期，机构与器物为常见馆藏品的示意性记录，
不代表真实档案数据，人物一律以岗位代称）：

1. 2025-01 五家机构登记六件器物，提供图像、授予使用权；
2. 2025-02 六套设计稿创建并审批；
3. 2025-03 五枚邮票、小型张、六枚首日封、宣传册冻结并开印首批；
4. 2025-04 南京市博物馆更正梅瓶图像：旧批次保留旧依据，换版重印；
5. 2025-07 一件器物授权到期：续期后解除暂停；
6. 2025-08 小型张批次报异常：隔离调查后解除；
7. 2025-08 伊朗馆一件器物来源记载冲突：进入复核，结论确认后解除。

用法：python3 scripts/build_fixture.py [输出路径]
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stamp_archive import (  # noqa: E402
    Approval,
    Archive,
    Artifact,
    ArtifactOrigin,
    Image,
    IssuanceHalted,
    License,
    Material,
    MaterialKind,
    Museum,
    Design,
    ValidationError,
)

MANAGER = "manager:stamp-office:发行项目岗"
DESIGNER = "designer:stamp-studio:主设岗"
PRINTER = "printer:stamp-press:印制跟单岗"


def museum_actor(museum_id: str) -> str:
    return f"museum:{museum_id}:藏品保管岗"


class SteppingClock:
    """每取一次时间戳前进一分钟，jump_to 可切换到业务阶段日期。"""

    def __init__(self, start: str) -> None:
        self._at = datetime.fromisoformat(start.replace("Z", "+00:00"))

    def jump_to(self, at: str) -> None:
        self._at = datetime.fromisoformat(at.replace("Z", "+00:00"))

    def now(self) -> str:
        value = self._at
        self._at = self._at + timedelta(minutes=1)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    def peek(self) -> str:
        """返回业务当前时刻而不推进时钟，供授权有效期判断。"""
        return self._at.strftime("%Y-%m-%dT%H:%M:%SZ")


MUSEUMS = [
    Museum("pala", "故宫博物院", "故宫博物院藏历代陶瓷向公众常设展出"),
    Museum("njmuseum", "南京市博物馆", "馆址位于南京朝天宫"),
    Museum("hbmuseum", "湖北省博物馆", "位于武汉东湖之滨"),
    Museum("gamuseum", "高安市博物馆", "藏有1980年元代窖藏出土元青花群"),
    Museum("iranmm", "伊朗国家博物馆", "藏有阿迪比尔陵寺旧藏中国瓷器"),
]

# id, 机构, 名称, 来源类型, 来源说明, 纹饰, 公开说明, 内部备注
ARTIFACTS = [
    (
        "art-guwan-peony-jar", "pala", "青花缠枝牡丹纹罐",
        ArtifactOrigin.HANDED_DOWN, "清宫旧藏，历代递藏脉络清晰（示例记载）",
        "腹部缠枝牡丹，肩饰海水与八宝",
        "元代景德镇窑青花大器，胎体厚重、发色浓艳。",
        "库区青瓷架B-12，例行提库需双人签字",
    ),
    (
        "art-xiaohe-meiping", "njmuseum", "青花萧何月下追韩信图梅瓶",
        ArtifactOrigin.EXCAVATED, "南京江宁将军山沐氏家族墓出土（示例记载）",
        "腹部绘萧何月下追韩信历史人物故事",
        "元青花人物故事名品，1959年入藏南京市博物馆。",
        "展厅独立恒温柜，外借须馆务会审批",
    ),
    (
        "art-siai-meiping", "hbmuseum", "青花四爱图梅瓶",
        ArtifactOrigin.EXCAVATED, "湖北钟祥明郢靖王墓出土（示例记载）",
        "肩部四组菱形开光，分绘王羲之爱兰等四爱图",
        "元代青花梅瓶代表，器形饱满、层次繁密。",
        "出土残片比对档案号 HB-2001-07",
    ),
    (
        "art-gaoyao-lidded", "gamuseum", "青花云龙纹带盖梅瓶",
        ArtifactOrigin.EXCAVATED, "1980年江西高安元代窖藏出土（示例记载）",
        "器身云龙纹，盖饰印花卷草",
        "窖藏出土元青花带盖梅瓶，盖内有墨书墨迹。",
        "盖与器身分开保管，提用须核对墨书编号",
    ),
    (
        "art-iran-phoenix-plate", "iranmm", "青花蓝地白花云肩凤纹菱花口大盘",
        ArtifactOrigin.HANDED_DOWN, "阿迪比尔陵寺旧藏，后入藏伊朗国家博物馆（示例记载）",
        "菱花口，云肩形开光内绘双凤，地子留白反侧",
        "元代外销青花大盘代表，适应伊斯兰地区围坐分食习惯。",
        "海外联展运输专用箱编号 IR-OV-03",
    ),
    (
        "art-iran-peony-jar", "iranmm", "青花缠枝牡丹云龙纹罐",
        ArtifactOrigin.HANDED_DOWN, "阿迪比尔陵寺旧藏，入藏登记存在异本（示例记载）",
        "下腹云龙，上腹缠枝牡丹",
        "元代青花大罐，与蓝地白花大盘同属陵寺旧藏群。",
        "旧登记卡与陵寺瓷目编号需再核",
    ),
]


def main(output: str) -> None:
    clock = SteppingClock("2025-01-06T09:00:00Z")
    archive = Archive(clock=clock.now, today=clock.peek)

    # --- 阶段一：机构与文物建档 ---------------------------------------
    clock.jump_to("2025-01-06T09:00:00Z")
    for museum in MUSEUMS:
        archive.register_museum(MANAGER, museum)

    clock.jump_to("2025-01-07T09:00:00Z")
    for (
        aid, mid, name, origin, origin_note, ornament, public_desc, internal,
    ) in ARTIFACTS:
        archive.register_artifact(MANAGER, Artifact(
            id=aid, museum_id=mid, name=name, era="元代",
            origin_type=origin, origin_note=origin_note,
            ornament=ornament, public_description=public_desc,
            internal_note=internal,
        ))

    clock.jump_to("2025-01-08T09:00:00Z")
    # 高安梅瓶授权先给较短有效期，用于演示到期—续期。
    license_until = {
        "art-gaoyao-lidded": "2025-06-30T23:59:59Z",
    }
    for aid, mid, *_ in ARTIFACTS:
        actor = museum_actor(mid)
        archive.add_image(actor, Image(
            id=f"img-{aid}-v1", artifact_id=aid, version=1,
            created_at="2025-01-08T09:00:00Z", note="馆方提供的专业摄影底图",
        ))
        archive.grant_license(MANAGER, License(
            id=f"lic-{aid}-v1", image_id=f"img-{aid}-v1", museum_id=mid,
            scope="stamp,souvenir_sheet,first_day_cover,brochure",
            granted_at="2025-01-09", valid_from="2025-01-09T00:00:00Z",
            valid_until=license_until.get(aid, "2026-12-31T23:59:59Z"),
        ))

    # --- 阶段二：设计稿与审批 -----------------------------------------
    clock.jump_to("2025-02-10T09:00:00Z")
    titles = {
        "art-guwan-peony-jar": "元青花·缠枝牡丹",
        "art-xiaohe-meiping": "元青花·萧何月下追韩信",
        "art-siai-meiping": "元青花·四爱图",
        "art-gaoyao-lidded": "元青花·云龙带盖梅瓶",
        "art-iran-phoenix-plate": "元青花·云肩凤纹大盘",
        "art-iran-peony-jar": "元青花·牡丹云龙罐",
    }
    design_ids = {}
    for i, (aid, *_rest) in enumerate(ARTIFACTS, start=1):
        design_id = f"design-{i}"
        design_ids[aid] = design_id
        archive.create_design(DESIGNER, Design(
            id=design_id, artifact_id=aid, title=titles[aid], revision=1,
            image_id=f"img-{aid}-v1", license_id=f"lic-{aid}-v1",
            created_by=DESIGNER,
            ornament_text=next(row[5] for row in ARTIFACTS if row[0] == aid),
        ))
        archive.approve_design(MANAGER, Approval(
            id=f"approval-{i}-r1", design_id=design_id, revision=1,
            decided_at="2025-02-12T10:00:00Z", approver=MANAGER,
            decision="approved", remark="构图与纹饰核对无误，同意付印",
            basis_refs=(f"img-{aid}-v1", f"lic-{aid}-v1"),
        ))

    # --- 阶段三：物料冻结与首批印制 -----------------------------------
    clock.jump_to("2025-03-03T09:00:00Z")

    def freeze(material_id: str, kind: MaterialKind, design_id: str,
               artifact_id: str, *, series: str | None = None,
               reissue_of: str | None = None) -> None:
        archive.create_material(
            MANAGER, material_id, kind, design_id,
            series=series or material_id, reissue_of=reissue_of,
        )
        archive.freeze_material(MANAGER, material_id, f"freeze-{material_id}")

    stamp_materials: list[tuple[str, str]] = []
    for i, (aid, *_rest) in enumerate(ARTIFACTS, start=1):
        if i <= 5:
            mid = f"stamp-{i}"
            freeze(mid, MaterialKind.STAMP, design_ids[aid], aid,
                   series=f"2025-yuan-stamp-{i}")
            stamp_materials.append((mid, aid))
    freeze("souvenir-sheet", MaterialKind.SOUVENIR_SHEET,
           design_ids["art-iran-peony-jar"], "art-iran-peony-jar",
           series="2025-yuan-souvenir-sheet")
    for i, (aid, *_rest) in enumerate(ARTIFACTS, start=1):
        if i <= 5:
            freeze(f"fdc-{i}", MaterialKind.FIRST_DAY_COVER,
                   design_ids[aid], aid)
    freeze("fdc-sheet", MaterialKind.FIRST_DAY_COVER,
           design_ids["art-iran-peony-jar"], "art-iran-peony-jar")
    freeze("brochure", MaterialKind.BROCHURE,
           design_ids["art-guwan-peony-jar"], "art-guwan-peony-jar")

    clock.jump_to("2025-03-10T08:00:00Z")

    def printed_batch(batch_id: str, material_id: str, quantity: int,
                      process: str, receipts: list[tuple[str, int]]) -> None:
        archive.open_batch(MANAGER, batch_id, material_id, quantity, process)
        archive.complete_batch(PRINTER, batch_id)
        for receipt_id, count in receipts:
            archive.receive_receipt(PRINTER, batch_id, receipt_id, count)

    printed_batch("batch-stamp1-01", "stamp-1", 1_200_000,
                  "影写版六色套印，背面胶印",
                  [("rc-stamp1-01-a", 800_000), ("rc-stamp1-01-b", 300_000)])
    # 相同回执重复提交：只记录，不重复扣减。
    duplicate = archive.receive_receipt(
        PRINTER, "batch-stamp1-01", "rc-stamp1-01-a", 800_000)
    assert duplicate.action == "batch.receipt_duplicate"

    printed_batch("batch-stamp2-01", "stamp-2", 1_200_000,
                  "影写版六色套印",
                  [("rc-stamp2-01-a", 600_000)])
    printed_batch("batch-stamp3-01", "stamp-3", 1_200_000,
                  "影写版六色套印",
                  [("rc-stamp3-01-a", 500_000)])
    printed_batch("batch-sheet-01", "souvenir-sheet", 900_000,
                  "影写版加雕刻凹版，边饰起凸",
                  [("rc-sheet-01-a", 400_000)])
    printed_batch("batch-fdc1-01", "fdc-1", 200_000,
                  "信封起凸烫银，钤首日纪念邮戳图案版",
                  [("rc-fdc1-01-a", 120_000)])

    # --- 阶段四：图像更正，旧批次保留、换版重印 -----------------------
    clock.jump_to("2025-04-15T09:00:00Z")
    xiaohe = "art-xiaohe-meiping"
    archive.correct_image(museum_actor("njmuseum"), Image(
        id="img-art-xiaohe-meiping-v2", artifact_id=xiaohe, version=2,
        created_at="2025-04-15T09:00:00Z",
        note="馆方复核后更换为修复完成状态的新摄影底图",
        supersedes="img-art-xiaohe-meiping-v1",
    ))
    # 更正后旧物料不得夹带新批次。
    try:
        archive.open_batch(MANAGER, "batch-stamp2-0X", "stamp-2", 10, "影写版")
    except IssuanceHalted:
        pass
    else:  # pragma: no cover - 构建期自检
        raise AssertionError("图像更正后旧物料应被暂停")

    archive.grant_license(MANAGER, License(
        id="lic-art-xiaohe-meiping-v2",
        image_id="img-art-xiaohe-meiping-v2", museum_id="njmuseum",
        scope="stamp,first_day_cover",
        granted_at="2025-04-16", valid_from="2025-04-16T00:00:00Z",
        valid_until="2026-12-31T23:59:59Z",
    ))
    archive.revise_design(
        DESIGNER, "design-2",
        "img-art-xiaohe-meiping-v2", "lic-art-xiaohe-meiping-v2",
    )
    archive.approve_design(MANAGER, Approval(
        id="approval-2-r2", design_id="design-2", revision=2,
        decided_at="2025-04-20T10:00:00Z", approver=MANAGER,
        decision="approved", remark="采用馆方更正底图，纹饰说明同步复核",
        basis_refs=("img-art-xiaohe-meiping-v2", "lic-art-xiaohe-meiping-v2"),
    ))
    freeze("stamp-2-r2", MaterialKind.STAMP, "design-2", xiaohe,
           series="2025-yuan-stamp-2", reissue_of="stamp-2")
    freeze("fdc-2-r2", MaterialKind.FIRST_DAY_COVER, "design-2", xiaohe,
           series="2025-yuan-stamp-2-fdc", reissue_of="fdc-2")

    clock.jump_to("2025-04-25T08:00:00Z")
    printed_batch("batch-stamp2-02", "stamp-2-r2", 800_000,
                  "影写版六色套印", [("rc-stamp2-02-a", 500_000)])
    printed_batch("batch-fdc2-02", "fdc-2-r2", 150_000,
                  "信封起凸烫银", [("rc-fdc2-02-a", 90_000)])

    # --- 阶段五：授权到期与续期 ---------------------------------------
    clock.jump_to("2025-07-02T09:00:00Z")
    archive.expire_license(MANAGER, "lic-art-gaoyao-lidded-v1")
    try:
        archive.open_batch(MANAGER, "batch-stamp4-0X", "stamp-4", 10, "影写版")
    except IssuanceHalted:
        pass
    else:  # pragma: no cover
        raise AssertionError("授权到期后应暂停新批次")

    clock.jump_to("2025-07-05T09:00:00Z")
    archive.renew_license(
        MANAGER, "lic-art-gaoyao-lidded-v1",
        "2025-07-05T00:00:00Z", "2027-06-30T23:59:59Z",
    )
    clock.jump_to("2025-07-08T08:00:00Z")
    printed_batch("batch-stamp4-02", "stamp-4", 600_000,
                  "影写版六色套印", [("rc-stamp4-02-a", 300_000)])

    # --- 阶段六：印刷批次异常 -----------------------------------------
    clock.jump_to("2025-08-10T09:00:00Z")
    archive.report_batch_anomaly(
        PRINTER, "batch-sheet-01",
        "抽检发现局部背胶漏涂、蓝发色差超限，已暂停该批后续发货（示例）",
    )
    try:
        archive.open_batch(MANAGER, "batch-sheet-0X", "souvenir-sheet", 10, "影写版")
    except IssuanceHalted:
        pass
    else:  # pragma: no cover
        raise AssertionError("批次异常未解除前应暂停同物料新批次")

    clock.jump_to("2025-08-18T09:00:00Z")
    archive.resolve_hold(
        MANAGER, "hold-batch-batch-sheet-01",
        "隔离异常品3000枚并单独登记，余品复检合格，恢复发行",
    )
    clock.jump_to("2025-08-20T08:00:00Z")
    printed_batch("batch-sheet-02", "souvenir-sheet", 300_000,
                  "影写版加雕刻凹版，边饰起凸",
                  [("rc-sheet-02-a", 180_000)])

    # --- 阶段七：冲突资料进入复核 -------------------------------------
    clock.jump_to("2025-08-22T09:00:00Z")
    archive.submit_conflicting_fact(
        museum_actor("iranmm"),
        "review-iran-peony-origin", "art-iran-peony-jar",
        "入藏来源在陵寺瓷目抄本与馆方旧登记卡之间编号不一（示例）",
        ["阿迪比尔陵寺瓷目抄本（示例）", "馆方1930年代登记卡（示例）"],
    )
    try:
        archive.open_batch(MANAGER, "batch-sheet-0Y", "souvenir-sheet", 10, "影写版")
    except IssuanceHalted:
        pass
    else:  # pragma: no cover
        raise AssertionError("未决复核期间应暂停相关发行")

    clock.jump_to("2025-08-28T09:00:00Z")
    archive.resolve_review(
        MANAGER, "review-iran-peony-origin",
        "经馆方核对以登记卡编号为准，瓷目抄本异文存档备查（示例）",
    )

    # --- 自检 ----------------------------------------------------------
    batch = archive.state.batches["batch-stamp1-01"]
    assert batch.issued == 1_100_000, batch.issued
    assert batch.remaining == 100_000
    assert archive.state.artifacts[xiaohe] is not None

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    archive.save(target)
    print(f"已生成 {target}：{len(archive.events)} 个事件，"
          f"{len(archive.state.artifacts)} 件器物，"
          f"{len(archive.state.materials)} 种物料，"
          f"{len(archive.state.batches)} 个批次")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "fixtures/archive.json"
    main(out)
