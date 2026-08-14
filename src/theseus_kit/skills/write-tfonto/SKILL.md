---
name: write-tfonto
description: 将结构化自然语言知识图谱（概念/数据字段/关系清单）与能力草图（Function/Action 候选）转换为平台可导入的 TFOnto（.tfo）文件。当画像调研产出知识结构与能力候选、需要生成机器人知识图谱时使用。
---

# Write TFOnto —— 知识结构与能力草图转 .tfo

把 persona-interview 产出的三张清单 + 能力草图（或任何画像文档中的概念/数据字段/关系/能力描述）转换为合法 `.tfo` 文件。**事实正确性由清单确认环节保证，本技能只保证格式合法性**——转换是机械活，不发明新概念、不改动需求方确认过的内容。

## 输入契约

输入必须是结构化清单：概念（名词 + 一句话定义）、数据字段（挂概念 + 类型）、关系（动词 + 方向 + 触发词），外加**选交**的能力草图（能力 → 形态 → 输入输出）。收到自由散文时，先用 `AskUserQuestion` 追问补全清单，**不猜**。

## 转换规则

| 清单项 | .tfo 归宿 | 要点 |
|---|---|---|
| 概念 | `object_types` | `pos: [noun]`；有「名字」字段的概念写 `name_properties`；有业务唯一标识的写 `primary_property` |
| 数据字段 | `properties` | `domain` 挂所属概念；`data_range` 写 JSON Schema 片段；单值字段加 `characteristics: {is_functional: true}` |
| 关系 | `link_types` | 定向 `domain → range`；触发词进 `trigger_words` |
| 能力草图 | `functions` / `actions` | 按场景组合表落位：只读 → query Function 单用；简单写 → Action + edit_set；复杂写 → Action 挂 edit Function；联动 → side_effects（见 `references/capability-layer.md`） |

- `ontology.namespace` 写机器人代号（如 `math-teacher`），只允许 `[A-Za-z0-9_.-]`；`schema_version: 7`。
- **能力草图是选交件**：没有经需求方确认的能力不写 `functions`/`actions`；写了就必须过自检。

## 起草纪律

1. **name 跨五类全局唯一**——重名平台直接拒绝导入。
2. **引用写 name 不写 id**：`domain`/`range`/`name_properties`/`primary_property`/`depends_on`/`function_id` 里写别的 Def 的名字；`id` 省略或写 `auto`。
3. **字段白名单（extra=forbid）**：每个 Def 只允许格式说明列出的字段，未知字段导入被拒。
4. **trigger_words 只属于 link_types**：写在属性/概念上会被拒绝。
5. 值域 `data_range` 必须是合法 JSON Schema 片段（mapping）——形状写清楚，机器人能力直接受益。

## 自检清单（交付门槛）

写完逐条核对（覆盖提交者最易写错的层；权威校验门是平台导入）：

- [ ] YAML 顶层是 mapping，无未知顶层键
- [ ] `schema_version: 7`；`ontology.namespace` 显式、非空、合法字符
- [ ] 五类 Def 名字全文件唯一
- [ ] 所有 `domain`/`range`/`name_properties`/`primary_property`/`depends_on`/`function_id` 引用都能解析到已定义的名字
- [ ] 每个 `data_range` 与 `signature` 是 mapping；`trigger_words` 只出现在 link_types
- [ ] 若写 functions/actions：`kind` 合法、`body` 必填、Action 的 `edit_set`/`backing` 至少其一；结构与 `references/teacher-math-example.md`、`references/capability-layer.md` 范例一致

## 校验（收稿门槛）

本技能附带独立校验器（`scripts/validate_tfonto.py`，零 tfrobot 依赖，仅需 PyYAML）：

```bash
python ${TFROBOT_SKILL_DIR}/scripts/validate_tfonto.py <文件>.tfo
```

跑出 `✅ 校验通过` 才算收稿，失败逐条修正后重跑。A2C 环境由 SDK 把 `${TFROBOT_SKILL_DIR}` 展开为技能包绝对路径；若占位符未展开（非 A2C 客户端），先把 `scripts/validate_tfonto.py` 资源内容保存为本地文件再运行。

**权威校验门仍是平台导入**：校验器通过 ≠ 平台一定通过（深层 AST、JSON Schema 全文、DSL 编译由平台校验），但校验器失败平台必然拒绝。交付时向需求方说明这一边界。

## 交付

- 落盘 `.tfo` 文件，默认 `docs/personas/<机器人名>.tfo`（与画像文档同名同目录）。
- 在画像文档的知识结构章节补一行链接与一句话概述，不重复粘贴全文。
- **.tfo 是活文档**：能力上线/变更后回改，Def 的 `version` 递增——画像只调研一次，知识图谱持续维护。

## Resources

- `references/tfonto-format.md` — .tfo 格式说明（画像阶段相关字段 + 常见错误对照表）
- `references/capability-layer.md` — 能力层：FunctionDef × ActionDef 概念、场景组合表、能力草图范例
- `references/teacher-math-example.md` — 范例：知识结构清单 → .tfo（初中数学教学机器人）
- `references/teacher-math.tfo` — 完整范例文件（结构 + 能力草图），已通过校验器与平台导入器验证
- `scripts/validate_tfonto.py` — 独立校验器（仅依赖 PyYAML），收稿门槛
