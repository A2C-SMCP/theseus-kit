"""Skill: apply-config-plan — 阶段三：计划 → Draft → 发布。

The final tier.  Executes a confirmed plan file item by item against the
current draft (create_draft / update_draft), negotiates with the user whether
to store a template, validates, and only then negotiates publication —
publish_config is an explicit, irreversible, ack-gated action.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="apply-config-plan",
    description="阶段三——把已确认的计划文件逐条落实到 Draft：调用 create_draft/update_draft，与用户商议 Template 存储，做校验，商议后发布。",
    content={
        "SKILL.md": """---
name: apply-config-plan
description: 阶段三——把阶段二已确认的计划文件落实到 Draft：逐条执行计划并回写进度，调用 create_draft / update_draft 等 MCP 工具，与用户商议是否存 Template，做相应校验，与用户商议后发布。当计划文件已确认、需要落地配置时使用。
---

# Apply Config Plan —— 计划 → Draft → 发布

## 流程

1. **读计划、建 SubTask**：读 `plans/<计划文件>`，把计划条目转成 SubTask 清单；每完成一条**回写进度到计划文件**（可追溯，中断可续）
2. **落到 Draft**：按计划逐条执行——`create_draft`（见 manage-topology 技能）/ `update_draft`（见 tune-config 技能，注意 `expected_hash` 冲突与 field-design / conflict-resolution / validation-strategy 参考）；本体定义变更经平台导入入口落 Draft
3. **Template 决策**：与用户商议这段配置是否值得存模板（save-template 技能：好模板特征、粒度选择）——值得才 `save_template`，**不默认存**
4. **校验**：`validate_draft` + 相关技能自检；失败逐条修正后重跑，**校验不过不进入发布商议**
5. **发布商议**：`publish_config` 是显式不可逆动作——向用户说明变更摘要与影响面，用户确认并 acknowledge 后才发布（publish-config 技能的 ack 门槛）

## 纪律

- 计划外的不顺手改——发现计划外问题回阶段二补计划，不擅自扩范围
- 每步结果回写计划文件（可追溯）
- 发布永远是与用户的商议结果，绝不自动触发

## 上游

- 计划文件：plan-config（阶段二）
- 安全不变量与工作目录约定：theseus（总纲）
""",
    },
)
