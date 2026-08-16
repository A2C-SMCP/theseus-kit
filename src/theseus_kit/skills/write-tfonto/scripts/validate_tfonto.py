#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# filename: validate_tfonto.py
"""独立 TFOnto (.tfo) 校验器 —— 不依赖 tfrobot 库，仅需 PyYAML。

Epic / Story 归属
-----------------
- Epic: **TFROB-606**（OntoStore 与 GraphIndexV4）。
- Story: **TFROB-728**（.tfo 原生设计交换格式）—— 本脚本是其独立交付校验门（write-tfonto SKILL 资源）。

使命
----
给运营/需求方一个零 tfrobot 依赖的自检入口：提交机器人知识图谱 .tfo 前跑通本脚本，
确保文件能被平台导入器接受。镜像 ``tfrobot/schema/onto/importers/tfo.py``（两遍符号
解析、重名 fail-loud、id 格式、顶层信封 fail-loud）与 OntologySnapshot v7 元模型
（``extra="forbid"`` 字段白名单）的校验语义，并深度镜像能力层 AST 形状
（``kinetic.py`` 六种 EditOp 与 ``expr.py`` 的 ValueExpr/Expr/ValidationRule——
``update_entity`` 载荷是 ``prop_delta``、值位必须写操作数等易错点在这里直接 fail）——
刻意不 import tfrobot（依赖太重），仅 PyYAML。

边界（诚实声明）
----------------
- 深层 AST（ObjectSetExpr 的完整形状，如 ``apply_to`` 内表达式）与 JSON Schema 全文校验、
  DSL 编译由平台导入/执行时进行，本脚本只做形状级检查（mapping/list/str/bool 与关键字段）。
- schema_version：本脚本只接受当前版本（7）或缺省；旧版本文件平台导入会自动迁移，
  但新提交请写当前版本。
- namespace 要求显式声明（平台有文件名兜底，但运营提交要求显式——校验器更严的唯一一处）。
- 参数引用（``{node: param, param: X}``）中 X 未在 ``parameters`` 声明：平台导入不检查
  （执行期绑定才失败），本脚本仅告警不判错。

用法
----
    python validate_tfonto.py <file.tfo> [<file2.tfo> ...]

退出码：0 = 全部通过；1 = 存在错误；2 = 环境依赖缺失（PyYAML 未安装）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print(
        "缺少依赖 PyYAML。请先安装：\n"
        "    pip install pyyaml\n"
        "本校验器刻意不依赖 tfrobot 库，仅依赖 PyYAML 一个三方包。",
        file=sys.stderr,
    )
    raise SystemExit(2)

# —— 与 tfrobot.schema.onto.base.TFONTO_SCHEMA_VERSION 保持同步 ——
SCHEMA_VERSION = 7

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# —— 顶层 / 家族字段白名单（对齐 pydantic extra="forbid" 语义）——
_TOP_LEVEL_KEYS = (
    "schema_version",
    "ontology",
    "object_types",
    "properties",
    "link_types",
    "functions",
    "actions",
)
# 实例数据键：.tfo 是设计交换格式（仅五 Def），给 serde 指路（对齐 tfo.py 提示）。
_INSTANCE_KEYS = frozenset({"instances", "link_assertions"})
_ONTOLOGY_KEYS = ("namespace", "source_base_iri")
_DEF_COMMON_KEYS = ("id", "name", "namespace", "version", "annotations")

_FAMILY_KEYS: dict[str, tuple[str, ...]] = {
    "object_types": (
        *_DEF_COMMON_KEYS,
        "super_types",
        "equivalent_to",
        "disjoint_with",
        "name_properties",
        "primary_property",
        "pos",
    ),
    "properties": (
        *_DEF_COMMON_KEYS,
        "domain",
        "data_range",
        "super_types",
        "equivalent_to",
        "disjoint_with",
        "characteristics",
        "is_annotation",
        "computed_by",
    ),
    "link_types": (
        *_DEF_COMMON_KEYS,
        "domain",
        "range",
        "super_types",
        "equivalent_to",
        "inverse_of",
        "disjoint_with",
        "characteristics",
        "trigger_words",
    ),
    "functions": (*_DEF_COMMON_KEYS, "kind", "signature", "body", "depends_on"),
    "actions": (
        *_DEF_COMMON_KEYS,
        "parameters",
        "edit_set",
        "backing",
        "submission_criteria",
        "side_effects",
    ),
}

# —— 引用解析白名单（对齐 tfo.py _DEFID_SCALAR_FIELDS / _DEFID_LIST_FIELDS）——
_SCALAR_REF_FIELDS = frozenset(
    {
        "primary_property",  # ObjectTypeDef
        "inverse_of",  # LinkTypeDef
        "object_type",  # DomainBinding / CreateEntityOp
        "link_type",  # AddLinkOp / UpdateLinkOp / DeleteLinkOp
        "link",  # TraverseOp
        "function_id",  # FunctionDefRef
        "property_id",  # InstancePropOperand
    }
)
_LIST_REF_FIELDS = frozenset(
    {
        "super_types",
        "equivalent_to",
        "disjoint_with",
        "name_properties",
        "domain",
        "range",
        "depends_on",
    }
)

_CHARACTERISTIC_KEYS = frozenset(
    {
        "is_functional",
        "is_inverse_functional",
        "is_symmetric",
        "is_transitive",
        "is_asymmetric",
        "is_reflexive",
        "is_irreflexive",
    }
)

_OP_TAGS = frozenset(
    {"create_entity", "update_entity", "delete_entity", "add_link", "update_link", "delete_link"}
)

# —— 能力层 AST 形状（对齐 kinetic.py / expr.py / refs.py，extra=forbid 语义）——

# EditOp 逐类字段白名单。注意：props 只属于 create_entity / add_link（= set 语义，
# 创建起点为空）；update_entity 的载荷是 prop_delta（动词载荷）——混写是高频错误。
_EDIT_OP_FIELDS: dict[str, tuple[str, ...]] = {
    "create_entity": ("op", "object_type", "props"),
    "update_entity": ("op", "target", "prop_delta"),
    "delete_entity": ("op", "target"),
    "add_link": ("op", "link_type", "source", "target", "props"),
    "update_link": ("op", "assertion", "link_type", "source", "target", "prop_delta"),
    "delete_link": ("op", "assertion", "link_type", "source", "target"),
}

_PROP_VERBS = frozenset({"set", "append", "remove"})

# ValueExpr 操作数（值位唯一方言：字面值/参数/principal 属性/宿主实例属性/self）。
_VALUE_EXPR_NODES = frozenset({"literal", "param", "principal_attr", "instance_prop", "self"})
_VALUE_EXPR_FIELDS: dict[str, tuple[str, ...]] = {
    "literal": ("node", "value"),
    "param": ("node", "param"),
    "principal_attr": ("node", "attr"),
    "instance_prop": ("node", "property_id"),
    "self": ("node",),
}

# Expr 布尔表达式树（submission_criteria.condition / visible_when / required_when）。
_EXPR_NODES = frozenset({"const", "compare", "and", "or", "not"})
_EXPR_FIELDS: dict[str, tuple[str, ...]] = {
    "const": ("node", "value"),
    "compare": ("node", "op", "left", "right"),
    "and": ("node", "operands"),
    "or": ("node", "operands"),
    "not": ("node", "operand"),
}
_COMPARISON_OPS = frozenset(
    {"eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "is_null", "is_not_null"}
)
_UNARY_OPS = frozenset({"is_null", "is_not_null"})

_PARAMETER_KEYS = ("name", "type", "visible_when", "required_when", "allowed_values", "prefill")


class _Errors:
    """按文件聚合错误/警告，结尾统一打印。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.counts: dict[str, int] = {}

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"  [{where}] {msg}")

    def warning(self, msg: str) -> None:
        self.warnings.append(f"  {msg}")

    def ok(self) -> bool:
        return not self.errors


