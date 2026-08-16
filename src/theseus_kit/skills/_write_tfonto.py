"""Skill: write-tfonto — 将结构化自然语言知识图谱转换为 TFOnto (.tfo).

Follow-up skill to ``persona-interview``.  Converts the three confirmed
lists (concepts / data fields / relations) plus the capability sketch into a
platform-importable ``.tfo`` knowledge graph.

The content lives in the ``write-tfonto/`` package folder (marketplace
SKILL v1 shape: ``SKILL.md`` + ``references/`` + ``scripts/``) — real files
keep their real extensions, the validator script stays runnable as-is, and
A2C-SMCP skill.md §3 mode C stages each file by rel_path.
"""

from __future__ import annotations

from ._registry import SkillDef, load_package_skill

SKILL: SkillDef = load_package_skill(
    "write-tfonto",
    name="write-tfonto",
    description="将知识结构清单与能力草图转换为平台可导入的 TFOnto（.tfo）：概念/数据字段/关系 → Def，能力 → Function/Action。",
)
