"""Skill: theseus — 配置工作流总纲（三阶段调度入口）。

The outermost orchestrator skill.  It does not touch configuration directly:
it partitions the whole "机器人画像 → 线上配置" workflow into three stages
and dispatches each stage to its dedicated skill (stage 1: persona-interview /
persona-optimize; stage 2: plan-config; stage 3: apply-config-plan), with the
per-robot workspace under ``~/.theseus/workspaces/<slug>/`` as the shared
handoff medium.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="theseus",
    description="TFRobot 配置工作流总纲——三阶段流水线（机器人画像 → 配置修改计划 → 落地发布）的统一调度入口。",
    content={
        "SKILL.md": """---
name: theseus
description: TFRobot 配置工作流总纲——三阶段流水线（机器人画像 → 配置修改计划 → 落地发布）的统一调度入口。当用户发起「为当前机器人建画像 / 调整画像 / 改配置 / 调整本体」等整体性工作时，先读本技能，再按阶段分派到 persona-interview / persona-optimize / plan-config / apply-config-plan 与各原子技能。
---

# Theseus —— 配置工作流总纲

本技能不直接操作配置，只做**调度**：把「机器人画像」到「线上配置」的整体工作切分为三阶段，每阶段由专门技能承接。阶段之间的交接物是工作目录里的文件——画像、配置工件、计划文件，谁接手都能从文件续上。

（TODO: 跨阶段上下文交接与 SubAgent 使用模式待单独讨论）

## 工作目录约定

- 根目录：`~/.theseus/workspaces/`（机器人工作区，按需创建）
- 每个机器人一个子目录，目录名 = 当前配置 `THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID` 的 slug 化（`:` → `-`，仅保留 `[A-Za-z0-9._-]`），如 `org:10001` → `org-10001`
- 布局：

```text
~/.theseus/workspaces/<slug>/
  <slug>.md          # 机器人画像（规范格式见 references/persona-format.md）
  configs/           # 结构化配置工件（.tfo / YAML / JSON）
  plans/             # 修改计划文件（一次编辑一份）
```

## 阶段一：机器人画像

- 无画像（`<slug>/<slug>.md` 不存在）→ **persona-interview** 技能采访创建
- 已有画像、用户要调整 → **persona-optimize** 技能增量优化，变更写入画像「变更记录」章节
- 画像按规范格式组织，**最终与用户确认并评审**后才进入阶段二；未确认的章节不参与配置转换

## 阶段二：配置修改计划

**plan-config** 技能：把画像转换为 `configs/` 结构化工件（.tfo 等），借助渐进披露调研当前真实配置，用 SubTask 追踪进度，固化一份 `plans/` 计划文件。（渐进披露与 TFOnto 本体的细节见该技能 TODO）

## 阶段三：落地发布

**apply-config-plan** 技能：按计划逐条执行，落到 Draft，与用户商议 Template 与发布——发布是显式不可逆动作。

## 安全不变量

- 凭证只存在 MCP 服务器进程内：画像、配置工件、计划文件中禁止出现任何令牌形态（PAT、JWT 等）
- 发布必须与用户商议确认（publish-config 技能的 ack 门槛），不会作为编辑的副作用触发

## Resources

- `references/persona-format.md` — 机器人画像规范格式（阶段一交付物标准，含「变更记录」章节约定）
""",
        "references/persona-format.md": """# 机器人画像规范格式（persona.md）

> 阶段一交付物的唯一规范。`<slug>.md` 按本格式组织，最终与用户确认并评审后生效；阶段二只消费经确认的内容。

## 标准章节

1. **机器人定位** — 一句话：机器人代号、面向谁、解决什么场景
2. **岗位职责** — 机器人承担的职责清单（可观察行为，拒绝口号式表述）
3. **技术要求** — 必须掌握的工具/规范/标准；每个技能标注能力形态（只读计算 / 写入操作），写入操作顺手标注「一次改几处」（连带变更）
4. **工作范围** — 领域上下位归属、明确**不做什么**（边界）、日常工作频次与输入输出、行话/黑话
5. **知识图谱定义（TFOnto）** — 三张清单（概念 / 数据字段带类型 / 关系带方向与触发词）+ 状态流转（什么动作触发）+ 能力草图（Function/Action 候选与形态）；转换为 .tfo 后放 `configs/`，画像内保留清单与文件链接，不重复粘贴 .tfo 全文
6. **变更记录** — 优化采访产生的每条变更一行：`YYYY-MM-DD 变更点 —— 理由（经用户确认）`。新建画像时此章节初始为空；历史条目不删除（画像演化史）；若超过约 50 行，压缩旧条目为「历史摘要」段落、保留最近 20 条明细（TODO: 归档阈值与流程待细节阶段定）

## 与旧画像示例的对应

persona-interview 技能的 `references/persona-example.md` 五部分结构是本格式的历史版本，对应关系：定位 → 岗位职责；专业技能 → 技术要求；职业领域 + 日常工作 → 工作范围；知识结构 + 能力草图 → 知识图谱定义。

（TODO: persona-example.md 与 persona-interview 采访步骤向本格式的对齐在细节阶段落实）

## 评审

画像成稿后与用户逐章确认；确认通过才进入阶段二。未确认的章节不参与配置转换。
""",
    },
)