def _where(family: str, index: int, name: str | None) -> str:
    name_part = f", name={name!r}" if name else ""
    return f"{family}[{index}]{name_part}"


def _type_name(value: Any) -> str:
    return type(value).__name__


# —— 顶层 ——


def _check_top_level(data: dict[str, Any], errs: _Errors) -> None:
    # 信封白名单 fail-loud（对齐 tfo.py _validate_envelope：未知键大声拒绝，禁静默丢弃）
    for key in data:
        if key not in _TOP_LEVEL_KEYS:
            hint = ""
            if key in _INSTANCE_KEYS:
                hint = "——实例数据不属于 .tfo 设计交换格式（仅承载五 Def），请经平台 serde 序列化"
            errs.error("顶层", f"未知键 {key!r}：平台导入器会拒绝导入{hint}")

    version = data.get("schema_version")
    if version is not None:
        if not isinstance(version, int):
            errs.error("顶层", f"schema_version 必须是 int，got: {version!r}")
        elif version != SCHEMA_VERSION:
            errs.error(
                "顶层",
                f"schema_version={version} 不是当前版本 {SCHEMA_VERSION}。"
                "旧版本文件平台导入会自动迁移，但新提交请写当前版本",
            )

    ontology = data.get("ontology")
    if not isinstance(ontology, dict):
        errs.error("ontology", f"必须是 mapping，got: {_type_name(ontology)}")
        return
    for key in ontology:
        if key not in _ONTOLOGY_KEYS:
            errs.error("ontology", f"未知字段 {key!r}")
    namespace = ontology.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        errs.error("ontology.namespace", "必须显式声明非空 namespace（[A-Za-z0-9_.-]）")
    elif not _NAMESPACE_RE.match(namespace):
        errs.error(
            "ontology.namespace",
            f"只允许 [A-Za-z0-9_.-]（禁 '/'，保 IRI 解析无歧义），got: {namespace!r}",
        )
    base_iri = ontology.get("source_base_iri")
    if base_iri is not None and not isinstance(base_iri, str):
        errs.error("ontology.source_base_iri", f"必须是 str，got: {_type_name(base_iri)}")


