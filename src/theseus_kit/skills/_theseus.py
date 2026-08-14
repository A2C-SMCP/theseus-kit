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

## 上下文隔离与交接（宿主系统无关）

阶段之间的交接物是工作区文件；大体积调研、脚本迭代等噪音大的环节，用**宿主 Agent 系统自己的上下文隔离机制**分担（Claude Code 的 subagent、Codex 的 subagent、TFRobotServer 的 Chain）——Theseus 只规定「何时隔离、隔离什么」，不绑定实现。详见 `references/context-isolation.md`。

## 工作目录约定

- 根目录：`~/.theseus/workspaces/`（机器人工作区，按需创建）
- 每个机器人一个子目录，目录名 = 当前配置 `THESEUS_CREDENTIAL__ROBOT_PUBLIC_ID` 的 slug 化（`:` → `-`，仅保留 `[A-Za-z0-9._-]`），如 `org:10001` → `org-10001`
- 布局：

```text
~/.theseus/workspaces/<slug>/
  <slug>.md          # 机器人画像（规范格式见 references/persona-format.md）
  configs/           # 结构化配置工件（.tfo / YAML / JSON）
  plans/             # 修改计划文件（一次编辑一份）
  scripts/           # 本机器人专用脚本（复杂转换 / 校验）
  tmp/               # 临时产物（可随时清空，不进入交接）
```

- 布局细则（configs/ 工件命名与校验门槛、plans/、scripts/、tmp/）见 `references/workspace-layout.md`

## 阶段一：机器人画像

- 无画像（`<slug>/<slug>.md` 不存在）→ **persona-interview** 技能采访创建
- 已有画像、用户要调整 → **persona-optimize** 技能增量优化，变更写入画像「变更记录」章节
- 画像按规范格式组织，**最终与用户确认并评审**后才进入阶段二；未确认的章节不参与配置转换

## 阶段二：配置修改计划

**plan-config** 技能：把画像转换为 `configs/` 结构化工件（.tfo 等），把技术要求转换为 MCP/SKILL/Plugin 选型（与用户商议），借助渐进披露调研当前真实配置，用 SubTask 追踪进度，固化一份 `plans/` 计划文件。（渐进披露与 TFOnto 本体的细节见该技能 TODO）

## 阶段三：落地发布

**apply-config-plan** 技能：按计划逐条执行，落到 Draft，与用户商议 Template 与发布——发布是显式不可逆动作。

## 安全不变量

- 凭证只存在 MCP 服务器进程内：画像、配置工件、计划文件中禁止出现任何令牌形态（PAT、JWT 等）
- 发布必须与用户商议确认（publish-config 技能的 ack 门槛），不会作为编辑的副作用触发

## Resources

- `references/persona-format.md` — 机器人画像规范格式（阶段一交付物标准，含「变更记录」章节约定）
- `references/workspace-layout.md` — 工作区布局细则：configs/ 工件命名与校验门槛、plans/、scripts/、tmp/
- `references/context-isolation.md` — 上下文隔离与交接：隔离点位 + 各宿主系统指引（Claude Code / Codex / TFRobotServer Chain）
""",
        "references/persona-format.md": """# 机器人画像规范格式（persona.md）

> 阶段一交付物的唯一规范。`<slug>.md` 按本格式组织，最终与用户确认并评审后生效；阶段二只消费经确认的内容。

## 标准章节

1. **机器人定位** — 一句话：机器人代号、面向谁、解决什么场景
2. **岗位职责** — 机器人承担的职责清单（可观察行为，拒绝口号式表述）
3. **技术要求** — 必须掌握的工具/规范/标准；每个技能标注能力形态（只读计算 / 写入操作），写入操作顺手标注「一次改几处」（连带变更）。工具需求用**自然语言**描述（如「能检索最新资料」「会几何计算」），具体装什么、从哪选，阶段二与用户商议转换——画像不出现工具选型的专业术语清单
4. **工作范围** — 领域上下位归属、明确**不做什么**（边界）、日常工作频次与输入输出、行话/黑话
5. **知识图谱定义（TFOnto）** — 三张清单（概念 / 数据字段带类型 / 关系带方向与触发词）+ 状态流转（什么动作触发）+ 能力草图（Function/Action 候选与形态）；转换为 .tfo 后放 `configs/`，画像内保留清单与文件链接，不重复粘贴 .tfo 全文
6. **变更记录** — 优化采访产生的每条变更一行：`YYYY-MM-DD 变更点 —— 理由（经用户确认）`。新建画像时此章节初始为空；历史条目不删除（画像演化史）；若超过约 50 行，压缩旧条目为「历史摘要」段落、保留最近 20 条明细（TODO: 归档阈值与流程待细节阶段定）

## 与旧画像示例的对应

persona-interview 技能的 `references/persona-example.md` 五部分结构是本格式的历史版本，对应关系：定位 → 岗位职责；专业技能 → 技术要求；职业领域 + 日常工作 → 工作范围；知识结构 + 能力草图 → 知识图谱定义。

（TODO: persona-example.md 与 persona-interview 采访步骤向本格式的对齐在细节阶段落实）

## 评审

画像成稿后与用户逐章确认；确认通过才进入阶段二。未确认的章节不参与配置转换。
""",
        "references/workspace-layout.md": """# 工作区布局设计（workspace-layout）

> 「物化数据按工作区结构策略落盘」的具体设计。根约定在总纲 SKILL.md，本文件细化 configs/ 及其余目录。

## 总览

```text
~/.theseus/workspaces/<slug>/    # slug = RobotPublicID 的 slug 化（: → -）
  <slug>.md                      # 机器人画像（规范：persona-format.md）
  configs/                       # 结构化配置工件（本文件重点）
  plans/                         # 修改计划（一次编辑一份）
  scripts/                       # 本机器人专用脚本（复杂转换 / 校验）
  tmp/                           # 临时产物（可随时清空，不进入交接）
```

