#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# filename: validate_tfonto.py
"""独立 TFOnto (.tfo) 校验器 —— 不依赖 tfrobot 库，仅需 PyYAML。

Epic / Story 归属
-----------------
- Epic: **TFROB-606**（OntoStore 与 GraphIndexV4）。
- Story: **TFROB-728**（.tfo 原生设计交换格式）—— 本脚本是其独立交付校验门（persona-interview SKILL 资源）。

使命
----
给运营/需求方一个零 tfrobot 依赖的自检入口：提交机器人知识图谱 .tfo 前跑通本脚本，
确保文件能被平台导入器接受。镜像 ``tfrobot/schema/onto/importers/tfo.py``（两遍符号
解析、重名 fail-loud、id 格式）与 OntologySnapshot v7 元模型（``extra="forbid"`` 字段
白名单）的校验语义——刻意不 import tfrobot（依赖太重），仅 PyYAML。

边界（诚实声明）
----------------
- 深层 AST（ValueExpr / Expr / ObjectSetExpr 的完整形状）与 JSON Schema 全文校验由
  平台导入时执行，本脚本只做形状级检查（mapping/list/str/bool 与关键字段）。
- schema_version：本脚本只接受当前版本（7）或缺省；旧版本文件平台导入会自动迁移，
  但新提交请写当前版本。
- namespace 要求显式声明（平台有文件名兜底，但运营提交要求显式——校验器更严的唯一一处）。

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
    {"primary_property", "inverse_of", "object_type", "link_type", "function_id"}
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
    for key in data:
        if key not in _TOP_LEVEL_KEYS:
            errs.warning(f"顶层未知键 {key!r} 会被平台导入静默丢弃，请确认是否拼写错误")

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
    if ref == "script":
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

    if edit_set is not None:
        if not isinstance(edit_set, dict):
            errs.error(where, f"edit_set 必须是 mapping，got: {_type_name(edit_set)}")
        else:
            ops = edit_set.get("ops", [])
            if not isinstance(ops, list):
                errs.error(where, f"edit_set.ops 必须是 list，got: {_type_name(ops)}")
            else:
                for index, op in enumerate(ops):
                    if not isinstance(op, dict):
                        errs.error(where, f"edit_set.ops[{index}] 必须是 mapping")
                        continue
                    tag = op.get("op")
                    if tag not in _OP_TAGS:
                        errs.error(where, f"edit_set.ops[{index}] 非法 op tag {tag!r}（六种 EditOp 之一）")

    if backing is not None:
        _check_function_ref(backing, f"{where}.backing", errs)

    parameters = entry.get("parameters", [])
    if not isinstance(parameters, list):
        errs.error(where, f"parameters 必须是 list，got: {_type_name(parameters)}")
    else:
        for index, param in enumerate(parameters):
            if not isinstance(param, dict):
                errs.error(where, f"parameters[{index}] 必须是 mapping")
                continue
            if not isinstance(param.get("name"), str):
                errs.error(where, f"parameters[{index}] 缺少 str 类型的 name")
            if "type" in param:
                _check_json_schema_fragment(param["type"], f"{where}.parameters[{index}].type", errs)

    if "submission_criteria" in entry and not isinstance(entry["submission_criteria"], list):
        errs.error(where, f"submission_criteria 必须是 list，got: {_type_name(entry['submission_criteria'])}")
    if "side_effects" in entry and not isinstance(entry["side_effects"], list):
        errs.error(where, f"side_effects 必须是 list，got: {_type_name(entry['side_effects'])}")


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