# —— Pass 1：收集名字 / 校验通用字段 ——


def _collect_names(data: dict[str, Any], errs: _Errors) -> set[str]:
    names: dict[str, str] = {}  # name → where
    for family in _FAMILY_KEYS:
        entries = data.get(family, [])
        if not isinstance(entries, list):
            errs.error(family, f"必须是 list，got: {_type_name(entries)}")
            continue
        for index, entry in enumerate(entries):
            where = _where(family, index, None)
            if not isinstance(entry, dict):
                errs.error(where, f"条目必须是 mapping，got: {_type_name(entry)}")
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                errs.error(where, "缺少有效的 'name' 字段")
                continue
            where = _where(family, index, name)

            for key in entry:
                if key not in _FAMILY_KEYS[family]:
                    errs.error(where, f"未知字段 {key!r}（{family} 不包含此字段，导入会被拒绝）")

            if name in names:
                errs.error(
                    where,
                    f"def 名 {name!r} 与 {names[name]} 重名——五类 Def 的名字必须全局唯一",
                )
            else:
                names[name] = where

            raw_id = entry.get("id")
            if raw_id is not None and raw_id != "auto" and (
                not isinstance(raw_id, str) or not _HEX32.match(raw_id)
            ):
                errs.error(where, f"非法 id {raw_id!r}——用 'auto'、省略，或 32 位小写 hex")

            version = entry.get("version")
            if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
                errs.error(where, f"version 必须是 >=1 的 int，got: {version!r}")

            annotations = entry.get("annotations")
            if annotations is not None and not isinstance(annotations, dict):
                errs.error(where, f"annotations 必须是 mapping，got: {_type_name(annotations)}")

            namespace = entry.get("namespace")
            if namespace is not None and (not isinstance(namespace, str) or not _NAMESPACE_RE.match(namespace)):
                errs.error(where, f"namespace 只允许 [A-Za-z0-9_.-]；手写 .tfo 通常省略（继承 ontology.namespace）")
    return set(names.keys())


# —— Pass 2：符号名引用解析（只读镜像 tfo._resolve_refs）——


def _walk_refs(node: Any, names: set[str], parent_key: str, where: str, errs: _Errors) -> None:
    """白名单字段中的符号名必须可解析（name 或显式 hex32），否则导入 fail-loud。"""
    if isinstance(node, str):
        if parent_key in _SCALAR_REF_FIELDS and node not in names and not _HEX32.match(node):
            errs.error(
                where,
                f"未定义的符号名引用 {node!r}（field={parent_key}）。已定义名称: {sorted(names)}",
            )
        return
    if isinstance(node, list):
        if parent_key in _LIST_REF_FIELDS:
            for item in node:
                if isinstance(item, str):
                    if item not in names and not _HEX32.match(item):
                        errs.error(
                            where,
                            f"未定义的符号名引用 {item!r}（field={parent_key}）。已定义名称: {sorted(names)}",
                        )
                elif isinstance(item, (dict, list)):
                    _walk_refs(item, names, parent_key, where, errs)
        else:
            for item in node:
                _walk_refs(item, names, parent_key, where, errs)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if parent_key == "props" and isinstance(key, str) and key not in names and not _HEX32.match(key):
                errs.error(where, f"props 键 {key!r} 不是已定义的 PropertyDef 符号名")
            _walk_refs(value, names, key, where, errs)


# —— 家族级形状检查 ——


def _check_json_schema_fragment(value: Any, where: str, errs: _Errors) -> None:
    """轻量 JSON Schema 片段检查（全文校验由平台导入执行）。"""
    if not isinstance(value, dict):
        errs.error(where, f"必须是 JSON Schema 片段（mapping），got: {_type_name(value)}")
        return
    if "type" in value and not isinstance(value["type"], str):
        errs.error(where, f"JSON Schema 'type' 必须是 str，got: {value['type']!r}")
    for key in ("properties", "items"):
        if key in value and not isinstance(value[key], dict):
            errs.error(where, f"JSON Schema {key!r} 必须是 mapping")


