"""读取、校验并查询元青花文物主题发行档案。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .stamp_context import DOMAIN

ARCHIVE_KEYS = frozenset(
    {
        "domain",
        "version",
        "institutions",
        "artifacts",
        "licenses",
        "designs",
        "materials",
        "batches",
        "receipts",
        "holds",
        "reviews",
    }
)

MATERIAL_KINDS = frozenset({"邮票", "小型张", "首日封", "宣传册"})
PROVENANCE_KINDS = frozenset({"出土", "传世"})
VISIBILITIES = frozenset({"公开", "内部"})
LICENSE_STATUS = frozenset({"有效", "到期", "撤销"})
DESIGN_STATUS = frozenset({"草稿", "待审", "已批准", "驳回"})
BATCH_STATUS = frozenset({"计划中", "印制中", "暂停", "已完成"})
HOLD_STAGES = frozenset({"设计", "审批", "印制", "发行"})
HOLD_REASONS = frozenset({"图像更正", "授权到期", "批次异常"})
HOLD_STATUS = frozenset({"未解除", "已解除"})
REVIEW_STATUS = frozenset({"待复核", "复核中", "已解决"})

REQUIRED_FIELDS = {
    "institutions": ("id", "name"),
    "artifacts": ("id", "name", "institution_id", "provenance", "decoration", "visibility"),
    "licenses": ("id", "artifact_id", "granted_by", "scope", "valid_from", "valid_until", "status"),
    "designs": ("id", "artifact_id", "version", "license_id", "decoration", "status", "history"),
    "materials": ("id", "kind", "title", "uses", "frozen"),
    "batches": ("id", "material_id", "process", "quantity", "status"),
    "receipts": ("id", "batch_id", "quantity"),
    "holds": ("id", "stage", "reason", "status"),
    "reviews": ("id", "subject", "status"),
}

NON_EMPTY = ("institutions", "artifacts", "licenses", "designs", "materials", "batches")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in items}


def _is_date(value: Any) -> bool:
    return isinstance(value, str) and bool(_DATE.match(value))


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _check_entry(collection: str, entry: Any) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{collection}条目必须是对象")
    missing = [key for key in REQUIRED_FIELDS[collection] if key not in entry]
    if missing:
        raise ValueError(f"{collection}条目缺少字段:{','.join(missing)}")
    if not isinstance(entry["id"], str) or not entry["id"].strip():
        raise ValueError(f"{collection}条目编号无效")


def _check_enums(value: dict[str, Any]) -> None:
    for artifact in value["artifacts"]:
        provenance = artifact["provenance"]
        if not isinstance(provenance, dict) or provenance.get("kind") not in PROVENANCE_KINDS:
            raise ValueError("文物来源无效")
        if artifact["visibility"] not in VISIBILITIES:
            raise ValueError("文物公开级别无效")
    for license_ in value["licenses"]:
        scope = license_["scope"]
        if not isinstance(scope, list) or not scope or any(kind not in MATERIAL_KINDS for kind in scope):
            raise ValueError("许可范围无效")
        if not (_is_date(license_["valid_from"]) and _is_date(license_["valid_until"])):
            raise ValueError("许可有效期无效")
        if license_["valid_from"] > license_["valid_until"]:
            raise ValueError("许可有效期无效")
        if license_["status"] not in LICENSE_STATUS:
            raise ValueError("许可状态无效")
    for design in value["designs"]:
        if not _is_positive_int(design["version"]):
            raise ValueError("设计版本无效")
        if design["status"] not in DESIGN_STATUS:
            raise ValueError("设计状态无效")
        history = design["history"]
        if not isinstance(history, list) or any(
            not isinstance(step, dict)
            or not _is_positive_int(step.get("version"))
            or not step.get("decision")
            or not _is_date(step.get("at"))
            for step in history
        ):
            raise ValueError("设计审批记录无效")
    for material in value["materials"]:
        if material["kind"] not in MATERIAL_KINDS:
            raise ValueError("物料类型无效")
        uses = material["uses"]
        if not isinstance(uses, list) or not uses or any(not isinstance(use, str) for use in uses):
            raise ValueError("物料使用关系无效")
        if not isinstance(material["frozen"], bool):
            raise ValueError("物料冻结标记无效")
    for batch in value["batches"]:
        if batch["status"] not in BATCH_STATUS:
            raise ValueError("批次状态无效")
        if not _is_positive_int(batch["quantity"]):
            raise ValueError("批次数量无效")
        if batch["status"] == "已完成":
            basis = batch.get("basis")
            if not _is_date(batch.get("completed_at")) or not isinstance(basis, dict):
                raise ValueError("已完成批次缺少当时依据")
            if not isinstance(basis.get("uses"), list) or not basis["uses"]:
                raise ValueError("已完成批次缺少当时依据")
    for receipt in value["receipts"]:
        if not _is_positive_int(receipt["quantity"]):
            raise ValueError("回执数量无效")
    for hold in value["holds"]:
        if (
            hold["stage"] not in HOLD_STAGES
            or hold["reason"] not in HOLD_REASONS
            or hold["status"] not in HOLD_STATUS
        ):
            raise ValueError("暂停记录无效")
        targets = [key for key in ("artifact_id", "license_id", "batch_id") if key in hold]
        if len(targets) != 1:
            raise ValueError("暂停记录必须且只能指向一个对象")
    for review in value["reviews"]:
        if review["status"] not in REVIEW_STATUS:
            raise ValueError("复核状态无效")


def load_archive(path: Path) -> dict[str, Any]:
    """读取发行档案,拒绝缺字段、错领域或结构不完整的内容。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != ARCHIVE_KEYS:
        raise ValueError("档案字段不完整")
    if value["domain"] != DOMAIN:
        raise ValueError("领域标识不一致")
    if not _is_positive_int(value["version"]):
        raise ValueError("档案版本无效")
    for collection in REQUIRED_FIELDS:
        entries = value[collection]
        if not isinstance(entries, list):
            raise ValueError(f"{collection}必须是列表")
        if collection in NON_EMPTY and not entries:
            raise ValueError(f"{collection}内容不足")
        ids: set[str] = set()
        for entry in entries:
            _check_entry(collection, entry)
            if entry["id"] in ids:
                raise ValueError(f"{collection}编号重复:{entry['id']}")
            ids.add(entry["id"])
    _check_enums(value)
    return value


