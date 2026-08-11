"""Skill: manage-topology — 管理配置拓扑结构。

Guides the LLM through creating new configuration nodes, deleting obsolete ones,
and modifying reference relationships (topology links).  This is about *structure*
— which nodes exist and how they connect — not about tuning individual field values.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="manage-topology",
    description="管理 TFRobot 配置拓扑：创建节点 → 删除节点 → 修改引用关系。结合 LLMTEXT 进行技术选型。",
    content={
        "SKILL.md": """---
name: manage-topology
description: 管理 TFRobot 配置拓扑结构 —— 新建节点、删除节点、建立/修改引用关系。
---

# 管理配置拓扑

你正在通过 `theseus-kit` 管理 TFRobot 配置的**拓扑结构**：哪些节点存在、它们之间如何连接。
本技能关注的是**结构层**操作——创建、删除、引用关系。节点内部的字段值调优请使用 `tune-config` 技能。

## 拓扑操作一览

| 操作 | 工具 | 说明 |
|------|------|------|
| 新建节点 | `create_draft` | 在指定 Scene 下用指定 Factory 创建新节点 |
| 删除节点 | REST `DELETE /v1/factory/drafts/delete` | 移除不再需要的节点 |
| 修改引用 | `update_draft`（只改 `*_setting_id` 字段） | 改变节点之间的连接关系 |

> **与 `tune-config` 的分工**：改引用字段（谁指向谁）→ 本技能；改值字段（temperature、model、max_tokens 等参数）→ `tune-config`。

## 标准操作流程

### 新建节点

#### 第 1 步：确定 Scene + Factory（技术选型）

1. 如果还没有分析过当前配置，先用 `analyze-config` 了解全局状态
2. 调用 `get_llms_doc(path="guides/factory-catalog.md")` 浏览可用的 Factory
3. 调用 `get_llms_doc(path="factories/{Scene}/{FactoryName}.md")` 了解候选 Factory 的能力边界
4. 调用 `get_config_summary(state="draft")` 检查是否已有同类节点

详见子资源 `references/factory-selection.md`。

#### 第 2 步：创建骨架

```python
create_draft(
    scene="LLM",                    # 功能域（LLM、TOOL、BRAIN 等）
    factory_name="DeepSeek草稿",     # Factory 类名（来自 LLMTEXT factory-catalog，区分大小写）
    setting_name="my-deepseek",     # 用户分配的实例名
    config={}                        # 可选，初始配置（不传则创建空骨架）
)
```

返回 `setting_id` 和 `content_hash`——后续 `update_draft` 需要用到。

#### 第 3 步：建立引用关系

新节点通常需要连接到现有配置树。引用关系的建立通过 `update_draft` 修改 `*_setting_id` 字段来实现：

1. 用 `list_config_nodes` + `get_config_detail` 找到被引用节点的 `setting_id`
2. 调用 `update_draft(setting_id, setting_name, config={"brain_setting_id": 8}, expected_hash=...)`

引用字段的特征：字段名以 `_id` 或 `_setting_id` 结尾，值是纯数字。

> 非引用字段（如 `temperature`、`model`）的填充不在本技能范围，切换到 `tune-config`。

### 删除节点

删除前**必须**做三项检查：

1. **确认内容**：`get_config_detail(locator=...)` 确认要删的是什么
2. **检查被引用**：通过 topology（`get_config_summary` 内部已整合）确认没有其他节点引用此节点——否则会产生 dangling reference
3. **用户确认**：向用户明确展示要删除的节点信息，获得确认后再执行

删除通过 `POST /v1/factory/drafts/delete` 完成，删除后调用 `get_config_summary` 验证。

### 修改引用关系

当需要改变节点之间的连接（如 LLM 换一个 Brain、Tool 换一个 Memory）时：

1. `get_config_detail` 读取当前节点的引用字段值
2. `list_config_nodes` 找到新的被引用目标
3. `update_draft` 只修改引用字段（如 `{"brain_setting_id": 12}`），传入 `expected_hash`

> ⚠️ 改引用时不要同时改值字段——将结构变更和参数调优分开，便于冲突处理和问题定位。

## LLMTEXT 集成

本技能使用 LLMTEXT 的**技术选型**视角：

| 决策点 | 读什么 |
|--------|--------|
| 不确定用哪个 Factory | `guides/factory-catalog.md` |
| 对比候选 Factory 的能力 | 分别拉取各 Factory 的详情页 |
| 了解 Factory 之间的引用关系模式 | 读已有同类配置的 `get_config_detail` 作为参考 |
| 不确定引用字段的目标类型 | Factory 详情页中的字段类型标注 |

## 子资源

- `references/factory-selection.md` — Factory 选型指南：Scene 用途、引用关系模式、常见坑

## 关键约束

- **先读 LLMTEXT 再决定 Factory**——不要凭记忆猜 Factory 名和能力。
- **区分结构和参数**——引用字段（拓扑）在本技能处理，值字段（参数）在 `tune-config` 处理。
- **删除前必须检查引用**——topology 确认无 dangling reference。
- **Factory 名区分大小写**——LLMTEXT 中显示什么就用什么。
""",
        "references/factory-selection.md": """# Factory 选型指南

## Scene（功能域）速查

| Scene | 用途 | 常见 Factory 数量 |
|-------|------|------------------|
| ROBOT | 机器人根节点——每个草稿有且仅有一个 | 1 |
| LLM | 大语言模型后端配置 | 多个 |
| BRAIN | 大脑逻辑（路由、决策） | 多个 |
| TOOL | 工具配置（搜索、MCP、API） | 多个 |
| CHAIN / CHAINS | 思维链/处理链 | 多个 |
| PROMPT | 提示词模板 | 多个 |
| MEMORY | 记忆与上下文管理 | 多个 |
| DOC_STORE | 文档/RAG 存储 | 多个 |
| EMBEDDING | 向量嵌入模型 | 多个 |
| GRAPH_DB | 知识图谱 | 多个 |
| DRIVE | 驱动方式配置 | 多个 |

> 完整列表以 LLMTEXT `factory-catalog.md` 为准——以上只是常见分类。

## Factory 选型决策树

```
用户目标
  → 属于哪个 Scene？（查上表或 factory-catalog）
    → 该 Scene 下有哪些 Factory？（factory-catalog）
      → 每个候选 Factory 的字段差异是什么？（拉取各 Factory 详情页对比）
        → 哪个最匹配用户需求？
```

## 引用关系模式

TFRobot 配置的核心是**引用链**。理解常见的引用模式有助于正确建立拓扑：

- **ROBOT → BRAIN**：机器人根节点引用大脑
- **BRAIN → LLM + TOOL + MEMORY**：大脑引用模型、工具、记忆
- **CHAIN → PROMPT + LLM**：链引用提示词和模型
- **TOOL → MCP_SERVER**：工具引用 MCP 服务器配置

## 常见坑

1. **单例 Factory**：某些 Factory（如 ROBOT）只能有一个实例——创建第二个会失败
2. **必填引用**：创建节点后，其必填引用字段必须立即设置，否则 `validate_draft` 失败
3. **循环引用**：A 引用 B、B 引用 A 会被拒绝
4. **跨 Scene 引用**：大多数引用有 Scene 限制——LLM 不能引用一个 TOOL 作为 brain

不确定时，读已有同类配置的 `get_config_detail` 作为参考。
""",
    },
)
