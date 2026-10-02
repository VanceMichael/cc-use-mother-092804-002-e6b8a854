"""元青花文物邮票发行档案：读取、校验与业务规则。

档案以追加式事件流（append-only）记录全部事实，当前状态由事件回放得出。
业务规则：

- 版本冻结：设计稿经审批后连同其引用的文物数据版本、图像版本、授权版本
  冻结进发行材料（邮票/型张/首日封/宣传册），防止版本错位；
- 风险暂停：图像更正、授权到期、批次异常、资料冲突使后续发行暂停在对应
  环节，已完成批次保留当时依据；
- 库存守恒：相同批次回执不得重复扣减库存；
- 冲突复核：互相冲突的资料必须进入复核，不得直接采用；
- 权限裁剪：博物馆只核对本馆资料，设计团队只能使用获准内容，公众只看公开
  说明，不暴露未公开库藏信息。

可按一枚邮票还原它引用的文物证据和每次批准变化。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """档案使用者角色，决定可见范围与可用命令。"""

    MANAGER = "manager"        # 集邮项目负责人
    MUSEUM = "museum"          # 文博机构管理员
    DESIGNER = "designer"      # 设计团队
    PRINTER = "printer"        # 印制厂商
    PUBLIC = "public"          # 公众查询


# ---------------------------------------------------------------------------
# 基础数据
# ---------------------------------------------------------------------------


class ArtifactOrigin(str, Enum):
    EXCAVATED = "excavated"   # 出土
    HANDED_DOWN = "handed_down"  # 传世


class MaterialKind(str, Enum):
    STAMP = "stamp"                       # 邮票
    SOUVENIR_SHEET = "souvenir_sheet"     # 小型张
    FIRST_DAY_COVER = "first_day_cover"   # 首日封
    BROCHURE = "brochure"                 # 宣传册


class HoldReason(str, Enum):
    IMAGE_CORRECTION = "image_correction"   # 图像更正
    LICENSE_EXPIRY = "license_expiry"       # 授权到期
    BATCH_ANOMALY = "batch_anomaly"         # 印刷批次异常
    CONFLICT = "conflict"                   # 资料冲突


class ReviewStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """档案中的一条不可变记录。seq 由日志在追加时分配。"""

    seq: int
    at: str
    actor: str
    action: str
    payload: dict[str, Any]

    def digest(self) -> str:
        body = json.dumps(
            {
                "at": self.at,
                "actor": self.actor,
                "action": self.action,
                "payload": self.payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "payload": self.payload,
            "digest": self.digest(),
        }


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ArchiveError(Exception):
    """规则违反基类。"""


class PermissionDenied(ArchiveError):
    """角色无权执行该操作，或越权访问非本馆资料。"""


class ValidationError(ArchiveError):
    """资料不满足结构或引用完整性要求。"""


class IssuanceHalted(ArchiveError):
    """发行被风险暂停拦截。

    ``holds`` 给出阻断本次操作的全部暂停点。
    """

    def __init__(self, message: str, holds: list["Hold"]):
        super().__init__(message)
        self.holds = holds


# ---------------------------------------------------------------------------
# 领域对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Museum:
    id: str
    name: str
    public_note: str = ""


@dataclass(frozen=True)
class Artifact:
    """一件元青花器物。internal_note 为未公开库藏信息。"""

    id: str
    museum_id: str
    name: str
    era: str
    origin_type: ArtifactOrigin
    origin_note: str           # 出土或传世来源说明
    ornament: str              # 纹饰说明
    public_description: str
    internal_note: str = ""


@dataclass(frozen=True)
class Image:
    """文物图像的一个版本。更正以新版本追加，旧版本保留。"""

    id: str
    artifact_id: str
    version: int
    created_at: str
    note: str
    supersedes: str | None = None  # 被更正的旧图像 id


@dataclass(frozen=True)
class License:
    """图像使用授权。状态随 grant/expire/renew 事件演变。"""

    id: str
    image_id: str
    museum_id: str
    scope: str                 # 例如 stamp,sheet,fdc,brochure
    granted_at: str
    valid_from: str
    valid_until: str
    status: str = "active"     # active | expired


@dataclass(frozen=True)
class Approval:
    """一次设计稿批准记录，构成逐版变化的审批链。"""

    id: str
    design_id: str
    revision: int
    decided_at: str
    approver: str
    decision: str              # approved | rejected
    remark: str
    basis_refs: tuple[str, ...]   # 批准所依据的图像/资料引用
    evidence_versions: dict[str, int] = field(default_factory=dict)  # 批准时版本快照


@dataclass(frozen=True)
class Design:
    id: str
    artifact_id: str
    title: str
    revision: int
    image_id: str
    license_id: str
    created_by: str
    ornament_text: str
    approvals: tuple[Approval, ...] = ()

    def latest_approval(self) -> Approval | None:
        return self.approvals[-1] if self.approvals else None

    def is_approved(self) -> bool:
        latest = self.latest_approval()
        return latest is not None and latest.decision == "approved"


@dataclass(frozen=True)
class Material:
    """一种发行物料对设计的引用；冻结后绑定证据版本。

    同一枚邮票换版重印时另立物料（reissue_of 指向上一版），保证旧批次的
    冻结依据不被覆盖，系列内各版可按 series 归集。
    """

    id: str
    kind: MaterialKind
    design_id: str
    artifact_id: str
    frozen: "FrozenEvidence | None"
    series: str = ""
    reissue_of: str | None = None


@dataclass(frozen=True)
class FrozenEvidence:
    """物料冻结的证据快照：让任何物料都可回溯到当时的依据。"""

    freeze_id: str
    frozen_at: str
    artifact_version: int
    image_id: str
    image_version: int
    license_id: str
    license_valid_until: str
    design_revision: int
    approval_id: str
    approval_digests: tuple[str, ...]  # 截至冻结时该设计的全部批准摘要


@dataclass(frozen=True)
class Batch:
    """印刷批次。回执按 receipt_id 幂等，保证库存不被重复扣减。"""

    id: str
    material_id: str
    quantity: int
    process_note: str          # 印制工艺
    created_at: str
    completed_at: str | None = None
    issued: int = 0
    receipts: frozenset[str] = frozenset()
    anomaly: str | None = None

    @property
    def remaining(self) -> int:
        return self.quantity - self.issued


@dataclass(frozen=True)
class Hold:
    """发行暂停点：对象可以是文物、图像、物料或批次。"""

    id: str
    reason: HoldReason
    target_type: str           # artifact | image | material | batch
    target_id: str
    opened_at: str
    resolved_at: str | None
    detail: str


@dataclass(frozen=True)
class ReviewItem:
    """复核队列条目：冲突资料在解决前不得被采用。"""

    id: str
    artifact_id: str | None
    opened_at: str
    summary: str
    sources: tuple[str, ...]
    status: ReviewStatus = ReviewStatus.OPEN
    resolution: str | None = None
    resolved_at: str | None = None


# ---------------------------------------------------------------------------
# 时钟与档案
# ---------------------------------------------------------------------------


class Clock:
    """按调用顺序给出可复现的时间戳（测试与夹具构建用）。"""

    def __init__(self, timestamps: Iterable[str]):
        self._stamps = list(timestamps)
        self._i = 0

    def now(self) -> str:
        stamp = self._stamps[self._i]
        self._i += 1
        return stamp


def _today(valid_until: str, on: str) -> bool:
    return on < valid_until


@dataclass
class ArchiveState:
    museums: dict[str, Museum] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    artifact_versions: dict[str, int] = field(default_factory=dict)
    images: dict[str, Image] = field(default_factory=dict)
    image_versions: dict[str, list[str]] = field(default_factory=dict)
    licenses: dict[str, License] = field(default_factory=dict)
    designs: dict[str, Design] = field(default_factory=dict)
    materials: dict[str, Material] = field(default_factory=dict)
    freezes: dict[str, FrozenEvidence] = field(default_factory=dict)
    batches: dict[str, Batch] = field(default_factory=dict)
    holds: list[Hold] = field(default_factory=list)
    reviews: dict[str, ReviewItem] = field(default_factory=dict)
    sequence: int = 0


class Archive:
    """追加式发行档案。写入产生事件，状态由事件回放得到。"""

    def __init__(
        self,
        events: list[Event] | None = None,
        *,
        clock: Callable[[], str] | None = None,
        today: Callable[[], str] | None = None,
    ):
        self._clock = clock or _default_clock
        self._today = today or self._clock
        self.events: list[Event] = []
        self.state = ArchiveState()
        for event in events or []:
            self._apply(event)
            self.events.append(event)

    # -- 事件日志 ---------------------------------------------------------

    def _append(
        self, actor: str, action: str, payload: dict[str, Any], *, at: str | None = None,
    ) -> Event:
        event = Event(
            seq=len(self.events) + 1,
            at=at or self._clock(),
            actor=actor,
            action=action,
            payload=payload,
        )
        self._apply(event)
        self.events.append(event)
        return event

    @staticmethod
    def _hash(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]

    # -- 注册机构与文物 ---------------------------------------------------

    def register_museum(self, actor: str, museum: Museum) -> Event:
        self._require(actor, {Role.MANAGER})
        if museum.id in self.state.museums:
            raise ValidationError(f"机构已存在：{museum.id}")
        return self._append(actor, "museum.registered", {
            "id": museum.id, "name": museum.name, "public_note": museum.public_note,
        })

    def register_artifact(self, actor: str, artifact: Artifact) -> Event:
        self._require(actor, {Role.MANAGER, Role.MUSEUM})
        if artifact.id in self.state.artifacts:
            raise ValidationError(f"文物已存在：{artifact.id}")
        if actor_role(actor) == Role.MUSEUM and actor_museum(actor) != artifact.museum_id:
            raise PermissionDenied("博物馆只能登记本馆文物")
        if artifact.museum_id not in self.state.museums:
            raise ValidationError(f"收藏机构不存在：{artifact.museum_id}")
        return self._append(actor, "artifact.registered", {
            "id": artifact.id,
            "museum_id": artifact.museum_id,
            "name": artifact.name,
            "era": artifact.era,
            "origin_type": artifact.origin_type.value,
            "origin_note": artifact.origin_note,
            "ornament": artifact.ornament,
            "public_description": artifact.public_description,
            "internal_note": artifact.internal_note,
        })

    def correct_artifact(
        self, actor: str, artifact_id: str, expected_version: int, changes: dict[str, Any]
    ) -> Event:
        """博物馆核对本馆资料后提交更正；版本必须递增。"""
        self._require(actor, {Role.MUSEUM, Role.MANAGER})
        artifact = self._artifact(artifact_id)
        if actor_role(actor) == Role.MUSEUM and actor_museum(actor) != artifact.museum_id:
            raise PermissionDenied("博物馆只能核对本馆资料")
        if self.state.artifact_versions[artifact_id] != expected_version:
            raise ValidationError("文物资料版本已变化，请基于最新版本提交")
        unknown = set(changes) - {
            "name", "era", "origin_type", "origin_note", "ornament",
            "public_description", "internal_note",
        }
        if unknown:
            raise ValidationError(f"不可更正的字段：{sorted(unknown)}")
        if not changes:
            raise ValidationError("更正内容为空")
        payload = {"id": artifact_id, "expected_version": expected_version,
                   "changes": dict(changes)}
        return self._append(actor, "artifact.corrected", payload)

    def submit_conflicting_fact(
        self, actor: str, review_id: str, artifact_id: str | None,
        summary: str, sources: list[str],
    ) -> list[Event]:
        """发现冲突资料：不覆盖既有事实，强制进入复核并暂停后续发行。"""
        self._require(actor, {Role.MUSEUM, Role.MANAGER, Role.DESIGNER})
        if review_id in self.state.reviews:
            raise ValidationError(f"复核条目已存在：{review_id}")
        opened = self._append(actor, "review.opened", {
            "id": review_id,
            "artifact_id": artifact_id,
            "summary": summary,
            "sources": list(sources),
        })
        results = [opened]
        if artifact_id is not None:
            results.append(self._append(actor, "hold.opened", {
                "id": f"hold-conflict-{review_id}",
                "reason": HoldReason.CONFLICT.value,
                "target_type": "artifact",
                "target_id": artifact_id,
                "detail": f"资料冲突进入复核：{summary}",
            }))
        return results

    def resolve_review(self, actor: str, review_id: str, resolution: str) -> list[Event]:
        """复核结论确认后关闭复核，并解除对应冲突暂停。"""
        self._require(actor, {Role.MANAGER})
        review = self.state.reviews.get(review_id)
        if review is None:
            raise ValidationError(f"复核条目不存在：{review_id}")
        if review.status is ReviewStatus.RESOLVED:
            raise ValidationError("复核条目已解决")
        resolved = self._append(actor, "review.resolved", {
            "id": review_id, "resolution": resolution,
        })
        results = [resolved]
        hold = next(
            (h for h in self.state.holds
             if h.reason is HoldReason.CONFLICT and h.id == f"hold-conflict-{review_id}"
             and h.resolved_at is None),
            None,
        )
        if hold is not None:
            results.append(self._append(actor, "hold.resolved", {
                "id": hold.id, "resolution": resolution,
            }))
        return results

    # -- 图像与授权 -------------------------------------------------------

    def add_image(self, actor: str, image: Image) -> Event:
        self._require(actor, {Role.MANAGER, Role.MUSEUM})
        artifact = self._artifact(image.artifact_id)
        if actor_role(actor) == Role.MUSEUM and actor_museum(actor) != artifact.museum_id:
            raise PermissionDenied("博物馆只能提供本馆文物图像")
        if image.id in self.state.images:
            raise ValidationError(f"图像已存在：{image.id}")
        if image.supersedes is not None and image.supersedes not in self.state.images:
            raise ValidationError("被更正图像不存在")
        return self._append(actor, "image.added", {
            "id": image.id,
            "artifact_id": image.artifact_id,
            "version": image.version,
            "created_at": image.created_at,
            "note": image.note,
            "supersedes": image.supersedes,
        })

    def correct_image(self, actor: str, new_image: Image) -> Event:
        """图像更正：登记新版本并暂停使用该文物的后续发行。"""
        self.add_image(actor, new_image)
        hold_id = f"hold-img-{new_image.id}"
        return self._append(actor, "hold.opened", {
            "id": hold_id,
            "reason": HoldReason.IMAGE_CORRECTION.value,
            "target_type": "image",
            "target_id": new_image.id,
            "detail": f"图像更正为 {new_image.id}，后续发行暂停以待重新审批",
        })

    def grant_license(self, actor: str, license_: License) -> Event:
        self._require(actor, {Role.MANAGER, Role.MUSEUM})
        image = self._image(license_.image_id)
        artifact = self._artifact(image.artifact_id)
        if actor_role(actor) == Role.MUSEUM and actor_museum(actor) != artifact.museum_id:
            raise PermissionDenied("博物馆只能授予本馆文物图像的使用权")
        if license_.id in self.state.licenses:
            raise ValidationError(f"授权已存在：{license_.id}")
        if license_.valid_from >= license_.valid_until:
            raise ValidationError("授权有效期无效")
        return self._append(actor, "license.granted", {
            "id": license_.id,
            "image_id": license_.image_id,
            "museum_id": license_.museum_id,
            "scope": license_.scope,
            "granted_at": license_.granted_at,
            "valid_from": license_.valid_from,
            "valid_until": license_.valid_until,
        })

    def expire_license(self, actor: str, license_id: str) -> list[Event]:
        """登记授权到期，并暂停引用该授权的后续发行。"""
        self._require(actor, {Role.MANAGER})
        license_ = self._license(license_id)
        if license_.status == "expired":
            raise ValidationError("授权已到期")
        expired = self._append(actor, "license.expired", {"id": license_id})
        hold = self._append(actor, "hold.opened", {
            "id": f"hold-license-{license_id}",
            "reason": HoldReason.LICENSE_EXPIRY.value,
            "target_type": "license",
            "target_id": license_id,
            "detail": f"授权 {license_id} 到期，后续发行暂停",
        })
        return [expired, hold]

    def renew_license(
        self, actor: str, license_id: str, valid_from: str, valid_until: str,
    ) -> list[Event]:
        """续期授权，并解除因该授权到期产生的暂停。"""
        self._require(actor, {Role.MANAGER})
        old = self._license(license_id)
        if valid_from >= valid_until:
            raise ValidationError("授权有效期无效")
        renewed = self._append(actor, "license.renewed", {
            "id": license_id,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "previous_valid_until": old.valid_until,
        })
        results = [renewed]
        hold = next(
            (h for h in self.state.holds
             if h.reason is HoldReason.LICENSE_EXPIRY
             and h.target_id == license_id and h.resolved_at is None),
            None,
        )
        if hold is not None:
            results.append(self._append(actor, "hold.resolved", {
                "id": hold.id,
                "resolution": f"授权续期至 {valid_until}",
            }))
        return results

    # -- 设计与审批 -------------------------------------------------------

    def create_design(self, actor: str, design: Design) -> Event:
        self._require(actor, {Role.DESIGNER, Role.MANAGER})
        if design.id in self.state.designs:
            raise ValidationError(f"设计稿已存在：{design.id}")
        artifact = self._artifact(design.artifact_id)
        image = self._image(design.image_id)
        license_ = self._license(design.license_id)
        if image.artifact_id != artifact.id or license_.image_id != image.id:
            raise ValidationError("设计稿引用的文物、图像、授权不一致")
        if actor_role(actor) == Role.DESIGNER and design.created_by != actor:
            raise PermissionDenied("只能以本人名义创建设计稿")
        return self._append(actor, "design.created", {
            "id": design.id,
            "artifact_id": design.artifact_id,
            "title": design.title,
            "revision": design.revision,
            "image_id": design.image_id,
            "license_id": design.license_id,
            "created_by": design.created_by,
            "ornament_text": design.ornament_text,
        })

    def approve_design(self, actor: str, approval: Approval) -> Event:
        """批准或驳回设计稿。批准即核验授权有效、无未决暂停与未决复核。"""
        self._require(actor, {Role.MANAGER})
        design = self._design(approval.design_id)
        if approval.revision != design.revision:
            raise ValidationError("批准的修订版本与设计稿不一致")
        last_approved = max(
            (a.revision for a in design.approvals if a.decision == "approved"),
            default=0,
        )
        if approval.decision == "approved" and approval.revision <= last_approved:
            raise ValidationError("批准记录必须按修订版本递增")
        if approval.decision == "rejected" and approval.revision < last_approved:
            raise ValidationError("不能驳回一个早已批准的旧版本")
        if approval.decision == "approved":
            # 先校验再入日志：授权失效、未决暂停或复核时不得批准。
            # 本次批准若明确采用更正后的图像，则对应图像暂停视为已处置。
            self._guard_issuance_ready(design, adopting_image_id=design.image_id)
        event = self._append(actor, "design.approved", {
            "id": approval.id,
            "design_id": approval.design_id,
            "revision": approval.revision,
            "decided_at": approval.decided_at,
            "approver": approval.approver,
            "decision": approval.decision,
            "remark": approval.remark,
            "basis_refs": list(approval.basis_refs),
        })
        if approval.decision == "approved":
            # 重新批准即采用更正图像：关闭指向当前图像的更正暂停。
            for hold in self.state.holds:
                if (
                    hold.resolved_at is None
                    and hold.reason is HoldReason.IMAGE_CORRECTION
                    and hold.target_id == design.image_id
                ):
                    self._append(actor, "hold.resolved", {
                        "id": hold.id,
                        "resolution": f"已按更正图像 {design.image_id} 重新审批",
                    })
        return event

    def revise_design(
        self, actor: str, design_id: str, image_id: str, license_id: str,
        ornament_text: str | None = None,
    ) -> Event:
        """图像更正或授权续期后，设计团队提交新修订版，版本号递增。"""
        self._require(actor, {Role.DESIGNER, Role.MANAGER})
        design = self._design(design_id)
        if actor_role(actor) == Role.DESIGNER and design.created_by != actor:
            raise PermissionDenied("只能修订本人提交的设计稿")
        image = self._image(image_id)
        license_ = self._license(license_id)
        if image.artifact_id != design.artifact_id or license_.image_id != image_id:
            raise ValidationError("修订引用的文物、图像、授权不一致")
        return self._append(actor, "design.revised", {
            "id": design_id,
            "revision": design.revision + 1,
            "image_id": image_id,
            "license_id": license_id,
            "ornament_text": design.ornament_text if ornament_text is None else ornament_text,
        })

    # -- 物料与冻结 -------------------------------------------------------

    def create_material(
        self, actor: str, material_id: str, kind: MaterialKind, design_id: str,
        *, series: str = "", reissue_of: str | None = None,
    ) -> Event:
        self._require(actor, {Role.MANAGER})
        if material_id in self.state.materials:
            raise ValidationError(f"物料已存在：{material_id}")
        design = self._design(design_id)
        if reissue_of is not None:
            previous = self.state.materials.get(reissue_of)
            if previous is None:
                raise ValidationError("换版所依据的旧物料不存在")
            if previous.kind is not kind or previous.artifact_id != design.artifact_id:
                raise ValidationError("换版物料的种类与文物必须与旧版一致")
        return self._append(actor, "material.created", {
            "id": material_id,
            "kind": kind.value,
            "design_id": design_id,
            "artifact_id": design.artifact_id,
            "series": series or material_id,
            "reissue_of": reissue_of,
        })

    def freeze_material(self, actor: str, material_id: str, freeze_id: str) -> Event:
        """把物料引用的文物证据和批准链冻结下来；后续更正不影响已冻结快照。"""
        self._require(actor, {Role.MANAGER})
        material = self.state.materials.get(material_id)
        if material is None:
            raise ValidationError(f"物料不存在：{material_id}")
        if material.frozen is not None:
            raise ValidationError("物料已冻结，证据版本不可更改")
        design = self._design(material.design_id)
        if not design.is_approved():
            raise ValidationError("设计稿未获批准，不得冻结发行物料")
        self._guard_issuance_ready(design)
        self._guard_approval_fresh(design)
        image = self._image(design.image_id)
        license_ = self._license(design.license_id)
        approvals = design.approvals
        frozen_at = self._clock()
        return self._append(actor, "material.frozen", {
            "id": freeze_id,
            "material_id": material_id,
            "frozen_at": frozen_at,
            "artifact_version": self.state.artifact_versions[design.artifact_id],
            "image_id": image.id,
            "image_version": image.version,
            "license_id": license_.id,
            "license_valid_until": license_.valid_until,
            "design_revision": design.revision,
            "approval_id": approvals[-1].id,
            "approval_digests": [self._hash(a.__dict__) for a in approvals],
        }, at=frozen_at)

    # -- 批次与库存 -------------------------------------------------------

    def open_batch(
        self, actor: str, batch_id: str, material_id: str, quantity: int,
        process_note: str,
    ) -> Event:
        self._require(actor, {Role.MANAGER, Role.PRINTER})
        if batch_id in self.state.batches:
            raise ValidationError(f"批次已存在：{batch_id}")
        material = self.state.materials.get(material_id)
        if material is None or material.frozen is None:
            raise ValidationError("只能为已冻结物料开印批次")
        if quantity <= 0:
            raise ValidationError("批次数量必须为正")
        design = self._design(material.design_id)
        self._guard_issuance_ready(design)
        self._guard_fresh_freeze(material)
        return self._append(actor, "batch.opened", {
            "id": batch_id,
            "material_id": material_id,
            "quantity": quantity,
            "process_note": process_note,
        })

    def complete_batch(self, actor: str, batch_id: str) -> Event:
        self._require(actor, {Role.PRINTER, Role.MANAGER})
        batch = self._batch(batch_id)
        if batch.completed_at is not None:
            raise ValidationError("批次已完成")
        return self._append(actor, "batch.completed", {"id": batch_id})

    def receive_receipt(self, actor: str, batch_id: str, receipt_id: str, count: int) -> Event:
        """回执扣减库存；相同批次回执重复提交不重复扣减（幂等）。"""
        self._require(actor, {Role.PRINTER, Role.MANAGER})
        batch = self._batch(batch_id)
        if receipt_id in batch.receipts:
            # 相同批次回执：登记重复提交但不改变库存。
            return self._append(actor, "batch.receipt_duplicate", {
                "batch_id": batch_id, "receipt_id": receipt_id,
            })
        if count <= 0:
            raise ValidationError("回执数量必须为正")
        if count > batch.remaining:
            raise ValidationError("回执数量超过批次剩余库存，违反库存守恒")
        return self._append(actor, "batch.receipt_received", {
            "batch_id": batch_id, "receipt_id": receipt_id, "count": count,
        })

    def report_batch_anomaly(self, actor: str, batch_id: str, detail: str) -> Event:
        """印刷批次异常：暂停该批次及同物料后续发行。"""
        self._require(actor, {Role.PRINTER, Role.MANAGER, Role.MUSEUM})
        self._batch(batch_id)
        return self._append(actor, "hold.opened", {
            "id": f"hold-batch-{batch_id}",
            "reason": HoldReason.BATCH_ANOMALY.value,
            "target_type": "batch",
            "target_id": batch_id,
            "detail": detail,
        })

    def resolve_hold(self, actor: str, hold_id: str, resolution: str) -> Event:
        self._require(actor, {Role.MANAGER})
        hold = self._hold(hold_id)
        if hold.resolved_at is not None:
            raise ValidationError("暂停已解除")
        return self._append(actor, "hold.resolved", {
            "id": hold_id, "resolution": resolution,
        })

    # -- 查询与追溯 -------------------------------------------------------

    def trace_stamp(self, material_id: str) -> dict[str, Any]:
        """按一枚邮票（或任一物料）还原文物证据与每次批准变化。"""
        material = self.state.materials.get(material_id)
        if material is None:
            raise ValidationError(f"物料不存在：{material_id}")
        design = self._design(material.design_id)
        artifact = self._artifact(design.artifact_id)
        image = self._image(design.image_id)
        license_ = self._license(design.license_id)
        approvals = [
            {
                "approval_id": a.id,
                "revision": a.revision,
                "decided_at": a.decided_at,
                "decision": a.decision,
                "approver": a.approver,
                "remark": a.remark,
                "basis_refs": list(a.basis_refs),
                "evidence_versions": dict(a.evidence_versions),
            }
            for a in design.approvals
        ]
        trace: dict[str, Any] = {
            "material_id": material.id,
            "kind": material.kind.value,
            "series": material.series,
            "reissue_of": material.reissue_of,
            "artifact": {
                "id": artifact.id,
                "museum_id": artifact.museum_id,
                "name": artifact.name,
                "origin_type": artifact.origin_type.value,
                "origin_note": artifact.origin_note,
                "ornament": artifact.ornament,
                "version_at_approval": approvals[-1]["evidence_versions"] if approvals else None,
            },
            "design": {
                "id": design.id,
                "revision": design.revision,
                "image_id": image.id,
                "image_version": image.version,
                "license_id": license_.id,
                "license_valid_until": license_.valid_until,
            },
            "approvals": approvals,
            "frozen": None,
            "batches": [],
        }
        if material.frozen is not None:
            f = material.frozen
            trace["frozen"] = {
                "freeze_id": f.freeze_id,
                "frozen_at": f.frozen_at,
                "artifact_version": f.artifact_version,
                "image_id": f.image_id,
                "image_version": f.image_version,
                "license_id": f.license_id,
                "license_valid_until": f.license_valid_until,
                "design_revision": f.design_revision,
                "approval_id": f.approval_id,
                "approval_digests": list(f.approval_digests),
            }
        for batch in self.state.batches.values():
            if batch.material_id == material_id:
                trace["batches"].append({
                    "id": batch.id,
                    "quantity": batch.quantity,
                    "issued": batch.issued,
                    "remaining": batch.remaining,
                    "completed_at": batch.completed_at,
                    "anomaly": batch.anomaly,
                    "receipts": sorted(batch.receipts),
                })
        return trace

    def public_view(self) -> dict[str, Any]:
        """公众查询视图：只含公开说明，裁剪内部备注、授权凭证与暂停细节。"""
        return {
            "museums": [
                {"id": m.id, "name": m.name, "public_note": m.public_note}
                for m in self.state.museums.values()
            ],
            "artifacts": [
                {
                    "id": a.id,
                    "museum_id": a.museum_id,
                    "name": a.name,
                    "era": a.era,
                    "origin_type": a.origin_type.value,
                    "origin_note": a.origin_note,
                    "ornament": a.ornament,
                    "description": a.public_description,
                }
                for a in self.state.artifacts.values()
            ],
            "stamps": [
                {
                    "material_id": m.id,
                    "kind": m.kind.value,
                    "title": self._design(m.design_id).title,
                    "artifact_id": m.artifact_id,
                    "series": m.series,
                    "reissue_of": m.reissue_of,
                }
                for m in self.state.materials.values()
            ],
        }

    def museum_view(self, actor: str) -> dict[str, Any]:
        """博物馆视图：仅本馆机构、文物（含内部备注）与相关图像/授权。"""
        role = actor_role(actor)
        if role is not Role.MUSEUM:
            raise PermissionDenied("仅文博机构可使用本馆视图")
        museum_id = actor.split(":", 2)[1] if actor.count(":") >= 1 else ""
        if museum_id not in self.state.museums:
            raise PermissionDenied("机构身份无法识别")
        artifacts = [a for a in self.state.artifacts.values() if a.museum_id == museum_id]
        artifact_ids = {a.id for a in artifacts}
        images = [i for i in self.state.images.values() if i.artifact_id in artifact_ids]
        image_ids = {i.id for i in images}
        return {
            "museum": self.state.museums[museum_id].__dict__,
            "artifacts": [dataclass_dict(a) for a in artifacts],
            "images": [dataclass_dict(i) for i in images],
            "licenses": [
                dataclass_dict(lic)
                for lic in self.state.licenses.values() if lic.image_id in image_ids
            ],
        }

    def designer_view(self, actor: str) -> dict[str, Any]:
        """设计团队视图：只能看到获准内容（有效授权 + 已批准 + 未暂停）。"""
        role = actor_role(actor)
        if role is not Role.DESIGNER:
            raise PermissionDenied("仅设计团队可使用设计视图")
        usable: list[dict[str, Any]] = []
        for design in self.state.designs.values():
            if not design.is_approved():
                continue
            image = self._image(design.image_id)
            license_ = self._license(design.license_id)
            if not self._license_active(license_):
                continue
            if self._open_holds_for(design):
                continue
            usable.append({
                "design_id": design.id,
                "artifact_id": design.artifact_id,
                "title": design.title,
                "revision": design.revision,
                "image_id": image.id,
                "image_version": image.version,
                "license_id": license_.id,
                "license_valid_until": license_.valid_until,
                "ornament_text": design.ornament_text,
            })
        return {"usable_designs": usable}

    # -- 持久化 -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"events": [e.to_dict() for e in self.events]}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(
        cls, path: str | Path, *,
        clock: Callable[[], str] | None = None,
        today: Callable[[], str] | None = None,
    ) -> "Archive":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        events = []
        for index, e in enumerate(raw["events"], start=1):
            event = Event(
                seq=e["seq"], at=e["at"], actor=e["actor"],
                action=e["action"], payload=e["payload"],
            )
            if e["seq"] != index:
                raise ValidationError(f"事件序号不连续：第 {index} 条为 {e['seq']}")
            recorded = e.get("digest")
            if recorded is not None and recorded != event.digest():
                raise ValidationError(f"事件 {e['seq']} 摘要不一致，档案可能被篡改")
            events.append(event)
        return cls(events, clock=clock, today=today)

    # -- 事件回放 ---------------------------------------------------------

    def _apply(self, event: Event) -> None:  # noqa: C901 - 回放分发集中在此
        s = self.state
        p = event.payload
        if event.seq != len(self.events) + 1:
            raise ValidationError(f"事件序号不连续：期望 {len(self.events)+1}，得到 {event.seq}")
        action = event.action

        if action == "museum.registered":
            s.museums[p["id"]] = Museum(p["id"], p["name"], p.get("public_note", ""))
        elif action == "artifact.registered":
            s.artifacts[p["id"]] = Artifact(
                p["id"], p["museum_id"], p["name"], p["era"],
                ArtifactOrigin(p["origin_type"]), p["origin_note"],
                p["ornament"], p["public_description"], p.get("internal_note", ""),
            )
            s.artifact_versions[p["id"]] = 1
        elif action == "artifact.corrected":
            current = s.artifacts[p["id"]]
            if s.artifact_versions[p["id"]] != p["expected_version"]:
                raise ValidationError("更正基于的版本不是当前版本")
            changes = dict(p["changes"])
            if "origin_type" in changes:
                changes["origin_type"] = ArtifactOrigin(changes["origin_type"])
            updated = replace(current, **changes)
            s.artifacts[p["id"]] = updated
            s.artifact_versions[p["id"]] += 1
        elif action == "review.opened":
            s.reviews[p["id"]] = ReviewItem(
                p["id"], p.get("artifact_id"), event.at, p["summary"],
                tuple(p["sources"]),
            )
        elif action == "review.resolved":
            review = s.reviews[p["id"]]
            s.reviews[p["id"]] = replace(
                review, status=ReviewStatus.RESOLVED,
                resolution=p["resolution"], resolved_at=event.at,
            )
        elif action == "image.added":
            image = Image(
                p["id"], p["artifact_id"], p["version"], p["created_at"],
                p["note"], p.get("supersedes"),
            )
            s.images[p["id"]] = image
            s.image_versions.setdefault(p["artifact_id"], []).append(p["id"])
        elif action == "license.granted":
            s.licenses[p["id"]] = License(
                p["id"], p["image_id"], p["museum_id"], p["scope"],
                p["granted_at"], p["valid_from"], p["valid_until"],
            )
        elif action == "license.expired":
            lic = s.licenses[p["id"]]
            s.licenses[p["id"]] = replace(lic, status="expired")
        elif action == "license.renewed":
            lic = s.licenses[p["id"]]
            s.licenses[p["id"]] = replace(
                lic, status="active",
                valid_from=p["valid_from"], valid_until=p["valid_until"],
            )
        elif action == "design.created":
            s.designs[p["id"]] = Design(
                p["id"], p["artifact_id"], p["title"], p["revision"],
                p["image_id"], p["license_id"], p["created_by"], p["ornament_text"],
            )
        elif action == "design.approved":
            design = s.designs[p["design_id"]]
            versions = {
                "artifact": s.artifact_versions[design.artifact_id],
                "image": s.images[design.image_id].version,
                "license_valid_until": s.licenses[design.license_id].valid_until,
                "design_revision": design.revision,
            }
            approval = Approval(
                p["id"], p["design_id"], p["revision"], p["decided_at"],
                p["approver"], p["decision"], p["remark"],
                tuple(p["basis_refs"]), versions,
            )
            s.designs[p["design_id"]] = replace(
                design, approvals=design.approvals + (approval,),
            )
        elif action == "design.revised":
            design = s.designs[p["id"]]
            s.designs[p["id"]] = replace(
                design,
                revision=p["revision"],
                image_id=p["image_id"],
                license_id=p["license_id"],
                ornament_text=p["ornament_text"],
            )
        elif action == "material.created":
            s.materials[p["id"]] = Material(
                p["id"], MaterialKind(p["kind"]), p["design_id"],
                p["artifact_id"], None,
                p.get("series", p["id"]), p.get("reissue_of"),
            )
        elif action == "material.frozen":
            material = s.materials[p["material_id"]]
            freeze = FrozenEvidence(
                p["id"], p["frozen_at"], p["artifact_version"],
                p["image_id"], p["image_version"], p["license_id"],
                p["license_valid_until"], p["design_revision"],
                p["approval_id"], tuple(p["approval_digests"]),
            )
            s.freezes[p["id"]] = freeze
            s.materials[p["material_id"]] = replace(material, frozen=freeze)
        elif action == "batch.opened":
            s.batches[p["id"]] = Batch(
                p["id"], p["material_id"], p["quantity"], p["process_note"], event.at,
            )
        elif action == "batch.completed":
            batch = s.batches[p["id"]]
            s.batches[p["id"]] = replace(batch, completed_at=event.at)
        elif action == "batch.receipt_received":
            batch = s.batches[p["batch_id"]]
            s.batches[p["batch_id"]] = replace(
                batch,
                issued=batch.issued + p["count"],
                receipts=batch.receipts | {p["receipt_id"]},
            )
        elif action == "batch.receipt_duplicate":
            pass  # 幂等：状态不变
        elif action == "hold.opened":
            s.holds.append(Hold(
                p["id"], HoldReason(p["reason"]), p["target_type"], p["target_id"],
                event.at, None, p["detail"],
            ))
            if p["target_type"] == "batch" and p["target_id"] in s.batches:
                batch = s.batches[p["target_id"]]
                s.batches[p["target_id"]] = replace(batch, anomaly=p["detail"])
        elif action == "hold.resolved":
            index = next(i for i, h in enumerate(s.holds) if h.id == p["id"])
            s.holds[index] = replace(s.holds[index], resolved_at=event.at)
        else:
            raise ValidationError(f"未知事件类型：{action}")

    # -- 内部规则 ---------------------------------------------------------

    def _require(self, actor: str, allowed: set[Role]) -> None:
        role = actor_role(actor)
        if role is None:
            raise PermissionDenied(f"未知角色：{actor}")
        if role not in allowed:
            raise PermissionDenied(f"{role.value} 无权执行此操作")

    def _license_active(self, license_: License) -> bool:
        return license_.status == "active" and _today(license_.valid_until, self._today())

    def _open_holds_for(
        self, design: Design, *, adopting_image_id: str | None = None,
    ) -> list[Hold]:
        """与一个设计相关的全部未解除暂停（文物/图像/物料/批次）。

        审批新修订版并明确采用更正图像（adopting_image_id）时，指向该图像的
        更正暂停不再阻断本次批准，批准后随即关闭。
        """
        material_ids = {
            m.id for m in self.state.materials.values() if m.design_id == design.id
        }
        batch_ids = {
            b.id for b in self.state.batches.values() if b.material_id in material_ids
        }
        blockers: list[Hold] = []
        for hold in self.state.holds:
            if hold.resolved_at is not None:
                continue
            if hold.reason is HoldReason.IMAGE_CORRECTION:
                if hold.target_id == adopting_image_id:
                    continue
                image = self.state.images.get(hold.target_id)
                if image is not None and image.artifact_id == design.artifact_id:
                    blockers.append(hold)
            elif hold.reason is HoldReason.LICENSE_EXPIRY:
                if hold.target_id == design.license_id:
                    blockers.append(hold)
            elif hold.reason is HoldReason.BATCH_ANOMALY:
                if hold.target_id in batch_ids:
                    blockers.append(hold)
            elif hold.reason is HoldReason.CONFLICT:
                if hold.target_id == design.artifact_id:
                    blockers.append(hold)
        return blockers

    def _guard_issuance_ready(
        self, design: Design, *, adopting_image_id: str | None = None,
    ) -> None:
        """批准/冻结/开印前统一闸门。"""
        license_ = self._license(design.license_id)
        holds: list[Hold] = []
        if not self._license_active(license_):
            holds.append(Hold(
                id=f"derived-license-{license_.id}",
                reason=HoldReason.LICENSE_EXPIRY,
                target_type="license", target_id=license_.id,
                opened_at=license_.valid_until, resolved_at=None,
                detail="授权已到期或失效",
            ))
        open_review = next(
            (r for r in self.state.reviews.values()
             if r.status is ReviewStatus.OPEN and r.artifact_id == design.artifact_id),
            None,
        )
        if open_review is not None:
            holds.append(Hold(
                id=f"derived-review-{open_review.id}",
                reason=HoldReason.CONFLICT,
                target_type="artifact", target_id=design.artifact_id,
                opened_at=open_review.opened_at, resolved_at=None,
                detail=f"存在未决资料复核：{open_review.summary}",
            ))
        holds.extend(self._open_holds_for(
            design, adopting_image_id=adopting_image_id))
        if holds:
            raise IssuanceHalted("发行条件不满足，已暂停在对应环节", holds)

    def _guard_approval_fresh(self, design: Design) -> None:
        """冻结前，最近一次批准依据的证据版本必须仍是当前版本。

        文物资料在批准后被更正时，需先提交设计修订并重新审批，避免把旧
        批准误用于新物料。
        """
        latest = next(
            (a for a in reversed(design.approvals) if a.decision == "approved"), None,
        )
        if latest is None:
            raise ValidationError("设计稿尚无有效批准")
        versions = latest.evidence_versions
        artifact_now = self.state.artifact_versions[design.artifact_id]
        image_now = self.state.images[design.image_id].version
        if versions.get("artifact") != artifact_now or versions.get("image") != image_now:
            raise ValidationError(
                "批准所依据的文物或图像版本已变化，请提交修订并重新审批后再冻结"
            )

    def _guard_fresh_freeze(self, material: Material) -> None:
        """新批次只能依据与当前设计一致的冻结快照。

        设计因图像更正/授权续期而修订后，旧物料的冻结即告过时：旧批次保留
        当时依据，但新印批次必须换版（reissue_of）重新冻结。
        """
        freeze = material.frozen
        assert freeze is not None
        design = self._design(material.design_id)
        stale = (
            freeze.image_id != design.image_id
            or freeze.license_id != design.license_id
            or freeze.design_revision != design.revision
            or freeze.artifact_version
            != self.state.artifact_versions[design.artifact_id]
        )
        if stale:
            raise ValidationError(
                f"物料 {material.id} 的冻结依据已过时，"
                "请基于修订后的设计稿换版并重新冻结，旧批次仍保留当时依据"
            )

    def _artifact(self, artifact_id: str) -> Artifact:
        value = self.state.artifacts.get(artifact_id)
        if value is None:
            raise ValidationError(f"文物不存在：{artifact_id}")
        return value

    def _image(self, image_id: str) -> Image:
        value = self.state.images.get(image_id)
        if value is None:
            raise ValidationError(f"图像不存在：{image_id}")
        return value

    def _license(self, license_id: str) -> License:
        value = self.state.licenses.get(license_id)
        if value is None:
            raise ValidationError(f"授权不存在：{license_id}")
        return value

    def _design(self, design_id: str) -> Design:
        value = self.state.designs.get(design_id)
        if value is None:
            raise ValidationError(f"设计稿不存在：{design_id}")
        return value

    def _batch(self, batch_id: str) -> Batch:
        value = self.state.batches.get(batch_id)
        if value is None:
            raise ValidationError(f"批次不存在：{batch_id}")
        return value

    def _hold(self, hold_id: str) -> Hold:
        value = next((h for h in self.state.holds if h.id == hold_id), None)
        if value is None:
            raise ValidationError(f"暂停记录不存在：{hold_id}")
        return value


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


_ACTOR_PREFIXES = {
    "manager": Role.MANAGER,
    "museum": Role.MUSEUM,
    "designer": Role.DESIGNER,
    "printer": Role.PRINTER,
    "public": Role.PUBLIC,
}


def actor_role(actor: str) -> Role | None:
    """actor 形如 ``museum:pala:李管理员``，前缀决定角色，机构段用于本馆校验。"""
    prefix = actor.split(":", 1)[0]
    return _ACTOR_PREFIXES.get(prefix)


def actor_museum(actor: str) -> str | None:
    parts = actor.split(":")
    return parts[1] if len(parts) >= 2 and parts[0] == "museum" else None


def dataclass_dict(value: Any) -> Any:
    """把枚举/嵌套 dataclass 转为可序列化字典（博物馆视图用）。"""
    if hasattr(value, "__dataclass_fields__"):
        return {k: dataclass_dict(v) for k, v in value.__dict__.items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [dataclass_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: dataclass_dict(v) for k, v in value.items()}
    return value


def _default_clock() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
