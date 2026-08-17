"""Skill: plan-config — 阶段二：画像 → 配置修改计划。

The middle tier.  Converts the confirmed persona into structured config
artifacts under ``configs/`` (via write-tfonto for the .tfo and topic YAMLs
otherwise), surveys the real configuration through the progressive-disclosure
tools (summary → nodes → detail → value, never full-tree reads), tracks
progress with SubTasks, and freezes one plan file under ``plans/`` for the
apply stage.  The two heaviest details — progressive disclosure of the real
config and of the TFOnto ontology via TFS Action — are marked TODO for a
dedicated follow-up.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="plan-config",
    description="阶段二——把已确认的机器人画像转换为结构化配置并固化修改计划：产出 configs/ 工件，技术要求 → MCP/SKILL/Plugin 选型（与用户商议），渐进披露调研真实配置，SubTask 追踪进度，落一份 plans/ 计划文件。",
    content={
        "SKILL.md": """---
name: plan-config
description: 阶段二——把已确认的机器人画像转换为结构化配置并固化修改计划：调用 write-tfonto 等原子技能产出 configs/ 工件，把技术要求转换为 MCP/SKILL/Plugin 选型并与用户商议确认，借助渐进披露调研当前真实配置（先拓扑后展开），用 SubTask 追踪进度，最终落一份 plans/ 计划文件。当阶段一画像已确认、需要落实配置修改时使用。
---

# Plan Config —— 画像 → 配置修改计划

## 输入 / 输出

- 输入：`<slug>/<slug>.md`（只消费经用户确认的内容，含「变更记录」中的未落实变更）
- 输出：`configs/` 结构化工件（.tfo / YAML / JSON，含工具链选型 `toolchain.yaml`）+ `plans/<YYYY-MM-DD>-<主题>-in-progress.md` 计划文件（文件名带状态，plans/ 至多一份进行中）

## 流程

1. **建 SubTask 清单**：先把画像 → 配置的全部工作拆成子任务（每项一个可勾销的产出物），逐项勾销；过程中发现新子任务随时补入——干到一半也不丢进度、不漏任务
2. **知识图谱转换**：三张清单 + 状态流转 + 能力草图 → **write-tfonto** 技能 → `configs/<slug>.tfo`；写完必须过 `scripts/validate_tfonto.py`（write-tfonto 收稿门槛）
3. **工具链选型**（自然语言技术要求 → MCP/SKILL/Plugin）：画像「技术要求」章节的工具需求是自然语言描述，本步骤把它们转换为专业选型——按三个来源逐项调研，**每项与用户商议确认选型与理由**：
   - 官方 Marketplace（`github.com/A2C-SMCP/tfrobot-marketplace`）：先查现成 plugin——当前生态尚在起步，查不到就记「无现成」
   - 开放世界 MCP：WebSearch 检索，要求**高 Star、风评好**，记仓库地址、Star 数、维护活跃度
   - SKILL 类资产：WebSearch 吸收思路后**按我们的 SKILL 格式转换**（SKILL.md + references/ + scripts/，大小与保密门槛）——原样不能直接安装
   - 用户可提供**自研 Plugin / MCP**，纳入候选一并评估
   - 选型清单落工件 `configs/toolchain.yaml`（需求 → 选型 → 理由 → 来源）；**用户确认后才进计划文件**，未确认不写
4. **其余配置转换**：岗位职责 / 工作范围 → 对应配置项的 YAML/JSON 工件（命名 `configs/<主题>.yaml`），字段形状以目标配置项为准
5. **渐进披露调研**：当前真实配置通过 MCP 工具读取——先 `get_config_summary` 看拓扑，再 `list_config_nodes` 展开节点列表、`get_config_detail` 展开**有必要编辑**的节点取字段与具体值。逐步展开，绝不整树读入
6. **复杂配置写脚本**：需要批量/结构化处理时写脚本（TFOnto 校验即先例）；脚本放工作区 `scripts/`、临时产物放 `tmp/`，执行前经用户确认，报错迭代放进宿主系统的隔离上下文（布局与隔离机制见 theseus 的 `references/workspace-layout.md` / `references/context-isolation.md`；A2C 的 `${TFROBOT_SKILL_DIR}` 只适用于技能包内脚本）
7. **固化计划**：落盘前先查 plans/——已有未完成计划须先完成（或经用户确认强制完成），**不允许并行修改**；按 theseus 技能的 `references/plan-format.md` 规范格式产出 `plans/<YYYY-MM-DD>-<主题>-in-progress.md`，供阶段三逐条执行

## 渐进披露（本技能最关键的两处细节，TODO）

- **真实配置的渐进披露**（流程步骤 5）：拓扑 → 展开的具体操作序列、上下文预算的逐级用法（8 KiB 默认 / 32 KiB 硬上限）、draft/template/online 三态的选择时机**待单独讨论后固化**；过渡期以 analyze-config 技能的 LLMTEXT 策略为参照。上下文管理模式已定：大体积调研放进宿主系统的隔离机制（theseus/references/context-isolation.md），调研结论物化回工作区
- **TFOnto 本体的渐进披露**：当前本体结构定义如何借助 TFS Action 渐进式披露，定位「哪里需要改、如何改」，步骤化处理的细节**待单独讨论后固化**；过渡期以 write-tfonto 的校验器 + 两个范例为参照

## Plan 文件格式

已固化：theseus 技能的 `references/plan-format.md`——7 章节 + 操作序列条目表（产出物粒度、状态回写、失败即停）。

## 交接

计划文件经用户确认后交 **apply-config-plan**（阶段三）。本技能不落 Draft、不发布。
""",
    },
)
