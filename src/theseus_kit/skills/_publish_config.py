"""Skill: publish-config — 将草稿配置发布到生产环境。

Guides the LLM through the preflight → approval → publish → verify cycle.
Emphasises that publish is global and irreversible, with an explicit
acknowledge_publish boundary.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="publish-config",
    description="将 TFRobot 草稿配置发布到生产环境：预检 → 审批 → 发布 → 验证。全局不可逆操作。",
    content={
        "SKILL.md": """---
name: publish-config
description: 将草稿配置发布到线上（生产）——预检、审批边界、发布、验证。全局且不可逆。
---

# 发布配置

你正在通过 `theseus-kit` 将 TFRobot 草稿配置发布到线上。本技能覆盖**预检 → 审批 → 发布 → 验证**循环。

> ⚠️ **发布是全局且不可逆的。** 它发布从 ROBOT-scene 草稿开始的**整个**配置树。没有单节点发布——所有 draft 变更一起上线。

## 标准发布流程

### 第 1 步：发布前预检

```python
# 三态概览
summary = get_config_summary()
# 检查：states.draft.status、states.draft.root_locator
# 记录：states.online.revision（用于发布后对比）

# 全量校验
validation = validate_draft()  # 不传 setting_id = 全量预发布检查
# 如果 validation.valid == False → 打印 fail_count 和每个失败节点的 errors
# → 回到 update-config 技能修复问题
```

如果 `draft.status == "publishing"` → 说明另一个发布正在进行中，等待后重试。

### 第 2 步：显式审批边界

**发布需要用户显式确认。** 调用 `publish_config` 前，向用户展示：

- 草稿根定位符和状态
- 即将发布的内容概要（scene 数量、修改的节点、新增的节点）
- **明确警告**：「这将把所有草稿发布到生产环境。此操作不可逆。确认发布？」

在用户**明确确认之前**，**不要**使用 `acknowledge_publish=true`。

### 第 3 步：发布

```python
publish_config(
    acknowledge_publish=True,           # 必须！否则被拒绝
    expected_root_hash="abc123...",     # 推荐：来自 get_config_summary 的草稿 root hash
)
```

`expected_root_hash` 防止：预检和发布之间有人修改了草稿。

### 第 4 步：发布后验证

```python
summary = get_config_summary()
# 验证：
# - states.draft.status → "clean"（所有修改已发布）
# - states.online.revision → 相比发布前已变化
# - states.online.root_locator → 与 draft 一致
# 返回的 onlineRobotId → 确认是目标机器人
```

### 第 5 步：处理发布失败

| 错误 | 原因 | 处理 |
|------|------|------|
| HTTP 400 "acknowledge_publish" | 未确认 | 与用户重新确认后设为 `true` |
| HTTP 400 "expected_root_hash" | 草稿根 hash 变了 | 重新读取，重新确认 |
| HTTP 401/403 | 缺少 `config:publish` Scope | 确认凭证，不要在不更换 Scope 的情况下重试 |
| HTTP 409 | 草稿已在发布中 | 等待，重新检查 |
| HTTP 422 | 发布前校验失败 | 按 validation errors 修复草稿 |

## 子资源

- `references/preflight-deep-dive.md` — 发布预检深入指南

## 关键约束

- **发布是全局的**——所有 draft 变更一起上线，没有部分发布。
- **每次发布一次审批**——每次都需要 `acknowledge_publish=true`。
- **发布后验证是必须的**——确认 online 状态已更新才算完成。
- **本工具不修改草稿**——发布后 draft 状态变为 `clean`，草稿保留。
- **模板不受影响**——模板有独立生命周期。
""",
        "references/preflight-deep-dive.md": """# 发布预检深入指南

## 变更节点深度确认

全量逐节点检查是不现实的——未变更节点已经过此前发布验证，无需重复确认。发布前聚焦**本次变更涉及的节点**：

1. 通过 `get_config_summary` 对比 `states.draft` 和 `states.online`，识别变更范围
2. 对**每个变更节点**做深度确认：
   - [ ] 字段值符合 LLMTEXT 约束（类型、范围、必填）
   - [ ] 引用关系完整（被引用节点存在且在发布范围内）
   - [ ] 敏感字段已正确设置（即使读取时被脱敏为 `<<redacted>>`）
3. 对**未变更节点**信任已有状态，不做逐节点复查

> `validate_draft()` 会对全量草稿做系统性校验（包括未变更但被变更节点间接影响的节点）。如果全量校验通过，未变更节点可以认为是安全的。

## Topology 检查

`get_config_summary(state="draft")` 中如果 `status == "dirty"`：

1. 存在多棵引用树（`roots > 1`）——检查哪些根节点是意外的
2. 存在孤立节点（`orphans`）——它们没有任何引用关系，是否需要保留？

孤立节点本身不会阻止发布，但它们可能表示不完整的配置——某个节点创建了但没有被连接到主引用树。

## 发布失败的恢复

发布失败时草稿**不受影响**——可以修复后重新发布。常见恢复路径：

1. `validate_draft` 失败 → 按错误修复 → 重新预检 → 重新发布
2. `publish_config` 返回 422 → 服务端预检不通过 → 修复草稿 → 重新发布
3. `publish_config` 返回 409 → 等待 → 重新检查 → 重新发布
4. 网络超时 → 调用 `get_config_summary` 检查 `draft.status`：
   - 如果是 `"publishing"` → 发布仍在进行，等待后重查
   - 如果是 `"clean"` → 发布已完成
   - 如果是 `"dirty"` → 发布未生效，重试

## 发布 vs 验证的关系

`validate_draft()`（全量预检）在本地做的检查与 `publish_config` 服务端预检**是同一套逻辑**。如果 `validate_draft` 通过，`publish_config` 不应该因为校验而失败。如果出现了不一致，说明 validate 和 publish 之间草稿被修改了——`expected_root_hash` 可以捕获这种情况。  # noqa: E501
""",
    },
)