def _check_characteristics(value: Any, where: str, errs: _Errors) -> None:
    if not isinstance(value, dict):
        errs.error(where, f"characteristics 必须是 mapping，got: {_type_name(value)}")
        return
    for key in value:
        if key not in _CHARACTERISTIC_KEYS:
            errs.error(where, f"characteristics 未知字段 {key!r}（7 布尔之一）")
    for key, val in value.items():
        if not isinstance(val, bool):
            errs.error(where, f"characteristics.{key} 必须是 bool，got: {_type_name(val)}")


def _check_function_ref(value: Any, where: str, errs: _Errors) -> None:
    """FunctionRef：{ref: def, function_id} 或 {ref: script, language: tfdsl, source}。"""
    if not isinstance(value, dict):
        errs.error(where, f"必须是 FunctionRef mapping，got: {_type_name(value)}")
        return
    ref = value.get("ref")
    if ref not in {"def", "script"}:
        errs.error(where, f"FunctionRef 'ref' 必须是 def|script，got: {ref!r}")
        return
    if ref == "def":
        if not isinstance(value.get("function_id"), str):
            errs.error(where, "FunctionDefRef 必须含 str 类型的 function_id（FunctionDef 符号名）")
    else:
        if value.get("language") != "tfdsl":
            errs.error(where, f"内联脚本 language 必须是 'tfdsl'，got: {value.get('language')!r}")
        if "source" in value and not isinstance(value["source"], str):
            errs.error(where, f"内联脚本 source 必须是 str，got: {_type_name(value['source'])}")


def _check_str_list(value: Any, field: str, where: str, errs: _Errors) -> None:
    if not isinstance(value, list):
        errs.error(where, f"{field} 必须是 list，got: {_type_name(value)}")
        return
    for item in value:
        if not isinstance(item, str):
            errs.error(where, f"{field} 元素必须是 str，got: {_type_name(item)}")


# —— 能力层 AST：ValueExpr / Expr / EditOp（镜像 kinetic.py / expr.py 形状）——


def _check_value_expr(value: Any, where: str, errs: _Errors, param_names: set[str] | None = None) -> None:
    """值位操作数（ValueExpr）：{node: literal|param|principal_attr|instance_prop|self}。

    值位禁止裸标量——字面值必须包成 ``{node: literal, value: ...}``（平台 pydantic
    判别联合同样拒绝裸标量，这是高频错误）。
    """
    if not isinstance(value, dict):
        errs.error(
            where,
            f"值位必须是操作数 mapping（{{node: ...}}），不能写裸标量 {value!r}——"
            "字面值请写 {node: literal, value: ...}",
        )
        return
    tag = value.get("node")
    if tag not in _VALUE_EXPR_NODES:
        errs.error(
            where,
            f"未知操作数 node 标签 {tag!r}（合法：{sorted(_VALUE_EXPR_NODES)}）；"
            "若是字面值请写 {node: literal, value: ...}",
        )
        return
    for key in value:
        if key not in _VALUE_EXPR_FIELDS[tag]:
            errs.error(where, f"操作数 {tag!r} 未知字段 {key!r}（导入会被拒绝）")
    if tag == "param":
        param = value.get("param")
        if not isinstance(param, str) or not param:
            errs.error(where, "param 操作数必须含 str 类型的 param（Action 参数名）")
        elif param_names is not None and param not in param_names:
            errs.warning(
                f"[{where}] 参数引用 {param!r} 未在 parameters 中声明——平台导入不检查，执行期绑定会失败"
            )
    elif tag == "principal_attr" and not isinstance(value.get("attr"), str):
        errs.error(where, "principal_attr 操作数必须含 str 类型的 attr")
    elif tag == "instance_prop" and not isinstance(value.get("property_id"), str):
        errs.error(where, "instance_prop 操作数必须含 str 类型的 property_id（PropertyDef 符号名）")


