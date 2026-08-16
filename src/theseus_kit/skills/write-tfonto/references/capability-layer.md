# 能力层：FunctionDef × ActionDef —— 逻辑 ⟂ 治理

TFOnto 区别于大多数图谱/本体产品的一层：机器人的「会干什么」是可声明、可组合、受治理的**结构化能力**。

- **FunctionDef = 一等读写计算织物**（TR-013 §4.0/§4.1）：可复用、可组合、类型化的计算。`kind: query` 只读派生（禁改数据）；`kind: edit` 产出 staged edit-set（暂存写入集，不直接落库）。
- **ActionDef = 受治理写入信封**（TR-013 §4.5）：唯一受治理的写入边界——参数表单、提交条件校验、副作用。**逻辑放 Function，治理放 Action**，两轴正交：同一个 edit Function 可被多个 Action 复用，治理规则改动不动逻辑。

画像的「专业技能 / 日常工作」就是本表候选的来源（persona-interview Step 5 产出候选清单，本资源负责落成 Def）。

## 场景组合表（何时单用、何时组合）

| 场景 | 用谁 | 组合方式 | 范例 |
|---|---|---|---|
| 只读计算：诊断/汇总/推荐 | FunctionDef(query) 单用 | 无需 Action | `diagnose_weak_points` 错因诊断 |
| 简单写入：记录一条事实 | ActionDef + edit_set 单用 | 声明式模板零代码 | `record_weak_point`（add_link 模板） |
| 复杂写入：先查、再算、再改 | ActionDef(backing) + FunctionDef(edit) 组合 | 逻辑进 Function（可复用/组合/单测），Action 只留治理信封 | `refresh_mastery` → `update_mastery` |
| 派生属性：读时计算不落存储 | PropertyDef.computed_by → FunctionDef(query) | 属性挂函数 | 掌握度等级 |
| 表单联动：动态选项/预填 | parameters.allowed_values / prefill → FunctionDef(query) | 表单助手 | 知识点下拉 |
| 写后联动：通知/级联 | ActionDef.side_effects → FunctionDef(edit) / 事件 | 治理信封外挂后继 | `mastery_updated` 事件 |

**判断口诀：只读 → Function；写入 → Action；写入有复杂逻辑 → Action 挂 edit Function；改完要联动 → side_effects。**

## 能力草图范例（teacher-math 的 functions/actions）

```yaml
functions:
  - name: diagnose_weak_points
    # 只读计算（错因诊断）：query 单用，不动数据、无需 Action
    kind: query
    signature:
      inputs:
        type: object
        properties:
          student_id: {type: string}
      output: {type: array, items: {type: string}}
    body: {ref: script, language: tfdsl, source: ""}
    depends_on: [Student, KnowledgePoint, struggles_with]
  - name: update_mastery
    # 复杂写入（先查错题、再算薄弱点、再批量改边）：edit 承载逻辑
    kind: edit
    signature:
      inputs:
        type: object
        properties:
          student_id: {type: string}
      output: {type: object}
    body: {ref: script, language: tfdsl, source: ""}
    depends_on: [Student, KnowledgePoint, knows_well, struggles_with]

actions:
  - name: record_weak_point
    # 简单写入（记一条薄弱点）：Action + edit_set 声明式模板零代码
    parameters:
      - {name: student_id, type: {type: string}}
      - {name: point_id, type: {type: string}}
    edit_set:
      ops:
        - op: add_link
          link_type: struggles_with
          source: {node: param, param: student_id}
          target: {node: param, param: point_id}
    submission_criteria:
      - condition: {node: compare, op: is_not_null, left: {node: param, param: student_id}}
        message: 学生 ID 不能为空
    side_effects:
      - {ref: event, event: mastery_updated}
  - name: refresh_mastery
    # 复杂写入的组合用法：Action 只留治理信封，逻辑全部在 backing 的 edit Function
    parameters:
      - {name: student_id, type: {type: string}}
    backing: {ref: def, function_id: update_mastery}
```

> 本段是 `references/teacher-math.tfo` 的能力草图部分（完整文件含 object_types/properties/link_types，
> 已通过独立校验器与平台导入器双路验证）。`source` 里的 tfdsl 源码在功能开发期填写。