def _license_valid_on(license_: dict[str, Any], day: str) -> bool:
    return license_["valid_from"] <= day <= license_["valid_until"]


def _affected_batches(archive: dict[str, Any], hold: dict[str, Any]) -> list[dict[str, Any]]:
    designs = _index(archive["designs"])
    materials = _index(archive["materials"])
    affected = []
    for batch in archive["batches"]:
        material = materials.get(batch["material_id"])
        if material is None:
            continue
        used = [designs[use] for use in material["uses"] if use in designs]
        if hold.get("batch_id") == batch["id"]:
            affected.append(batch)
        elif "license_id" in hold and any(d["license_id"] == hold["license_id"] for d in used):
            affected.append(batch)
        elif "artifact_id" in hold and any(d["artifact_id"] == hold["artifact_id"] for d in used):
            affected.append(batch)
    return affected


def find_conflicts(archive: dict[str, Any]) -> list[str]:
    """找出需要复核的资料冲突。"""
    designs = _index(archive["designs"])
    artifacts = _index(archive["artifacts"])
    conflicts: list[str] = []
    usage: dict[tuple[str, str], set[str]] = {}
    for material in archive["materials"]:
        for design_id in material["uses"]:
            design = designs.get(design_id)
            if design is None or design["status"] != "已批准":
                continue
            key = (design["artifact_id"], material["kind"])
            usage.setdefault(key, set()).add(design_id)
    for (artifact_id, kind), design_ids in usage.items():
        if len(design_ids) > 1:
            conflicts.append(f"usage:{artifact_id}:{kind}")
    for design in archive["designs"]:
        artifact = artifacts.get(design["artifact_id"])
        if (
            design["status"] == "已批准"
            and artifact is not None
            and design["decoration"] != artifact["decoration"]
        ):
            conflicts.append(f"decoration:{design['id']}")
    return sorted(conflicts)