def _check_expr(node: Any, where: str, errs: _Errors, param_names: set[str] | None = None) -> None:
    """布尔表达式树（Expr）：{node: const|compare|and|or|not}。"""
    if not isinstance(node, dict):
        errs.error(where, f"表达式节点必须是 mapping，got: {_type_name(node)}")
        return
    tag = node.get("node")
    if tag not in _EXPR_NODES:
        errs.error(where, f"未知表达式 node 标签 {tag!r}（合法：{sorted(_EXPR_NODES)}）")
        return
    for key in node:
        if key not in _EXPR_FIELDS[tag]:
            errs.error(where, f"表达式 {tag!r} 未知字段 {key!r}（导入会被拒绝）")

    if tag == "const":
        if "value" in node and not isinstance(node["value"], bool):
            errs.error(where, "const 表达式的 value 必须是 bool")
    elif tag == "compare":
        op = node.get("op")
        if op not in _COMPARISON_OPS:
            errs.error(where, f"非法比较算子 {op!r}（合法：{sorted(_COMPARISON_OPS)}）")
            return
        if "left" not in node:
            errs.error(where, "compare 缺少 left 操作数")
        else:
            _check_value_expr(node["left"], f"{where}.left", errs, param_names)
        right = node.get("right")
        if op in _UNARY_OPS:
            if right is not None:
                errs.error(where, f"一元算子 {op!r} 不接受 right 操作数")
        elif right is None:
            errs.error(where, f"二元算子 {op!r} 缺少 right 操作数")
        else:
            _check_value_expr(right, f"{where}.right", errs, param_names)
    elif tag in ("and", "or"):
        operands = node.get("operands")
        if not isinstance(operands, list) or not operands:
            errs.error(where, f"{tag} 表达式的 operands 必须是至少 1 项的 list")
        else:
            for index, operand in enumerate(operands):
                _check_expr(operand, f"{where}.operands[{index}]", errs, param_names)
    elif tag == "not":
        if "operand" not in node:
            errs.error(where, "not 表达式缺少 operand")
        else:
            _check_expr(node["operand"], f"{where}.operand", errs, param_names)


def _check_prop_verb_payloads(
    payloads: Any, where: str, errs: _Errors, param_names: set[str] | None = None
) -> None:
    """prop_delta 动词载荷序列：[{verb: set|append|remove, props: {属性名: ValueExpr}}]。"""
    if not isinstance(payloads, list):
        errs.error(where, f"prop_delta 必须是 list，got: {_type_name(payloads)}")
        return
    seen: dict[str, str] = {}  # 属性名 → 动词（跨 payload 冲突扫描，镜像 kinetic 校验器）
    for index, payload in enumerate(payloads):
        pwhere = f"{where}[{index}]"
        if not isinstance(payload, dict):
            errs.error(pwhere, f"必须是 mapping，got: {_type_name(payload)}")
            continue
        for key in payload:
            if key not in ("verb", "props"):
                errs.error(pwhere, f"未知字段 {key!r}（prop_delta 载荷仅 verb/props）")
        verb = payload.get("verb")
        if verb not in _PROP_VERBS:
            errs.error(pwhere, f"非法动词 {verb!r}（合法：{sorted(_PROP_VERBS)}）")
            continue
        props = payload.get("props", {})
        if not isinstance(props, dict):
            errs.error(pwhere, f"props 必须是 mapping，got: {_type_name(props)}")
            continue
        for prop_name, prop_value in props.items():
            if not isinstance(prop_name, str):
                errs.error(pwhere, f"props 键必须是 str（PropertyDef 符号名），got: {_type_name(prop_name)}")
            elif prop_name in seen and seen[prop_name] != verb:
                errs.error(
                    pwhere,
                    f"属性 {prop_name!r} 同时出现在 {seen[prop_name]!r} 与 {verb!r} 动词中，语义歧义",
                )
            else:
                seen[prop_name] = verb
            _check_value_expr(prop_value, f"{pwhere}.props[{prop_name!r}]", errs, param_names)


def _check_link_addressing(
    op: dict[str, Any], tag: str, where: str, errs: _Errors, param_names: set[str] | None
) -> None:
    """update_link/delete_link 双寻址：assertion 或 (link_type, source, target) 恰选其一。"""
    by_assertion = "assertion" in op
    triple_parts = ("link_type" in op, "source" in op, "target" in op)
    by_triple = all(triple_parts)
    if by_assertion and any(triple_parts):
        errs.error(where, f"{tag} 链接寻址二选一：assertion 与端点三元组不可同时提供")
        return
    if not by_assertion and not by_triple:
        errs.error(where, f"{tag} 链接寻址缺失：须提供 assertion，或 link_type+source+target 三元组")
        return
    if by_assertion:
        _check_value_expr(op["assertion"], f"{where}.assertion", errs, param_names)
    else:
        for key in ("source", "target"):
            _check_value_expr(op[key], f"{where}.{key}", errs, param_names)


