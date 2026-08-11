"""Skill: create-config — 创建全新 TFRobot 配置。

Guides the LLM through creating a new configuration from scratch: understand the
user's goal → consult LLMTEXT for available factories → create a draft skeleton
→ fill in fields → validate.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="create-config",
    description="创建全新 TFRobot 草稿配置：需求分析 → LLMTEXT 选型 → create_draft → 填字段 → 校验。",
    content={
        "SKILL.md": """---
name: create-config
description: 从零开始创建 TFRobot 草稿配置 —— 确定 Scene 和 Factory、创建骨架、填充字段、建立引用、校验。
---

# 创建新配置

你正在通过 `theseus-kit` 创建全新的 TFRobot 草稿配置。本技能覆盖**从需求到可校验配置**的完整创建路径。

这是配置管理中最常见的操作——无论是给机器人换一个新 LLM 后端、加一个搜索工具、还是新建一个思维链，都从这里开始。

## 标准创建流程

### 第 1 步：理解需求 → 确定 Scene + Factory

1. 用 `analyze-config` 技能中的需求引导方法（`references/needs-extraction.md`）与用户沟通
2. 调用 `get_llms_doc(path="guides/factory-catalog.md")` 浏览可用的 Factory
3. 调用 `get_llms_doc(path="factories/{Scene}/{FactoryName}.md")` 了解目标 Factory 的字段定义
4. 调用 `get_config_summary(state="draft")` 检查是否已有同类配置

### 第 2 步：创建骨架

```python
create_draft(
    scene="LLM",              # 功能域（如 LLM、TOOL、BRAIN）
    factory_name="DeepSeek草稿",  # Factory 类名（来自 LLMTEXT factory-catalog）
    setting_name="my-deepseek",  # 用户分配的实例名（自定义，建议有辨识度）
    config={}                     # 可选，初始配置（不传则创建空骨架）
)
```

返回的 `content_hash` 保存好——后续 `update_draft` 需要用。

### 第 3 步：填充字段

1. 用 `get_config_detail(locator="tcfg:draft/{Scene}/{Factory}/{setting_id}")` 查看当前骨架
2. 根据 LLMTEXT 中的字段定义，构造 `config` 字典
3. 关键字段类型处理：
   - **普通值**：`{"model": "deepseek-v3", "temperature": 0.7}`
   - **引用字段**（引用另一个 setting）：`{"brain_setting_id": 8}` —— 先通过 `list_config_nodes` 找到目标节点的 setting_id
   - **列表/嵌套对象**：按 LLMTEXT 中的结构填充
4. 调用 `update_draft(setting_id, setting_name, config, expected_hash=...)`

引用 `references/field-design.md` 了解字段设计的详细指南。

### 第 4 步：建立引用关系

配置节点之间的引用关系形成了配置树。如果新节点需要引用其他节点（如 LLM 引用 Brain、Tool 引用 Memory）：

1. 用 `list_config_nodes` + `get_config_detail` 找到被引用节点的 `setting_id`
2. 在 `update_draft` 的 config 中设置引用字段为该 `setting_id`
3. 创建后调用 `get_config_summary(state="draft")` 确认新节点是否正确连入了引用树

### 第 5 步：校验

```python
validate_draft(setting_id=<new_id>)
```

如果校验失败，参照 `update-config` 技能中的 `references/validation-strategy.md` 排查。

## LLMTEXT 集成

| 决策点 | 读什么 |
|--------|--------|
| 不确定用哪个 Factory | `guides/factory-catalog.md` |
| 需要了解 Factory 的字段 | `factories/{Scene}/{FactoryName}.md` |
| 不确定引用关系模式 | 读已有同类配置的 `get_config_detail` 作为参考 |
| 不清楚字段类型/约束 | 具体 Factory 的 LLMTEXT 页面 |

## 子资源

- `references/field-design.md` — 字段设计与引用关系深入指南

## 关键约束

- **先读 LLMTEXT 再创建**——不要凭记忆猜字段名和约束。
- **先创建骨架再填字段**——`create_draft` → `update_draft` 两步走。
- **保存 content_hash**——`create_draft` 返回的 hash 用于后续 `update_draft` 的乐观并发控制。
- **创建后必须校验**——`validate_draft` 是新配置上线前的必要步骤。
- **Factory 名区分大小写**——LLMTEXT 中显示什么就用什么。
""",
        "references/field-design.md": """# 字段设计与引用关系

## 从 LLMTEXT 提取字段约束

每个 Factory 的 LLMTEXT 页面描述了其配置字段。阅读时关注：

1. **字段名**——必须与 LLMTEXT 中的 key 完全一致
2. **类型**——str / int / float / bool / list / dict / 引用
3. **必填 vs 可选**——缺少必填字段会导致 `validate_draft` 失败
4. **取值范围**——枚举值、数值范围、正则约束
5. **默认值**——不传时的行为

## 引用类型字段

配置节点之间的引用是 TFRobot 配置的核心机制。识别引用字段的方法：

1. 字段名以 `_id` 或 `_setting_id` 结尾 → 通常是引用
2. LLMTEXT 中标注类型为 "reference" 或 "setting_id"
3. 值的格式是纯数字（如 `8` 或 `"8"`）

### 建立引用的步骤

```
1. list_config_nodes(parent="tcfg:draft/{target_scene}") → 找到目标节点
2. 从 locator 中提取 setting_id（如 tcfg:draft/BRAIN/RobotBrainDraftSetting/8 → id=8）
3. 在 update_draft 的 config 中设置：{"brain_setting_id": 8}
```

### 引用完整性

- 引用链条不能断——被引用的节点必须在 draft 中存在
- 不能自引用——节点不能引用自己
- 循环引用通常被 TFRobotServer 拒绝

## 嵌套对象和列表

有些 Factory 的 config 结构比较复杂：

```json
{
  "endpoints": [
    {"name": "primary", "url": "https://...", "api_key": "..."},
    {"name": "fallback", "url": "https://..."}
  ],
  "retry": {"max_retries": 3, "backoff": "exponential"}
}
```

LLMTEXT 应描述这些嵌套结构的 schema。如果不清楚，读已有的同类配置作为参考。

## 敏感字段

密码、API Key、Token 等敏感字段会被 theseus-kit 自动脱敏（`get_config_detail` 中显示为 `<<redacted>>`）。创建时正常传入，脱敏仅影响读取展示。
""",
    },
)
