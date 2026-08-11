"""Skill: save-template — 将草稿子树保存为可复用模板。

Guides the LLM through identifying reusable subtrees, naming conventions,
saving with optimistic concurrency, and verifying the result.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="save-template",
    description="将草稿子树保存为可复用模板：识别可复用节点 → 命名 → save_template → 验证。",
    content={
        "SKILL.md": """---
name: save-template
description: 将草稿配置子树保存为可复用模板 —— 识别、命名、保存、验证。
---

# 保存配置模板

你正在通过 `theseus-kit` 将草稿子树保存为可复用模板。模板可被后续创建新配置时重用，提高一致性、减少重复操作。

## 何时模板化

适合模板化的场景：
- 一个精心调好的 LLM 后端配置（如某特定模型的参数组合）
- 一个标准的 Tool 配置（常用搜索、MCP 工具）
- 一套固定的 Chain/Prompt 组合

不适合模板化的场景：
- 包含大量引用其他具体节点的配置（引用关系在模板实例化时可能不成立）
- 一次性的、不会被重复使用的特殊配置

## 标准模板化流程

### 第 1 步：确认草稿状态

```python
detail = get_config_detail(locator="tcfg:draft/LLM/DeepSeek草稿/11")
# 保存 content_hash
```

确认草稿内容是你想要模板化的最终状态。如果还有待修改的地方，先用 `update-config` 完善。

### 第 2 步：命名

模板名应：
- 简洁描述模板用途（如 `deepseek-v3-standard`、`bing-search-tool`）
- 遵循命名惯例：小写、连字符分隔
- 与已有模板不重名（通过 `get_config_summary` 查看 template count）

### 第 3 步：保存

```python
save_template(
    setting_id=11,
    template_name="deepseek-v3-standard",
    expected_hash="abc123...",  # 推荐
)
```

- **草稿不会被修改**——这只是创建一个独立的模板副本
- 返回 `template_id` 和 `locator`（如 `tcfg:template/42`）

### 第 4 步：验证

```python
get_template(template_id="42", metadata_only=False)
```

确认模板内容完整、字段值正确。

## 子资源

- `references/template-design.md` — 模板设计原则与粒度选择

## 关键约束

- **草稿不受影响**——模板化是复制，不改变原草稿。
- **模板不可变**——保存后不支持直接修改。如需修改，更新草稿后重新 save_template（生成新 template_id）。
- **命名是永久的**——好的命名让后续查找和复用变得容易。
""",
        "references/template-design.md": """# 模板设计原则

## 好模板的特征

1. **自包含**——模板内容不依赖外部引用（或只引用通用节点）
2. **参数化空间**——留下可调整的字段，不需要每个实例完全相同
3. **有文档**——模板名本身就是最好的文档，名字描述用途
4. **经过验证**——模板来自一个已经校验通过的草稿

## 坏模板的特征

1. **包含断裂引用**——引用了不存在的 setting_id
2. **过度具体**——没有任何可调整的参数，实例化后必须大量修改
3. **过度抽象**——几乎所有字段都需要填充，模板价值低

## 粒度选择

| 粒度 | 示例 | 适用场景 |
|------|------|---------|
| 单个节点 | 一个 LLM 后端配置 | 「我要一个新的 DeepSeek 实例」 |
| 节点+子节点 | LLM + 对应的 Tool 配置 | 「我要加一个带搜索工具的 LLM」 |
| 全站配置 | 整个 ROBOT 树 | 「我要克隆一个机器人配置到另一个机器人」 |

当前 `save_template` 保存的是**单个节点**（及其 config 中的引用）。如果需要多层模板化，可通过多次 save 实现。

## 命名约定

- `{model}-{variant}`：`deepseek-v3`、`claude-opus-4`
- `{tool-type}-{source}`：`web-search-bing`、`mcp-github`
- `{chain-type}-{purpose}`：`seq-chain-qa`、`parallel-chain-multi-agent`
""",
    },
)
