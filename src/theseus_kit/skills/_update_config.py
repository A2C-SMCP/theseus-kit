"""Skill: update-config — 更新/删除 TFRobot 草稿配置。

Guides the LLM through the read-check-write cycle for modifying existing draft
configuration, with optimistic concurrency control, conflict handling, and
validation feedback loops.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="update-config",
    description="更新或删除 TFRobot 草稿配置：Schema 优先 → 读取现状 → 构造变更 → 冲突保护 → 校验。",
    content={
        "SKILL.md": """---
name: update-config
description: 安全修改或删除 TFRobot 草稿配置 —— 读取-检查-写入循环、乐观并发控制、校验错误处理。
---

# 更新配置

你正在通过 `theseus-kit` 修改已有的 TFRobot 草稿配置。本技能覆盖**读取-检查-写入**循环：理解 Schema → 读取当前内容 → 构造安全修改 → 处理冲突 → 校验结果。

删除操作也在此覆盖——将 config 设为空对象 `{}` 通过 `update_draft` 实现。

## 标准编辑流程

### 第 1 步：定位目标 + 读取 Schema

1. 如果还没有，先用 `analyze-config` 技能定位要修改的节点
2. 调用 `get_llms_doc` 拉取目标 Factory 的字段文档，确保理解约束
3. 如果修改涉及引用字段，同时拉取被引用 Factory 的文档

### 第 2 步：读取当前内容

```python
detail = get_config_detail(locator="tcfg:draft/LLM/DeepSeek草稿/11")
# 保存 content_hash 和当前 setting_name
```

确认 `truncated` 为 false——如果被截断，先用 `select` 聚焦到要修改的区域。

### 第 3 步：构造修改

基于 LLMTEXT Schema（第 1 步）和当前内容（第 2 步）：

- 只包含需要变更的字段——服务端执行的是 merge，不是全量替换
- 对照 Schema 校验你的变更：字段类型、必填字段、取值约束
- 不确定时，重新拉取相关的 LLMTEXT 页面

### 第 4 步：带冲突保护的更新

```python
update_draft(
    setting_id=11,
    setting_name="deepseek",     # 来自第 2 步
    config={"temperature": 0.3}, # 只传变更字段
    expected_hash="abc123...",   # 来自第 2 步（强烈推荐！）
)
```

**始终传入 `expected_hash`。** 这启用了乐观并发控制：
- 如果其他人在你读取之后修改了草稿 → HTTP 409，重新读取后重试
- 只在最后一写胜出场景下才省略——不推荐

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

## 删除操作

TFRobotServer 支持通过 `POST /v1/factory/drafts/delete` 删除草稿。删除前：
1. 通过 `get_config_detail` 确认节点内容
2. 检查 topology 确认不是其他节点的被引用节点（否则会导致 dangling reference）
3. 与用户明确确认删除意图
4. 删除后调用 `get_config_summary` 验证

## LLMTEXT 集成

| 场景 | 读什么 |
|------|--------|
| 修改前了解字段约束 | 目标 Factory 的 LLMTEXT 页面 |
| 修改引用字段 | 被引用 + 引用方两面都要读 |
| 不确定修改影响范围 | topology（通过 `get_config_summary`）+ `list_config_nodes` |
| 校验失败后排查 | Factory 页面的字段约束描述 |

## 子资源

- `references/conflict-resolution.md` — 乐观并发冲突处理策略
- `references/validation-strategy.md` — 校验错误诊断与修复模式

## 关键约束

- **Schema 优先**——修改前必须读 LLMTEXT。不同 TFRobotServer 版本字段可能不同。
- **使用 `expected_hash`**——防止多人编辑时的丢失更新。
- **service 端执行 merge**——只传变更字段，不要传全量。
- **本工具绝不发布**——修改仅停留在 draft。发布走 `publish-config` 技能。
""",
        "references/conflict-resolution.md": """# 乐观并发冲突处理

## 冲突发生的场景

两个 LLM agent（或 agent + 人类）同时读取同一草稿、各自修改、各自提交——后提交者会遇到冲突。

## 冲突检测机制

`get_config_detail` → `content_hash`（SHA-256 of config）
→ `update_draft(expected_hash=...)`

如果服务端当前的 config hash 与你传入的 `expected_hash` 不匹配 → **HTTP 409 / DraftConflictError**。

## 标准处理循环

```
1. get_config_detail → 读内容 + 保存 hash
2. update_draft(..., expected_hash=current_hash)
   ├── 200 → 成功，继续
   └── 409 → 冲突
       3. get_config_detail → 重新读取最新内容 + 新 hash
       4. 将你的修改重新应用到最新内容上
       5. update_draft(..., expected_hash=new_hash)
          ├── 200 → 成功
          └── 409 → 回到步骤 3（最多重试 3 次）
```

## 何时放弃 expected_hash

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