## configs/ —— 结构化配置工件

定位：画像 → 真实配置转换链的第二物化层（自然语言 → Markdown → **结构化数据** → 工具调用）。来源永远是已评审画像章节；去向是 plans/ 计划引用、最终经工具调用落入 Draft。

### 命名

- 内容命名、kebab-case，不带 slug 前缀（目录本身已按机器人隔离）：`ontology.tfo`、`topology.yaml`、`tuning.yaml`
- 同名重新生成即覆盖；历史版本不另存——画像变更后重走 plan-config 重新生成，不手改旧工件硬套

### 类型与入计划门槛

| 扩展名 | 用途 | 门槛 |
|---|---|---|
| `.tfo` | TFOnto 本体（write-tfonto 产物） | `scripts/validate_tfonto.py` 通过（rc 0） |
| `.yaml` / `.json` | 其余结构化配置片段 | `yaml.safe_load` / `json.loads` 通过 |

禁止自造格式；字段形状以目标配置项契约为准。

### 工件头部标注

每个工件首行注释标注来源与校验状态，便于审计与续接：

```yaml
# 来源: 画像「知识图谱定义」章节 | 生成: 2026-08-14 | 校验: passed
```

### 生命周期

- 工件是**中间物**，与真实系统无自动同步；apply-config-plan 按 plans/ 计划执行，工件是蓝图不是参数
- 计划完成后工件保留（审计与后续迭代起点）；tmp/ 里的试算产物可清
- 未过校验门槛的工件不得被计划引用

### 安全

工件中禁止出现任何令牌形态（PAT、JWT 等）——凭证只存在于 MCP 服务器进程内。

## plans/ —— 修改计划

- 一次编辑一份：`plans/<YYYY-MM-DD>-<主题>.md`
- 计划文件按文件名引用 configs 工件；apply-config-plan 逐条回写进度
- （TODO: Plan 文件格式的字段与结构在 plan-config 细节阶段设计）

## scripts/ 与 tmp/

- `scripts/`：本机器人专用脚本（批量转换、校验）。执行前经用户确认；报错迭代放进宿主系统的隔离上下文（见 context-isolation.md）
- `tmp/`：试算、草稿、报错日志等临时产物；任何阶段不得从 tmp/ 读取交接数据

## 与画像的对应

每个 configs 工件在头部标注来源画像章节；画像「变更记录」中的未落实变更，是下一轮 plan-config 的任务输入。
""",
        "references/context-isolation.md": """# 上下文隔离与交接（宿主系统无关）

> Theseus 在哪个 Agent 系统里运行，就借那个系统的上下文隔离机制——本工作层只规定「何时隔离、隔离什么」，不规定「用什么隔离」。

## 核心立场

「SubAgent」只是 Claude Code 的一种上下文隔离手段：Codex 有同名概念，TFRobotServer 自研体系用 **Chain**。Theseus 工作层无法左右未来承载系统的实现，因此不绑定任何一家——通用性来自**交接物文件化**（画像 / configs 工件 / 计划文件），与隔离机制无关。

## 隔离点位（何时值得开新上下文）

- 阶段二的大体积调研（真实配置 / TFOnto 本体的渐进披露）——逐级展开的中间过程关进独立上下文，主会话只收结论
- 复杂脚本的编写与试跑（validate_tfonto.py 即先例）——报错迭代是噪音大头
- 单条计划的逐项执行（apply-config-plan）——进度回写计划文件，主会话按需查看

隔离任务的产出物必须落工作区文件，否则新上下文白跑。

## 各宿主系统指引

### Claude Code（当前宿主）

给用户提示后，帮其在本地创建 SubAgent 配置（`.claude/agents/*.md`，frontmatter 声明 name / description / tools / model）：

```markdown
---
name: theseus-explorer
description: Theseus 只读调研子代理。当需要在独立上下文里对 TFRobot 配置做渐进披露调研、运行校验脚本、核对计划文件时使用。只读探索，绝不写配置。
tools: Read, Grep, Glob, Bash
model: sonnet
---

你是 Theseus 阶段二的只读调研员，工作区在 ~/.theseus/workspaces/<slug>/。
职责：按 plan-config 技能的渐进披露调研步骤，结果物化到工作区文件。
纪律：不修改任何配置；凭证不出进程；产出只经工作区文件回传。
```

原则：工具集最小化（只读优先）；description 用第三方视角写清「何时使用」；model 按任务档位选。

### Codex

同样支持 subagent 概念（见官方文档），按同样原则创建：只读工具集、第三方视角描述、产出物化到工作区。

### TFRobotV2 / TFRobotServer（自研）

用**独立 Chain** 承担隔离点位的子任务（CHAIN 场景配置的 Chain 即天然的执行单元）：

- Chain **关闭过程 Trace 注入**——中间过程不向外部调用者广播事件，保持低噪音
- Chain **仍可接受外部 Trace 注入**——主会话把变更前的上下文 Trace 喂给 Chain，让子任务获得全局洞察，而非从零冷启动

（TODO: Chain 开启/关闭 Trace 注入、接受外部 Trace 的配置项与调用方式，待 TFRobotServer 恢复后基于 llms.txt 收集证据——当前 llms.txt 只渲染工厂文档（L1/L2/L3），不含 Chain 隔离契约，需上游补充。）

## 交接协议（任何机制通用）

新上下文启动第一件事：读 theseus 总纲 + 工作区画像 + 相关计划文件；结束后把结论写回工作区。**不依赖会话记忆，只依赖文件。**
""",
    },
)