def validate_archive(archive: dict[str, Any], as_of: str) -> list[str]:
    """按业务规则校验档案,返回违规说明列表(空列表表示通过)。"""
    errors: list[str] = []
    institutions = _index(archive["institutions"])
    artifacts = _index(archive["artifacts"])
    licenses = _index(archive["licenses"])
    designs = _index(archive["designs"])
    materials = _index(archive["materials"])
    batches = _index(archive["batches"])

    for artifact in archive["artifacts"]:
        if artifact["institution_id"] not in institutions:
            errors.append(f"文物{artifact['id']}的收藏机构不存在")

    for license_ in archive["licenses"]:
        artifact = artifacts.get(license_["artifact_id"])
        if artifact is None:
            errors.append(f"许可{license_['id']}指向不存在的文物")
            continue
        if license_["granted_by"] != artifact["institution_id"]:
            errors.append(f"许可{license_['id']}须由收藏机构授予")
        if license_["status"] == "有效" and as_of > license_["valid_until"]:
            errors.append(f"许可{license_['id']}状态与有效期不一致")
        if license_["status"] == "到期" and as_of <= license_["valid_until"]:
            errors.append(f"许可{license_['id']}状态与有效期不一致")

    for design in archive["designs"]:
        if design["artifact_id"] not in artifacts:
            errors.append(f"设计{design['id']}指向不存在的文物")
        license_ = licenses.get(design["license_id"])
        if license_ is None:
            errors.append(f"设计{design['id']}缺少图像许可")
            continue
        if license_["artifact_id"] != design["artifact_id"]:
            errors.append(f"设计{design['id']}与许可{license_['id']}的文物不一致")
        versions = [step["version"] for step in design["history"]]
        if versions != sorted(versions) or len(set(versions)) != len(versions):
            errors.append(f"设计{design['id']}的审批版本未递增")
        if design["status"] == "已批准":
            approvals = [step for step in design["history"] if step["decision"] == "批准"]
            if not approvals:
                errors.append(f"设计{design['id']}已批准但缺少批准记录")
            elif not _license_valid_on(license_, approvals[-1]["at"]):
                errors.append(f"设计{design['id']}批准时许可{license_['id']}不在有效期内")

    for material in archive["materials"]:
        for design_id in material["uses"]:
            design = designs.get(design_id)
            if design is None:
                errors.append(f"物料{material['id']}引用了不存在的设计{design_id}")
                continue
            license_ = licenses.get(design["license_id"])
            if license_ is not None and material["kind"] not in license_["scope"]:
                errors.append(f"物料{material['id']}超出许可{license_['id']}的范围")
            if material["frozen"] and design["status"] != "已批准":
                errors.append(f"物料{material['id']}已冻结但设计{design_id}未批准")

    for batch in archive["batches"]:
        material = materials.get(batch["material_id"])
        if material is None:
            errors.append(f"批次{batch['id']}指向不存在的物料")
            continue
        used = [designs[use] for use in material["uses"] if use in designs]
        if batch["status"] == "已完成":
            if not material["frozen"]:
                errors.append(f"批次{batch['id']}已完成但物料{material['id']}未冻结")
            basis_uses = batch["basis"]["uses"]
            if {use.get("design_id") for use in basis_uses} != set(material["uses"]):
                errors.append(f"批次{batch['id']}的依据与物料使用关系不一致")
            for use in basis_uses:
                design = designs.get(use.get("design_id"))
                license_ = licenses.get(use.get("license_id"))
                if design is None or license_ is None:
                    errors.append(f"批次{batch['id']}的依据指向不存在的记录")
                    continue
                if use.get("version") != design["version"] or design["license_id"] != license_["id"]:
                    errors.append(f"批次{batch['id']}的依据与设计{design['id']}不一致")
                if not _license_valid_on(license_, batch["completed_at"]):
                    errors.append(f"批次{batch['id']}完成时许可{license_['id']}不在有效期内")
        else:
            expired = [
                licenses[design["license_id"]]
                for design in used
                if design["license_id"] in licenses
                and not _license_valid_on(licenses[design["license_id"]], as_of)
            ]
            for license_ in expired:
                if batch["status"] != "暂停":
                    errors.append(f"批次{batch['id']}使用的许可{license_['id']}已到期但未暂停")
                if not any(
                    hold["status"] == "未解除"
                    and hold["reason"] == "授权到期"
                    and hold.get("license_id") == license_["id"]
                    for hold in archive["holds"]
                ):
                    errors.append(f"批次{batch['id']}缺少许可{license_['id']}到期的暂停记录")

    for hold in archive["holds"]:
        if hold["status"] != "未解除":
            continue
        for batch in _affected_batches(archive, hold):
            if batch["status"] not in ("暂停", "已完成"):
                errors.append(f"暂停{hold['id']}未落实到批次{batch['id']}")

    totals: dict[str, int] = {}
    for receipt in archive["receipts"]:
        batch = batches.get(receipt["batch_id"])
        if batch is None:
            errors.append(f"回执{receipt['id']}指向不存在的批次")
            continue
        totals[batch["id"]] = totals.get(batch["id"], 0) + receipt["quantity"]
    for batch_id, total in totals.items():
        if total > batches[batch_id]["quantity"]:
            errors.append(f"批次{batch_id}回执超量")

    subjects = {review["subject"] for review in archive["reviews"]}
    for conflict in find_conflicts(archive):
        if conflict not in subjects:
            errors.append(f"冲突{conflict}未进入复核")

    return errors


