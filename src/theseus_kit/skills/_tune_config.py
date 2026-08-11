"""Skill: tune-config — 调优配置字段。

Guides the LLM through reading current field values, understanding field constraints
via LLMTEXT, safely modifying values with optimistic concurrency control, handling
validation errors, and verifying the result.

This is about *parameters* — the actual field values within a node — not about
creating/deleting nodes or changing topology.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="tune-config",
    description="调优 TFRobot 配置字段：读取现状 → 理解字段约束 → 合理化修改 → 校验 → 冲突处理。",
    content={
        "SKILL.md": """---
name: tune-config
description: 调优 TFRobot 配置字段值 —— 读取-检查-写入循环、乐观并发控制、校验错误处理。
---

# 调优配置字段

你正在通过 `theseus-kit` 调优已有 TFRobot 配置节点的**字段值**。本技能关注的是**参数层**操作——
修改节点内部的参数让配置更合理。节点的创建、删除、引用关系变更请使用 `manage-topology` 技能。

## 标准调优流程

### 第 1 步：定位目标 + 读取字段约束

1. 如果还没有，先用 `analyze-config` 定位要调优的节点
2. 调用 `get_llms_doc(path="factories/{Scene}/{FactoryName}.md")` 拉取目标 Factory 的字段文档
3. 如果调优涉及引用字段，额外确认引用目标是否正确（切换到 `manage-topology`）

**LLMTEXT 在这一步的核心作用**：理解每个字段的类型、取值范围、默认值和语义——
这些是判断「当前值是否合理」以及「应该调到什么值」的依据。

### 第 2 步：读取当前值

```python
detail = get_config_detail(locator="tcfg:draft/LLM/DeepSeek草稿/11")
# 保存 content_hash 和当前字段值
```

确认 `truncated` 为 false——如果被截断，先用 `select` 聚焦到要调优的区域。

### 第 3 步：分析现状 → 构造修改

结合第 1 步的 LLMTEXT 字段约束和第 2 步的当前值：

- 哪些字段值偏离了最佳实践？（如 temperature 过高导致输出不稳定）
- 哪些字段使用了默认值但其实应该显式设置？（如 max_tokens 太小截断了输出）
- 哪些字段值之间有冲突？（如某两个参数组合在一起效果不好）

确认修改方案后，构造只含变更字段的 `config` 字典。**服务端执行 merge，只传要改的字段。**

详见子资源 `references/field-design.md`。

### 第 4 步：带冲突保护的更新

```python
update_draft(
    setting_id=11,
    setting_name="deepseek",       # 来自第 2 步
    config={"temperature": 0.3},   # 只传变更字段
    expected_hash="abc123...",     # 来自第 2 步（强烈推荐！）
)
```

**始终传入 `expected_hash`。** 这启用了 theseus-kit 客户端实现的乐观并发控制：
- 写入前自动 GET 当前草稿，对比 hash——不匹配则抛出 `DraftConflictError`，重新读取后重试
- 只在单 actor 场景下才省略——不推荐

详见子资源 `references/conflict-resolution.md`。

### 第 5 步：校验

```python
validate_draft(setting_id=11)
```

如果返回 `valid: false`：
- 查看每个 `DraftValidationResult` 中的 `errors[]`
- 按 `error_type` 和 `message` 定位问题
- 修复后重新 `update_draft` → `validate_draft`

详见子资源 `references/validation-strategy.md`。

### 第 6 步：验证结果

成功更新后，调用 `get_config_detail` 确认修改已生效。检查 `content_hash` 与第 2 步不同。

## LLMTEXT 集成

本技能使用 LLMTEXT 的**字段约束**视角：

| 场景 | 读什么 |
|------|--------|
| 了解字段的类型和取值范围 | 目标 Factory 的 LLMTEXT 页面 |
| 不确定某个参数的含义 | Factory 页面中该字段的描述 |
| 对比不同 Factory 的同名参数 | 两个 Factory 的页面分别拉取 |
| 校验失败后排查字段约束 | 回到 Factory 页面对照字段定义 |

## 子资源

- `references/field-design.md` — 字段设计：从 LLMTEXT 提取约束、引用类型、嵌套结构、敏感字段
- `references/conflict-resolution.md` — 乐观并发冲突处理策略
- `references/validation-strategy.md` — 校验错误诊断与修复模式

## 关键约束

- **先读 LLMTEXT 再改值**——不同版本字段约束可能不同。
- **使用 `expected_hash`**——防止多人编辑时的丢失更新。
- **服务端执行 merge**——只传变更字段，不要传全量。
- **本工具绝不发布**——修改仅停留在 draft。发布走 `publish-config` 技能。
- **与 `manage-topology` 的分工**——本技能改值字段（temperature、model、max_tokens 等），引用字段（`*_setting_id`）的变更属于拓扑操作。
""",
        "references/field-design.md": """# 字段设计与约束

## 从 LLMTEXT 提取字段约束

每个 Factory 的 LLMTEXT 页面描述了其配置字段。阅读时关注：

1. **字段名**——必须与 LLMTEXT 中的 key 完全一致
2. **类型**——str / int / float / bool / list / dict
3. **必填 vs 可选**——缺少必填字段会导致 `validate_draft` 失败
4. **取值范围**——枚举值、数值范围、正则约束
5. **默认值**——不传时的行为

