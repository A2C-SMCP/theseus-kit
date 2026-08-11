"""Skill: analyze-config — 分析当前 TFRobot 配置。

Entry-point skill for understanding what exists on the robot. Teaches the LLM
the progressive-disclosure exploration pattern: summary → LLMTEXT → navigate
→ detail → cross-validate.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="analyze-config",
    description="分析 TFRobot 当前配置：确认目标 → 全局概览 → LLMTEXT 技术选型 → 制定优化大纲。",
    content={
        "SKILL.md": """---
name: analyze-config
description: 浏览和分析 TFRobot 当前配置 —— 确认分析目标、获取全局概览、结合 LLMTEXT 制定优化方案。
---

# 分析机器人配置

你正在通过 `theseus-kit` 分析 TFRobot 配置。本技能覆盖三阶段分析路径：

1. **确认分析目标** — 分析终归需要一个目标。必要时使用 `AskUserQuestion` 向用户确认
2. **获取全局概览** — 了解当前配置状态和拓扑结构
3. **结合 LLMTEXT 制定优化大纲** — 了解各技术选型的特点，决策使用或调整什么 Factory

## 标准分析流程

### 第 1 步：确认分析目标

分析终归需要一个目标。在与用户沟通的初期，使用 `AskUserQuestion` 向用户确认：

- 用户想了解什么？当前配置整体状况、某个具体模块、还是性能瓶颈？
- 用户的业务目标是什么？优化回答质量、添加新能力、还是排查问题？
- 如果不确定，问得越具体越好——「你更关心回答风格、模型成本、还是工具调用链？」

如果用户表述模糊，引用 `references/needs-extraction.md` 中的结构化提问模板，帮用户将模糊意图转化为具体的分析维度。

**不要跳过这一步直接读取配置**——没有目标的分析会浪费上下文窗口且无法给出有价值的结论。

### 第 2 步：全局概览

调用 `get_config_summary()`。返回：

- `robot_identity` — 机器人 ID、TFRobotServer 版本
- `states.draft` — 草稿是否存在、状态（clean/dirty/publishing）、根定位符
- `states.online` — 已发布配置信息
- `states.template` — 模板数量

**关于 dirty 状态**：`status == "dirty"` 表示当前存在多个根节点（多于一棵引用树），即存在孤立节点。这**不一定有害**——有些时候，用户会保留一些常用配置又不想存储为 Template，会选择在草稿面板里以孤立节点存储。**当前草稿发布，只要 Robot 根节点唯一即可**，有其他非 Robot 根的孤立节点是无妨的。

> `get_config_summary()` 内部已整合 topology 端点（`GET /v1/factory/drafts/topology`）来判断 dirty 状态。通过 topology 可以了解整体的节点连接拓扑，有助于审视配置合理性——你可以在此时就注意到某些模块的引用链问题。

### 第 3 步：结合 LLMTEXT 制定优化大纲

在明确了目标和现状后，结合 LLMTEXT 了解各技术选型的特点：

1. `get_llms_doc(path="")` → 返回索引页，列出所有可用文档
2. `get_llms_doc(path="guides/factory-catalog.md")` → 了解有哪些 Scene 和 Factory
3. 根据分析目标，拉取相关 Factory 页面了解字段和约束

详见子资源 `references/llmtext-strategy.md`。

> ⚠️ LLMTEXT 目前可能不完整。如果读到不合理或找不到的文档，**打断用户**请求 TFRobotServer 工程师修复——修复可以立即发版。

在本步骤中，你需要完成关键决策：

- **选择或调整 Factory**：根据 LLMTEXT 描述的各 Factory 能力边界，判断当前配置中使用的 Factory 是否最合适，是否需要切换到其他 Factory 来达到用户预期
- **制定优化大纲**：将分析目标映射为具体的配置变更计划。例如「当前用 Factory A，字段 X 设为 Y，考虑到你的目标 Z，建议切换到 Factory B 并调整字段 W」

### 后续步骤（按需深入）

根据优化大纲的需要，继续深入细节：

- **导航配置树**：用 `list_config_nodes` 逐层探索，从 `tcfg:draft` → scene → factory → setting
- **读取配置详情**：用 `get_config_detail` 获取具体节点内容。注意 `content_hash`（后续编辑时用于乐观并发控制）、`truncated`（截断时按 `next_actions` 钻取）、`redacted`（敏感字段自动脱敏）
- **交叉验证**：将实际配置值与 LLMTEXT 描述的字段语义对比——配置是否符合模块的设计意图？如果发现不一致，在报告中标注

**引用关系**：draft 状态中的引用关系信息在 `get_config_detail` 返回的 config 字段中（`setting_id` 类型的字段值即引用），topology 端点则提供全局的连接关系视图。

## LLMTEXT 集成策略

| 你想了解什么 | 读哪个页面 |
|-------------|-----------|
| 有哪些 Scene 和 Factory | `guides/factory-catalog.md` |
| 某个 Factory 的字段和约束 | `factories/{Scene}/{FactoryName}.md` |
| 配置修改的通用约定 | `guides/config-workflow.md` |
| 三态语义（draft/online/template） | `guides/lifecycle.md` |
| 所有 API 端点 | `api-reference.md` |

## 子资源

- `references/llmtext-strategy.md` — LLMTEXT 深度使用策略与技术选型决策
- `references/needs-extraction.md` — 引导用户精准表达分析需求

## 关键约束

- **每次读取都是实时的**——没有缓存。如果中间发生了变更，重新读取。
- **Schema 是运行时的**——不要依赖记忆中的字段，始终查 LLMTEXT。
- **8 KiB 默认 / 32 KiB 硬上限**——大配置需要多次缩小范围的读取。
- **敏感字段自动脱敏**——密码、令牌、密钥永远不会出现在输出中。
""",
        "references/llmtext-strategy.md": """# LLMTEXT 使用策略与技术选型

