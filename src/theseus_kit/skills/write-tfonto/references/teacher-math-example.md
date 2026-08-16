# 范例：知识结构清单 → .tfo（初中数学教学机器人）

> persona-interview 采集的三张清单（输入）与 write-tfonto 转换结果（输出）对照。
> 本 .tfo 已通过独立校验器 validate_tfonto.py 校验（v7）。
> 完整文件（结构 + 能力草图）见 `references/teacher-math.tfo`；能力草图解读见 `references/capability-layer.md`。

## 输入：三张清单

**概念**：学生（接受教学服务的个体）、知识点（最小教学单元）、题目（练习/考试习题）、单元（教材章节组织单位）

**数据字段**：

- 学生：姓名（文本）、学号（文本）、年级（整数）
- 知识点：名称（文本）、难度（易/中/难）
- 题目：标题（文本）、难度（易/中/难）
- 单元：名称（文本）

**关系**：

- 掌握得好：学生 → 知识点（掌握/擅长/熟练）
- 薄弱：学生 → 知识点（薄弱/不擅长/总错）
- 考察：知识点 → 题目（覆盖/考察）
- 隶属于：知识点 → 单元（属于/隶属）
- 前置于：知识点 → 知识点（前置知识/先学）

## 输出：teacher-math.tfo

本范例以英文 snake_case 命名 Def，中文表达全部进入 trigger_words；「学号」作为业务唯一标识
成为 `primary_property`，「姓名」等检索用字段进入 `name_properties`。

```yaml
# TFOnto native format v7
# 范例：初中数学教学机器人知识结构（write-tfonto 技能资源）
schema_version: 7
ontology:
  namespace: math-teacher

object_types:
  - name: Student
    pos: [noun]
    name_properties: [student_name]
    primary_property: student_id
  - name: KnowledgePoint
    pos: [noun]
    name_properties: [point_name]
  - name: Exercise
    pos: [noun]
    name_properties: [exercise_title]
  - name: Unit
    pos: [noun]
    name_properties: [unit_name]

properties:
  - name: student_name
    domain: [Student]
    data_range:
      type: string
  - name: student_id
    domain: [Student]
    data_range:
      type: string
    characteristics:
      is_functional: true
  - name: grade_level
    domain: [Student]
    data_range:
      type: integer
  - name: point_name
    domain: [KnowledgePoint]
    data_range:
      type: string
  - name: difficulty
    domain: [KnowledgePoint, Exercise]
    data_range:
      type: string
      enum: [easy, medium, hard]
  - name: exercise_title
    domain: [Exercise]
    data_range:
      type: string
  - name: unit_name
    domain: [Unit]
    data_range:
      type: string

link_types:
  - name: knows_well
    domain: [Student]
    range: [KnowledgePoint]
    trigger_words: [掌握, 擅长, 熟练]
  - name: struggles_with
    domain: [Student]
    range: [KnowledgePoint]
    trigger_words: [薄弱, 不擅长, 总错]
  - name: covers
    domain: [KnowledgePoint]
    range: [Exercise]
    trigger_words: [覆盖, 考察]
  - name: part_of
    domain: [KnowledgePoint]
    range: [Unit]
    trigger_words: [属于, 隶属]
  - name: prerequisite_of
    domain: [KnowledgePoint]
    range: [KnowledgePoint]
    trigger_words: [前置知识, 先学]
```
