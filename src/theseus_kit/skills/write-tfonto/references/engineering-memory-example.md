# 范例：软件工程项目记忆机器人 → .tfo

> persona-interview 采集（输入）与 write-tfonto 转换结果（输出）对照的第二个范例，展示「状态流转」与「一个 Action 一次触发多属性变更」的设计（基础范例见 `references/teacher-math-example.md`）。完整文件（结构 + 能力草图）见 `references/engineering-memory.tfo`，已通过 `validate_tfonto.py`（v7）与 TFRobotV2 平台导入器（`TFOntoImportAdapter`，FidelityReport 零 notice）实测。

## 输入：三张清单 + 状态流转

**概念**：需求、技术决策、实现（PR/提交）、故障、人员、来源记录

**数据字段**：

| 概念 | 字段（类型） |
|---|---|
| 需求 | 标题（文本）、状态（提案/批准/取消） |
| 技术决策 | 标题（文本）、状态（生效/被取代） |
| 实现 | 标题（文本）、状态（开发中/已合并/已发布） |
| 故障 | 标题（文本）、严重度（低/中/高/致命）、发生时间（日期时间）、状态（未处理/已缓解/已解决） |
| 人员 | 姓名（文本） |
| 来源记录 | 标题（文本）、类型（Issue/PR/文档/会议/聊天） |

**关系**：需求→驱动→决策；实现→落地→需求；故障→源于→实现；决策→取代→决策；来源记录→佐证→需求/决策/实现/故障；人员→提出→来源记录

**状态流转**（画像新采集项，状态值进数据字段枚举、流转动作进能力草图）：决策 生效→被取代；故障 未处理→已缓解→已解决

## 输出要点

结构部分（object_types/properties/link_types）照搬清单；能力草图（functions/actions）示范：

- `trace_requirement`（query 单用）：需求 → 决策 → 实现 → 故障 全链追溯，只读不落地
- `record_incident`（ActionDef + edit_set 单用）：**一个语义动作改齐 4 个属性 + 2 条边**——记一条故障 = 标题/严重度/时间/状态一次写入，同时关联实现与来源。过去逐个 Property 修改、逐条建边，变更量大且易漏易错
- `supersede_decision`（ActionDef + edit_set 单用）：状态流转动作——旧决策状态改「被取代」+ 建取代边，一次改齐
- 复杂写入组合（先查、再算、再改）：见 `references/capability-layer.md` 的 `refresh_mastery` 范例（Action 挂 edit Function）

> edit_set 操作形状已对照 TR-013 §4.5（TFRobotV2 `kinetic.py`）并经平台导入器实测：`update_entity` 的载荷是 **`prop_delta`**（动词 `set/append/remove`），`props` 只属于 `create_entity`/`add_link`；值位一律 `{node: param, param: …}` 或 `{node: literal, value: …}`，不写裸标量。