LLMTEXT 是 TFRobotServer 在运行时渲染的配置文档——**所见即当前版本的真实能力**。它在分析流程的第 3 步发挥核心作用：了解各 Factory 的能力边界，决策使用或调整什么 Factory 来达到用户预期。

## 四页核心地图

| 页面 | 路径 | 内容 |
|------|------|------|
| 索引 | `""` (空路径) | 版本信息 + 文档地图 + 通用约定 |
| Factory 目录 | `guides/factory-catalog.md` | 全部 Scene 和 Factory 清单（72 个组件） |
| 工作流指南 | `guides/config-workflow.md` | 鉴权、大小写、错误语义等通用约定 |
| 生命周期 | `guides/lifecycle.md` | Draft/Online/Template 三态语义与流转 |

## 渐进读取策略

1. **永远先拉索引**：`get_llms_doc(path="")` 了解版本和可用页面
2. **通览 Factory 目录**：`get_llms_doc(path="guides/factory-catalog.md")` 了解全局能力版图
3. **按目标深度对比**：根据分析目标，拉取 2-3 个候选 Factory 的详情页，对比它们的字段差异和能力边界
4. **做出选型决策**：基于 LLMTEXT 描述的字段约束和各 Factory 的适用场景，决定使用或调整哪个 Factory

## 从 LLMTEXT 到技术选型

以典型场景为例：

```
用户目标：「我想让机器人回答更专业、更有领域深度」

1. get_llms_doc("") → 确认版本，了解有哪些文档页面
2. get_llms_doc("guides/factory-catalog.md") → 定位到 LLM scene，发现 GLM草稿、DeepSeek草稿等多个 Factory
3. get_llms_doc("factories/LLM/GLM草稿.md") → 了解 GLM 草稿的字段：model、temperature、systemPrompt 等
4. get_llms_doc("factories/LLM/DeepSeek草稿.md") → 对比 DeepSeek 的字段差异
5. 结合第 1 步确认的目标 → 「你要的领域深度，DeepSeek 的 maxTokens 更大、temperature 可调范围更广，建议用 DeepSeek草稿」
```

## 交叉验证

- LLMTEXT 说的字段约束 vs 实际 `get_config_detail` 返回的配置值
- 如果实际配置中出现了 LLMTEXT 未文档化的字段，标注为潜在风险
- 如果 LLMTEXT 描述了某字段但实际配置中缺失，确认是否是默认值生效

## LLMTEXT 的当前局限

- 部分 Factory 的 detail 页面内容可能不完整
- 字段描述可能不够精确
- 引用关系的文档可能缺失

**遇到上述情况，中断会话向用户报告**。用户可安排 TFRobotServer 即时修复发版。
""",
        "references/needs-extraction.md": """# 引导用户精准表达分析需求

用户通常用业务语言描述需求，而不是配置术语。LLM 的任务是在分析流程的**第 1 步**就将模糊需求转化为可操作的分析目标。配合 `AskUserQuestion` 使用效果最佳。

## 核心原则

**不要猜。问。** 当用户说「帮我看看配置有没有问题」时，你不知道他关心的是性能、成本、安全还是功能正确性。用 `AskUserQuestion` 给出 2-3 个精准选项，让用户选择。

## 结构化提问层次

### 第一层：确定分析意图

| 用户原始表述 | 拆解后的分析维度 | 追问示例 |
|-------------|-----------------|---------|
| 「配置有没有问题」 | 性能 / 安全 / 引用完整性 | 「你更关心回答速度、调用链是否正确、还是有没有安全漏洞？」 |
| 「机器人不太对」 | 输出质量 / 行为异常 / 配置漂移 | 「是回答内容有问题，还是某个功能不工作了？」 |
| 「帮我优化一下」 | 成本 / 延迟 / 质量 | 「优先目标是降低 Token 消耗、加快响应速度、还是提升回答专业度？」 |

### 第二层：确定功能域（Scene）

用户说「我想让机器人做 X」→ 判断 X 属于哪个功能域：

| 用户需求关键词 | 可能的功能域 (Scene) |
|---------------|---------------------|
| 换模型、加 AI、改 LLM | LLM |
| 加工具、改搜索、改 MCP | TOOL |
| 改思维链、加推理步骤 | CHAIN / CHAINS |
| 改记忆、加 RAG、改召回 | MEMORY / DOC_STORE |
| 改提示词、改 System Prompt | PROMPT |
| 改知识图谱、本体 | GRAPH_DB |
| 改 Embedding 模型 | EMBEDDING |
| 改大脑逻辑 | BRAIN |
| 改驱动方式 | DRIVE |

### 第三层：提取配置维度

从用户描述中提取具体参数：

- **数值型**：「温度调到 0.8」「最多返回 10 条」
- **选择型**：「用 V3 模型」「换成 Bing 搜索」
- **开关型**：「开启流式输出」「关闭缓存」
- **引用型**：「用 ask同事brain 作为大脑」

## 示例对话

```
用户：我想让机器人回答更专业一点

LLM：（使用 AskUserQuestion）
  问题：你希望从哪个维度提升专业性？
  选项 1：调整 System Prompt（改变人设和回答风格）
  选项 2：更换 LLM 模型（不同模型的知识深度不同）
  选项 3：降低 Temperature（让输出更确定、更专注）

用户选择：选项 2

LLM：好的，我们从 LLM 模型选型入手。
  我先看一下当前配置概览，然后对比可用的 Factory 选项。
  [调用 get_config_summary() + get_llms_doc("guides/factory-catalog.md")]
```
""",
    },
)