def _check_edit_op(op: Any, where: str, errs: _Errors, param_names: set[str] | None) -> None:
    """EditOp 逐类形状（六种操作；镜像 kinetic.py 的 extra=forbid + 必填 + 校验器）。"""
    if not isinstance(op, dict):
        errs.error(where, f"必须是 mapping，got: {_type_name(op)}")
        return
    tag = op.get("op")
    if tag not in _OP_TAGS:
        errs.error(where, f"非法 op tag {tag!r}（六种 EditOp 之一）")
        return
    for key in op:
        if key not in _EDIT_OP_FIELDS[tag]:
            hint = ""
            if key == "props" and tag in ("update_entity", "update_link"):
                hint = "——update 类操作的载荷是 prop_delta（动词载荷），props 只属于 create_entity/add_link"
            errs.error(where, f"{tag} 未知字段 {key!r}（导入会被拒绝）{hint}")

    if tag == "create_entity":
        if not isinstance(op.get("object_type"), str):
            errs.error(where, "create_entity 必须含 str 类型的 object_type（ObjectTypeDef 符号名）")
        props = op.get("props", {})
        if not isinstance(props, dict):
            errs.error(where, f"props 必须是 mapping，got: {_type_name(props)}")
        else:
            for prop_name, prop_value in props.items():
                _check_value_expr(prop_value, f"{where}.props[{prop_name!r}]", errs, param_names)
    elif tag in ("update_entity", "delete_entity"):
        if "target" not in op:
            errs.error(where, f"{tag} 缺少 target（ValueExpr）")
        else:
            _check_value_expr(op["target"], f"{where}.target", errs, param_names)
        if tag == "update_entity" and "prop_delta" in op:
            _check_prop_verb_payloads(op["prop_delta"], f"{where}.prop_delta", errs, param_names)
    elif tag == "add_link":
        for key in ("link_type", "source", "target"):
            if key not in op:
                errs.error(where, f"add_link 缺少 {key}")
        if not isinstance(op.get("link_type"), str):
            errs.error(where, "add_link 的 link_type 必须是 str（LinkTypeDef 符号名）")
        for key in ("source", "target"):
            if key in op:
                _check_value_expr(op[key], f"{where}.{key}", errs, param_names)
        props = op.get("props", {})
        if not isinstance(props, dict):
            errs.error(where, f"props 必须是 mapping，got: {_type_name(props)}")
        else:
            for prop_name, prop_value in props.items():
                _check_value_expr(prop_value, f"{where}.props[{prop_name!r}]", errs, param_names)
    elif tag in ("update_link", "delete_link"):
        _check_link_addressing(op, tag, where, errs, param_names)
        if tag == "update_link" and "prop_delta" in op:
            _check_prop_verb_payloads(op["prop_delta"], f"{where}.prop_delta", errs, param_names)


def _check_validation_rule(
    rule: Any, where: str, errs: _Errors, param_names: set[str] | None = None
) -> None:
    """ValidationRule：{condition: Expr, message: 非空 str}。"""
    if not isinstance(rule, dict):
        errs.error(where, f"必须是 mapping，got: {_type_name(rule)}")
        return
    for key in rule:
        if key not in ("condition", "message"):
            errs.error(where, f"未知字段 {key!r}（ValidationRule 仅 condition/message）")
    if "condition" not in rule:
        errs.error(where, "缺少 condition（表达式）")
    else:
        _check_expr(rule["condition"], f"{where}.condition", errs, param_names)
    if not isinstance(rule.get("message"), str) or not rule["message"]:
        errs.error(where, "message 必须是非空 str（违反时返回给调用方的修正指引）")


def _check_side_effect(effect: Any, where: str, errs: _Errors) -> None:
    """SideEffect 三态：FunctionDefRef | InlineScript | EventDecl（按 ref 判别）。"""
    if not isinstance(effect, dict):
        errs.error(where, f"必须是 mapping，got: {_type_name(effect)}")
        return
    ref = effect.get("ref")
    if ref not in {"def", "script", "event"}:
        errs.error(where, f"side_effect 'ref' 必须是 def|script|event，got: {ref!r}")
        return
    if ref == "def":
        if not isinstance(effect.get("function_id"), str):
            errs.error(where, "FunctionDefRef 必须含 str 类型的 function_id（FunctionDef 符号名）")
    elif ref == "script":
        if effect.get("language") != "tfdsl":
            errs.error(where, f"内联脚本 language 必须是 'tfdsl'，got: {effect.get('language')!r}")
        if "source" in effect and not isinstance(effect["source"], str):
            errs.error(where, f"内联脚本 source 必须是 str，got: {_type_name(effect['source'])}")
    else:  # event
        if not isinstance(effect.get("event"), str):
            errs.error(where, "EventDecl 必须含 str 类型的 event（事件名）")
        if "payload_schema" in effect and not isinstance(effect["payload_schema"], dict):
            errs.error(where, "payload_schema 必须是 JSON Schema mapping")


# —— 家族检查器 ——


