"""Skill: persona-optimize — 机器人画像的增量优化采访。

Follow-up interview for an existing robot persona.  Reads the persona
(including its 变更记录 changelog section), guides the user to propose
optimizations chapter by chapter, records each change with its rationale into
the persona's 变更记录 section, and confirms with the user before any
knowledge-graph-affecting change is handed off to plan-config.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="persona-optimize",
    description="机器人画像的增量优化采访——画像已存在时，引导用户提出优化建议，变更与理由成对写入画像「变更记录」章节。",
    content={
        "SKILL.md": """---
name: persona-optimize
description: 机器人画像的增量优化采访——画像已存在时，引导用户提出优化建议，把每条变更与理由写入画像「变更记录」章节并同步正文。当用户表示要调整/优化已有机器人画像（岗位职责、技术要求、工作范围、知识图谱等）时使用。
---

# Persona Optimize —— 画像优化采访

## 何时用

工作目录 `<slug>/<slug>.md` 已存在且用户要调整（阶段一分流：无画像 → persona-interview；有画像且要改 → 本技能）。slug 约定见 theseus 技能。

## 流程

1. **通读现状**：读 `<slug>/<slug>.md` 全文（含「变更记录」章节），向用户复述当前画像要点，确认这是优化基线
2. **逐章收集优化建议**：按规范格式的章节各问一次——岗位职责 / 技术要求 / 工作范围 / 知识图谱定义 / 边界，「哪一章与实际不符？缺了什么？多了什么？」
3. **追问理由**：每条优化建议追问「为什么」——理由与变更成对记录（决策与理由成对，日后可追溯）
4. **评估影响面**：给每条变更标注——纯文字章节 / 影响知识图谱（三张清单或能力草图）/ 影响技术边界
5. **落盘**：更新对应章节正文，并在「变更记录」章节追加一行 `YYYY-MM-DD 变更点 —— 理由`（每行一条）
6. **确认闭环**：复述全部变更，经用户确认后才生效；影响知识图谱的变更 → 移交 plan-config 更新 `configs/` 与 .tfo

## 纪律

- 一次只改用户明确提出的点，不顺手「优化」其它章节
- 变更记录不删旧条目——画像演化史是「为什么现在这样」的依据
- 优化建议只记「要改什么 + 为什么」，落成什么配置是 plan-config 的活，本技能不越界
- 若变更记录超长，按规范格式的卫生规则压缩为「历史摘要」（TODO: 归档阈值与流程待细节阶段定）

## Resources

- theseus 技能的 `references/persona-format.md` — 画像规范格式（本技能按它读写）
""",
    },
)
