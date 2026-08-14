# TFOnto (.tfo) 格式说明（画像阶段）

> 权威来源（TFRobotV2 仓库）：TR-013 与 `tfrobot/schema/onto/importers/tfo.py`。本文只保留画像阶段需要的字段。

## 1. 文件形态

- UTF-8 YAML；顶层必须是 mapping；`schema_version: 7`（当前版本）。
- 顶层键：`schema_version` / `ontology` / `object_types` / `properties` / `link_types` /
  `functions` / `actions`；其它键平台导入**静默丢弃**。
- `ontology.namespace` **必填**：只允许 `[A-Za-z0-9_.-]`（禁 `/`）。

## 2. Def 通用字段（五类共用）

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | **跨五类全局唯一**（重名拒绝导入） |
| `id` | 否 | 省略或 `id: auto` |
| `version` / `annotations` | 否 | def 级版本（能力变更时递增）/ 自由注解 |
| `namespace` | 否 | 继承 `ontology.namespace`，手写不用 |

## 3. 符号名引用

引用别的 Def 写它的 **`name`**，不写 id。导入器两遍解析，引用可先于定义出现。白名单字段：
单值 `primary_property` / `inverse_of` / `function_id`；列表 `super_types` / `equivalent_to` /
`disjoint_with` / `name_properties` / `domain` / `range` / `depends_on`。
引用未定义 → 导入 fail-loud。

## 4. ObjectTypeDef —— 概念（名词）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `super_types` | list[ref] | 否 | 父类 |
| `name_properties` | list[ref] | 否 | 命名属性（检索召回命门） |
| `primary_property` | ref | 否 | 业务身份/合并锚点（同 primary 值自动 merge） |
| `pos` | list[str] | 否 | 词性，如 `[noun]` |
| `equivalent_to` / `disjoint_with` | list[ref] | 否 | 等价类 / 互斥类（元数据） |

## 5. PropertyDef —— 属性

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `domain` | list | 否 | 挂哪些概念；简写 `[Student]`；完整形式含 `object_type`/`required`/`immutable` |
| `data_range` | dict | 否 | 值域 = **合法 JSON Schema 片段**；写入校验与 LLM 入参 schema 都消费它 |
| `characteristics` | dict | 否 | 7 布尔；数据属性只有 `is_functional` 有意义（True = 单值） |

> ⚠️ PropertyDef 没有 trigger_words（TFROB-669 定夺）：宿主仅 LinkTypeDef。

## 6. LinkTypeDef —— 关系（动词）

定向语义：`domain → range`。

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `domain` | list[ref] | 否 | 源端概念 |
| `range` | list[ref] | 否 | 目标端概念 |
| `inverse_of` | ref | 否 | 逆关系 |
| `trigger_words` | list[str] | 否 | **下钻判定键（宿主唯一）**——沿此边下钻召回 |

## 7. data_range 的 JSON Schema 规则

- 必须是 mapping（合法 JSON Schema 片段即可）。
- 常见形态：`{type: string}` / `{type: integer}` / `{type: array, items: {...}}` /
  `{type: string, enum: [easy, medium, hard]}`。

## 8. 能力层：FunctionDef / ActionDef

机器人「会干什么」的结构化表达。概念与场景组合（何时单用、何时组合）见
`references/capability-layer.md`。

**FunctionDef**（`functions`）：`kind` 必填（query 只读 / edit 产出 staged edit-set）；`body` 必填
（`{ref: script, language: tfdsl, source}` 或 `{ref: def, function_id}`）；`signature` =
`{inputs, output}` JSON Schema；`depends_on` 依赖声明。

**ActionDef**（`actions`）：`edit_set` 与 `backing` **至少其一**。`parameters` 表单配置；
`edit_set` 声明式编辑模板（六种 EditOp）；`backing` 挂 kind=edit 的 Function；
`submission_criteria` 提交条件（message 返回调用方）；`side_effects` 后继（函数或事件）。

## 9. 常见错误对照表

| 错误 | 正确写法 |
|---|---|
| 五类 Def 重名 | 名字全文件唯一 |
| 引用未定义 | 先定义被引用 Def，或改名字拼写 |
| trigger_words 写在 properties/object_types | trigger_words 只属于 link_types |
| 未知字段 | 对照本文字段表（extra=forbid） |
| domain 元素形状错 | str 简写，或含 object_type 的 mapping |