def _check_object_types(entry: dict[str, Any], where: str, errs: _Errors) -> None:
    _check_str_list(entry.get("pos", []), "pos", where, errs)
    pp = entry.get("primary_property")
    if pp is not None and not isinstance(pp, str):
        errs.error(where, f"primary_property 必须是 str（PropertyDef 符号名），got: {_type_name(pp)}")


def _check_properties(entry: dict[str, Any], where: str, errs: _Errors) -> None:
    domain = entry.get("domain", [])
    if not isinstance(domain, list):
        errs.error(where, f"domain 必须是 list，got: {_type_name(domain)}")
        return
    for item in domain:
        if isinstance(item, str):
            continue  # v5 简写，引用已由 _walk_refs 解析
        if isinstance(item, dict):
            for key in item:
                if key not in {"object_type", "required", "immutable"}:
                    errs.error(where, f"domain 绑定未知字段 {key!r}（object_type/required/immutable）")
            if not isinstance(item.get("object_type"), str):
                errs.error(where, "domain 绑定必须含 str 类型的 object_type（ObjectTypeDef 符号名）")
            for key in ("required", "immutable"):
                if key in item and not isinstance(item[key], bool):
                    errs.error(where, f"domain 绑定 {key} 必须是 bool，got: {_type_name(item[key])}")
        else:
            errs.error(where, f"domain 元素必须是 str 或 mapping，got: {_type_name(item)}")

    if "data_range" in entry:
        _check_json_schema_fragment(entry["data_range"], f"{where}.data_range", errs)
    if "characteristics" in entry:
        _check_characteristics(entry["characteristics"], where, errs)
    if "is_annotation" in entry and not isinstance(entry["is_annotation"], bool):
        errs.error(where, f"is_annotation 必须是 bool，got: {_type_name(entry['is_annotation'])}")
    if "computed_by" in entry:
        _check_function_ref(entry["computed_by"], f"{where}.computed_by", errs)


def _check_link_types(entry: dict[str, Any], where: str, errs: _Errors) -> None:
    for field in ("domain", "range"):
        if field in entry:
            _check_str_list(entry[field], field, where, errs)
    if "trigger_words" in entry:
        _check_str_list(entry["trigger_words"], "trigger_words", where, errs)
    if "characteristics" in entry:
        _check_characteristics(entry["characteristics"], where, errs)
    inv = entry.get("inverse_of")
    if inv is not None and not isinstance(inv, str):
        errs.error(where, f"inverse_of 必须是 str（LinkTypeDef 符号名），got: {_type_name(inv)}")


def _check_functions(entry: dict[str, Any], where: str, errs: _Errors) -> None:
    kind = entry.get("kind")
    if kind not in {"query", "edit"}:
        errs.error(where, f"kind 必须是 query|edit，got: {kind!r}")
    sig = entry.get("signature", {})
    if not isinstance(sig, dict):
        errs.error(where, f"signature 必须是 mapping，got: {_type_name(sig)}")
    else:
        for key in sig:
            if key not in ("inputs", "output"):
                errs.error(where + ".signature", f"未知字段 {key!r}（signature 仅 inputs/output）")
        for key in ("inputs", "output"):
            if key in sig:
                _check_json_schema_fragment(sig[key], f"{where}.signature.{key}", errs)
    if "body" in entry:
        _check_function_ref(entry["body"], f"{where}.body", errs)
    else:
        errs.error(where, "缺少必填字段 body（FunctionRef）")


