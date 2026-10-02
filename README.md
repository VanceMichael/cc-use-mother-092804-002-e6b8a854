# 元青花文物邮票授权与版本档案

管理元青花文物图像、授权、设计版本与邮票发行物料的关系，确保设计、文物保管、
版权授权与发行批次之间任何一处版本变化都可追溯、可暂停、可复核。

## 参与方与事实

主要参与方包括集邮项目负责人、文博机构管理员、设计人员、印制厂商、公众查询人员。领域资料记录以下已经确认的事实：

- 邮政发行元青花主题特种邮票一套五枚和小型张
- 题材来自五家文博机构的六件元青花器物
- 首日封采用与邮票对应的文物图像和起凸工艺

## 业务约束

- 文物来源
- 图像授权
- 发行物料版本
- 公开信息裁剪
- 批次库存守恒

## 档案结构

档案是追加式事件流（`fixtures/archive.json`），当前状态由事件回放得出：

```
机构博物馆 ── 文物（来源/纹饰/公开说明/内部备注，版本递增）
                 │
                 ├── 图像 v1, v2…（更正只追加新版本）
                 │      └── 授权（授予/到期/续期，决定能否继续使用）
                 │
                 ├── 设计稿（修订递增，批准/驳回留链）
                 │      └── 物料（邮票/型张/首日封/宣传册）── 冻结证据快照
                 │             └── 印刷批次（完成/回执，库存守恒）
                 │
                 └── 暂停点（图像更正/授权到期/批次异常/冲突复核）与复核队列
```

- `contracts/context.schema.json`：领域上下文资料结构；
- `contracts/archive.schema.json`：事件日志契约（20 种事件、载荷与摘要规则）；
- `fixtures/context.json`：领域上下文示例；
- `fixtures/archive.json`：示例发行档案（5 机构、6 器物、15 种物料、9 批次，含
  图像更正换版、授权到期续期、批次异常隔离、冲突复核四条处置线）；
- `src/stamp_context.py`：读取并校验领域上下文；
- `src/stamp_archive.py`：事件日志、角色权限与全部发行规则；
- `scripts/build_fixture.py`：按业务时间线重建示例档案。

## 角色

`Archive` 的所有命令都带 actor，前缀决定角色：`manager:*` 负责人、
`museum:<机构id>:<岗位>` 文博机构、`designer:*` 设计、`printer:*` 印制、
`public` 公众。博物馆只能核对本馆资料；设计视图只返回已批准、授权有效且未暂停
的内容；公众视图裁剪内部库藏备注、授权凭证与暂停细节。

## 快速使用

```python
from src.stamp_archive import Archive

arc = Archive.load("fixtures/archive.json", today=lambda: "2025-09-01T00:00:00Z")

# 按一枚邮票还原文物证据、每次批准变化、冻结快照与批次
trace = arc.trace_stamp("stamp-2-r2")

# 三种裁剪视图
arc.public_view()                                   # 公众：只看公开说明
arc.museum_view("museum:njmuseum:藏品保管岗")        # 本馆核对
arc.designer_view("designer:stamp-studio:主设岗")    # 获准内容
```

当授权失效、证据版本变化、存在未解除暂停或未决复核时，批准/冻结/开印会抛出
`IssuanceHalted` 或 `ValidationError`，后续发行停在对应环节；已完成批次不受影响，
仍保留冻结时的依据。相同批次回执重复提交记 `batch.receipt_duplicate`，不扣库存。

## 开发命令

重建示例档案：

```bash
python3 scripts/build_fixture.py
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

编译检查：

```bash
python3 -m compileall -q src scripts
```

以上命令只读取仓库内文件，不需要连接外部业务系统。