def stock_after(
    opening: dict[str, int], archive: dict[str, Any], receipts: list[dict[str, Any]]
) -> dict[str, int]:
    """按回执扣减库存;同一回执编号只入账一次,重复提交不再扣减。"""
    batches = _index(archive["batches"])
    applied: set[str] = set()
    stock = dict(opening)
    for receipt in receipts:
        receipt_id = receipt["id"]
        if receipt_id in applied:
            continue
        batch = batches.get(receipt["batch_id"])
        if batch is None:
            raise ValueError(f"回执{receipt_id}指向不存在的批次")
        applied.add(receipt_id)
        material_id = batch["material_id"]
        stock[material_id] = stock.get(material_id, 0) - receipt["quantity"]
    return stock


def public_view(archive: dict[str, Any]) -> dict[str, Any]:
    """生成公众查询视图:只保留公开文物的白名单字段。"""
    institutions = _index(archive["institutions"])
    designs = _index(archive["designs"])
    artifacts = [item for item in archive["artifacts"] if item["visibility"] == "公开"]
    visible = {item["id"] for item in artifacts}
    public_artifacts = [
        {
            "id": item["id"],
            "name": item["name"],
            "decoration": item["decoration"],
            "institution": institutions[item["institution_id"]]["name"],
            "provenance": item["provenance"]["kind"],
        }
        for item in artifacts
    ]
    public_materials = []
    for material in archive["materials"]:
        used = {designs[use]["artifact_id"] for use in material["uses"] if use in designs}
        if used and used <= visible:
            public_materials.append(
                {
                    "id": material["id"],
                    "kind": material["kind"],
                    "title": material["title"],
                    "artifacts": sorted(used),
                }
            )
    return {
        "domain": archive["domain"],
        "version": archive["version"],
        "artifacts": public_artifacts,
        "materials": public_materials,
    }


def trace_material(archive: dict[str, Any], material_id: str) -> dict[str, Any]:
    """按单枚物料还原其文物证据、每次批准变化与批次依据。"""
    material = _index(archive["materials"]).get(material_id)
    if material is None:
        raise ValueError(f"未找到物料{material_id}")
    designs = _index(archive["designs"])
    artifacts = _index(archive["artifacts"])
    institutions = _index(archive["institutions"])
    licenses = _index(archive["licenses"])
    used_designs = [designs[use] for use in material["uses"] if use in designs]
    used_artifacts = [
        artifacts[design["artifact_id"]]
        for design in used_designs
        if design["artifact_id"] in artifacts
    ]
    return {
        "material": material,
        "designs": [
            {
                "id": design["id"],
                "version": design["version"],
                "status": design["status"],
                "approvals": design["history"],
            }
            for design in used_designs
        ],
        "artifacts": used_artifacts,
        "institutions": [
            institutions[item["institution_id"]]
            for item in used_artifacts
            if item["institution_id"] in institutions
        ],
        "licenses": [
            licenses[design["license_id"]]
            for design in used_designs
            if design["license_id"] in licenses
        ],
        "batches": [batch for batch in archive["batches"] if batch["material_id"] == material_id],
    }