## 引用类型字段

配置节点之间的引用关系通过 `*_setting_id` 字段建立。识别方法：

1. 字段名以 `_id` 或 `_setting_id` 结尾 → 通常是引用
2. LLMTEXT 中标注类型为 "reference"
3. 值的格式是纯数字（如 `8`）

> 引用字段的修改属于拓扑操作，详见 `manage-topology` 技能的 `references/factory-selection.md`。

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

密码、API Key、Token 等敏感字段会被 theseus-kit 自动脱敏（`get_config_detail` 中显示为 `<<redacted>>`）。修改时正常传入新值，脱敏仅影响读取展示。

## 字段一致性检查

调优时，不仅要看单个字段是否合法，还要检查字段值之间的一致性：

- 某些参数组合在一起效果不好（如 temperature=0 同时 top_p < 1）
- 某些字段的值依赖于其他字段（如 max_tokens 受 model 的上下文窗口限制）
- 某些字段看似没问题，但结合 LLMTEXT 描述的最佳实践来看不合理

LLMTEXT 中如有「推荐值」「不建议超过」「当 X 为 Y 时」等描述，重点关注。
""",
        "references/conflict-resolution.md": """# 乐观并发冲突处理

## 冲突发生的场景

两个 LLM agent（或 agent + 人类）同时读取同一草稿、各自修改、各自提交——后提交者会遇到冲突。

## 冲突检测机制

`theseus-kit` 在**客户端**实现乐观并发控制（TFRobotServer 不提供 ETag/If-Match）：

1. `get_config_detail` 返回 `content_hash`（theseus-kit 本地对 config 计算的 SHA-256）
2. `update_draft(expected_hash=...)` 时，theseus-kit 先做一次额外的 GET 读取当前草稿，计算当前 hash 并与 `expected_hash` 比较
3. 不匹配 → 抛出 `DraftConflictError`（Python 异常，**不是**服务端返回的 HTTP 状态码）

> ⚠️ **这不是原子操作。** GET 检查和 PUT 写入之间存在竞态窗口——另一个 actor 可能恰好在检查通过后、写入前修改了草稿。这是一种**尽力而为**的保护，在大多数场景下足够，但不能替代服务端原子 compare-and-swap。

## 标准处理循环

```
1. get_config_detail → 读内容 + 保存 hash
2. update_draft(..., expected_hash=current_hash)
   ├── 成功 → 继续
   └── DraftConflictError → 冲突
       3. get_config_detail → 重新读取最新内容 + 新 hash
       4. 将你的修改重新应用到最新内容上
       5. update_draft(..., expected_hash=new_hash)
          ├── 成功 → 继续
          └── DraftConflictError → 回到步骤 3（最多重试 3 次）
```

## 何时可以省略 expected_hash

- 你是唯一操作者（单 agent 场景），不需要冲突保护
- 你刚从 `create_draft` 创建了节点，还没有其他人能看到

## 多 actor 协作建议

- 不同 agent 修改不同节点 → 不会冲突
- 不同 agent 修改同一节点的不同字段 → 可能不冲突（server 端 merge），但推荐用 hash 保护
- 不同 agent 修改同一字段 → 必然冲突，hash 保护最关键
""",
        "references/validation-strategy.md": """# 校验错误诊断与修复

## validate_draft 响应解读

```json
{
  "valid": false,
  "total_count": 3,
  "pass_count": 2,
  "fail_count": 1,
  "results": [
    {
      "setting_id": 11,
      "scene": "LLM",
      "factory_name": "DeepSeek草稿",
      "setting_name": "deepseek",
      "valid": false,
      "errors": [
        {"field": "config.temperature", "message": "value must be between 0 and 2", "error_type": "value_error"}
      ]
    }
  ]
}
```

## 常见错误类型

| error_type | 含义 | 修复方式 |
|-----------|------|---------|
| `value_error` | 字段值超出范围或格式错误 | 对照 LLMTEXT 检查约束 |
| `missing` | 必填字段未设置 | 通过 `update_draft` 补填 |
| `type_error` | 字段类型不匹配（如 string 当 int） | 检查 LLMTEXT 中的类型定义 |
| `reference_error` | 引用了一个不存在的 setting_id | 用 `list_config_nodes` 找正确的 id |
| `duplicate` | 同一 Factory 下有同名 setting | 改名或删除重复项 |

## 修复模式

### 逐节点修复

适合少量错误（≤3 个错误节点）：
1. 针对每个失败节点调用 `get_config_detail`
2. 根据 `errors[].field` 定位问题字段
3. `update_draft` 逐个修复
4. `validate_draft` 重验

### 全量修复

适合大量错误或系统性错误（如引用链断裂）：
1. 回到 `analyze-config` 重新理解整体结构
2. 重新读 LLMTEXT 确认字段约束
3. 全量重建 config 字典
4. 不用 `expected_hash`（因为上下文已重建）

## 校验失败 ≠ 配置无用

`valid: false` 只表示不满足上线条件，不影响草稿的保存和使用。可以先修复部分错误，保存编辑进度，后续继续修复。
""",
    },
)