def _check_actions(entry: dict[str, Any], where: str, errs: _Errors) -> None:
    edit_set = entry.get("edit_set")
    backing = entry.get("backing")
    if edit_set is None and backing is None:
        errs.error(where, "写能力缺失：edit_set 与 backing 至少提供其一（TR-013 §4.5）")

    if backing is not None:
        _check_function_ref(backing, f"{where}.backing", errs)

    param_names: set[str] = set()
    parameters = entry.get("parameters", [])
    if not isinstance(parameters, list):
        errs.error(where, f"parameters 必须是 list，got: {_type_name(parameters)}")
    else:
        for index, param in enumerate(parameters):
            pwhere = f"{where}.parameters[{index}]"
            if not isinstance(param, dict):
                errs.error(pwhere, f"必须是 mapping，got: {_type_name(param)}")
                continue
            for key in param:
                if key not in _PARAMETER_KEYS:
                    errs.error(pwhere, f"未知字段 {key!r}（ParameterSpec 白名单之外）")
            if not isinstance(param.get("name"), str):
                errs.error(pwhere, "缺少 str 类型的 name")
            else:
                param_names.add(param["name"])
            if "type" in param:
                _check_json_schema_fragment(param["type"], f"{pwhere}.type", errs)
            for key in ("visible_when", "required_when"):
                if key in param:
                    _check_expr(param[key], f"{pwhere}.{key}", errs)  # 参数名后收集，此处不交叉检查
            if "allowed_values" in param:
                av = param["allowed_values"]
                if not isinstance(av, dict) or av.get("ref") not in {"static", "def", "script"}:
                    errs.error(pwhere, "allowed_values 的 ref 必须是 static|def|script")
                elif av["ref"] == "static" and not isinstance(av.get("values"), list):
                    errs.error(pwhere, "static allowed_values 必须含 list 类型的 values")
                elif av["ref"] == "def" and not isinstance(av.get("function_id"), str):
                    errs.error(pwhere, "def allowed_values 必须含 str 类型的 function_id")
                elif av["ref"] == "script" and av.get("language") != "tfdsl":
                    errs.error(pwhere, "script allowed_values 的 language 必须是 'tfdsl'")
            if "prefill" in param:
                _check_function_ref(param["prefill"], f"{pwhere}.prefill", errs)

    if edit_set is not None:
        if not isinstance(edit_set, dict):
            errs.error(where, f"edit_set 必须是 mapping，got: {_type_name(edit_set)}")
        else:
            for key in edit_set:
                if key not in ("ops", "apply_to"):
                    errs.error(where + ".edit_set", f"未知字段 {key!r}（仅 ops/apply_to）")
            ops = edit_set.get("ops", [])
            if not isinstance(ops, list):
                errs.error(where + ".edit_set", f"ops 必须是 list，got: {_type_name(ops)}")
            else:
                for index, op in enumerate(ops):
                    _check_edit_op(op, f"{where}.edit_set.ops[{index}]", errs, param_names)
            if "apply_to" in edit_set and not isinstance(edit_set["apply_to"], dict):
                errs.error(where + ".edit_set", "apply_to 必须是 ObjectSetExpr mapping")

    submission_criteria = entry.get("submission_criteria", [])
    if not isinstance(submission_criteria, list):
        errs.error(where, f"submission_criteria 必须是 list，got: {_type_name(submission_criteria)}")
    else:
        for index, rule in enumerate(submission_criteria):
            _check_validation_rule(rule, f"{where}.submission_criteria[{index}]", errs, param_names)

    side_effects = entry.get("side_effects", [])
    if not isinstance(side_effects, list):
        errs.error(where, f"side_effects 必须是 list，got: {_type_name(side_effects)}")
    else:
        for index, effect in enumerate(side_effects):
            _check_side_effect(effect, f"{where}.side_effects[{index}]", errs)


_FAMILY_CHECKERS = {
    "object_types": _check_object_types,
    "properties": _check_properties,
    "link_types": _check_link_types,
    "functions": _check_functions,
    "actions": _check_actions,
}


# —— 主流程 ——


def validate_file(path: Path) -> _Errors:
    errs = _Errors(str(path))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errs.error("文件", f"读取失败：{exc}")
        return errs
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        errs.error("YAML", f"解析失败：{exc}")
        return errs
    if data is None:
        errs.error("YAML", "文件为空")
        return errs
    if not isinstance(data, dict):
        errs.error("顶层", f"必须是 YAML mapping，got: {_type_name(data)}")
        return errs

    _check_top_level(data, errs)
    names = _collect_names(data, errs)

    # Pass 2：引用解析 + 家族形状检查
    for family in _FAMILY_KEYS:
        entries = data.get(family, [])
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            where = _where(family, index, name if isinstance(name, str) else None)
            _walk_refs(entry, names, "", where, errs)
            _FAMILY_CHECKERS[family](entry, where, errs)
    errs.counts = {family: len(data.get(family, []) or []) for family in _FAMILY_KEYS}
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"TFOnto (.tfo) 独立校验器（schema_version={SCHEMA_VERSION}，零 tfrobot 依赖）"
    )
    parser.add_argument("files", nargs="+", help="一个或多个 .tfo 文件")
    parser.add_argument("--version", action="version", version=f"validate_tfonto v{SCHEMA_VERSION}")
    args = parser.parse_args(argv)

    failed = 0
    for raw in args.files:
        path = Path(raw)
        errs = validate_file(path)
        print(f"== {path} ==")
        for warning in errs.warnings:
            print(f"  ⚠ {warning}")
        if errs.ok():
            summary = "  ".join(f"{family}={errs.counts[family]}" for family in _FAMILY_KEYS)
            print(f"  ✅ 校验通过（{summary}）")
        else:
            failed += 1
            for error in errs.errors:
                print(f"  ❌ {error}")
            print(f"  ❌ 共 {len(errs.errors)} 个错误")
        print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
