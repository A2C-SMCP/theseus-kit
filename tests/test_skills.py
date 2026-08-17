"""Tests for Issue #10 — skill:// resource projection (refactored 2026-08).

Validates the 5-category hierarchical skill structure: main SKILL.md resources
plus sub-resources (references/*.md). Covers both new URIs and deprecated
legacy aliases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from theseus_kit.config import TheseusSettings
from theseus_kit.server import create_mcp_server

_SKILL_NS = "skill://com.a2c-smcp.theseus-kit"

# New skill categories
_NEW_SKILLS = (
    "analyze-config",
    "apply-config-plan",
    "enhance",
    "manage-topology",
    "persona-interview",
    "persona-optimize",
    "plan-config",
    "publish-config",
    "save-template",
    "theseus",
    "tune-config",
    "write-tfonto",
)

# Legacy aliases (deprecated)
_LEGACY_ALIASES = {
    "inspect-robot-config": "analyze-config",
    "edit-robot-draft": "tune-config",
    "publish-robot-config": "publish-config",
}

_ALL_SKILL_NAMES = (*_NEW_SKILLS, *_LEGACY_ALIASES)


# -- Helpers ---------------------------------------------------------------


def _settings() -> TheseusSettings:
    return TheseusSettings(
        robot={
            "robot_id": "test-robot",
            "namespace": "default",
            "robot_type": "tfrobot",
            "api_base_url": "https://localhost:1",
            "manager_base_url": "https://localhost:1",
        },
        credential={
            "kind": "user_pat",
            "pat": SecretStr("tfp_test_pat"),
            "robot_public_id": "test:1",
        },
    )


def _read_text(result: Any) -> str:
    items = list(result)
    assert len(items) == 1
    content: str | bytes = items[0].content
    if isinstance(content, bytes):
        return content.decode()
    return str(content)


# -- Resource listing ------------------------------------------------------


@pytest.mark.parametrize("skill_name", _NEW_SKILLS)
async def test_each_new_skill_resource_exists(skill_name: str) -> None:
    """Each new skill category has a main resource."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert f"{_SKILL_NS}/{skill_name}" in uris


@pytest.mark.parametrize("legacy_name", _LEGACY_ALIASES)
async def test_each_legacy_skill_resource_exists(legacy_name: str) -> None:
    """Each legacy skill name still resolves."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert f"{_SKILL_NS}/{legacy_name}" in uris


async def test_sub_resources_exist() -> None:
    """Sub-resources (references/*.md) appear as separate MCP resources."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}

    # Check that analyze-config has its sub-resources.  Each skill also exposes
    # its own SKILL.md as a sub-resource — the mode C registrable shape the SDK
    # Computer stages from (the root is only the declaration node).
    expected_subs = [
        f"{_SKILL_NS}/analyze-config/SKILL.md",
        f"{_SKILL_NS}/analyze-config/references/llmtext-strategy.md",
        f"{_SKILL_NS}/analyze-config/references/needs-extraction.md",
        f"{_SKILL_NS}/manage-topology/references/factory-selection.md",
        f"{_SKILL_NS}/persona-interview/references/persona-example.md",
        f"{_SKILL_NS}/theseus/references/persona-format.md",
        f"{_SKILL_NS}/theseus/references/workspace-layout.md",
        f"{_SKILL_NS}/theseus/references/context-isolation.md",
        f"{_SKILL_NS}/theseus/references/plan-format.md",
        f"{_SKILL_NS}/tune-config/references/field-design.md",
        f"{_SKILL_NS}/tune-config/references/conflict-resolution.md",
        f"{_SKILL_NS}/tune-config/references/validation-strategy.md",
        f"{_SKILL_NS}/publish-config/references/preflight-deep-dive.md",
        f"{_SKILL_NS}/write-tfonto/references/tfonto-format.md",
        f"{_SKILL_NS}/write-tfonto/references/capability-layer.md",
        f"{_SKILL_NS}/write-tfonto/references/teacher-math-example.md",
        f"{_SKILL_NS}/write-tfonto/references/teacher-math.tfo",
        f"{_SKILL_NS}/write-tfonto/references/engineering-memory-example.md",
        f"{_SKILL_NS}/write-tfonto/references/engineering-memory.tfo",
        f"{_SKILL_NS}/write-tfonto/scripts/validate_tfonto.py",
    ]
    for uri in expected_subs:
        assert uri in uris, f"Missing sub-resource: {uri}"


async def test_resource_count() -> None:
    """Total skill resources is 12 main + 12 SKILL.md subs + 19 sub + 3 legacy = 46 (plus 2 window = 48)."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]
    # 12 main + 12 SKILL.md subs + 19 sub + 3 legacy = 46
    assert len(skill_uris) == 46, f"Expected 46 skill resources, got {len(skill_uris)}: {skill_uris}"


# -- Resource annotations ---------------------------------------------------


async def test_main_skill_annotations() -> None:
    """Main skill resources have audience=assistant, priority=0.7, markdown, versioned meta."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    for skill_name in _NEW_SKILLS:
        matching = [r for r in resources if str(r.uri) == f"{_SKILL_NS}/{skill_name}"]
        assert len(matching) == 1, f"Missing main resource for {skill_name}"
        r = matching[0]
        assert r.annotations is not None
        assert r.annotations.audience == ["assistant"]
        assert r.annotations.priority == 0.7
        assert r.mimeType == "text/markdown"
        assert r.description, f"{r.uri}: description is empty"
        # A2C-SMCP skill.md §3: mode C declaration + version for update detection.
        assert r.meta is not None
        assert r.meta.get("source") == "resources"
        assert r.meta.get("version"), f"{r.uri}: missing version meta"


async def test_source_meta_only_on_skill_roots() -> None:
    """skill.md §3: staging mode is declared on SKILL roots only.

    Sub-resources are discovered by URI prefix (``skill://<root>/**``) and
    legacy aliases are URI-level compat shims — neither may carry ``source``,
    or the SDK Computer would attempt to stage each one as an independent
    skill root (noise, duplicate registrations, ERROR logs).
    """
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    for r in resources:
        uri = str(r.uri)
        if not uri.startswith(_SKILL_NS):
            continue
        leaf = uri.removeprefix(_SKILL_NS).strip("/")
        if leaf in _NEW_SKILLS:
            assert r.meta is not None and r.meta.get("source") == "resources", f"{uri}: root must declare source"
        else:
            assert r.meta is None or "source" not in r.meta, f"{uri}: sub/legacy resource must not declare source"


async def test_legacy_resource_annotations() -> None:
    """Legacy resources have lower priority and deprecation metadata."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    for legacy_name in _LEGACY_ALIASES:
        matching = [r for r in resources if str(r.uri) == f"{_SKILL_NS}/{legacy_name}"]
        assert len(matching) == 1
        r = matching[0]
        assert r.annotations is not None
        assert r.annotations.priority == 0.6, f"{legacy_name}: priority should be 0.6"
        assert r.meta is not None
        assert r.meta.get("deprecated") is True, f"{legacy_name}: missing deprecated flag"
        assert "migrated_to" in r.meta, f"{legacy_name}: missing migrated_to"


# -- Resource content ------------------------------------------------------


async def test_read_analyze_config() -> None:
    """analyze-config SKILL.md contains key tool references."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/analyze-config")
    content = _read_text(result)

    assert "get_config_summary" in content
    assert "get_llms_doc" in content
    assert "list_config_nodes" in content
    assert "get_config_detail" in content
    assert "llmtext-strategy" in content  # sub-resource reference
    assert "needs-extraction" in content


async def test_read_manage_topology() -> None:
    """manage-topology SKILL.md contains create_draft, delete, and factory selection."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/manage-topology")
    content = _read_text(result)

    assert "create_draft" in content
    assert "update_draft" in content
    assert "factory-catalog" in content
    assert "factory-selection" in content
    assert "tune-config" in content  # cross-reference


async def test_read_tune_config() -> None:
    """tune-config SKILL.md contains update_draft, expected_hash, and sub-resources."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/tune-config")
    content = _read_text(result)

    assert "update_draft" in content
    assert "expected_hash" in content
    assert "conflict-resolution" in content
    assert "validation-strategy" in content
    assert "field-design" in content


async def test_read_save_template() -> None:
    """save-template SKILL.md contains save_template, naming conventions, and design principles."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/save-template")
    content = _read_text(result)

    assert "save_template" in content
    assert "get_template" in content
    assert "好模板的特征" in content
    assert "粒度选择" in content


async def test_read_publish_config() -> None:
    """publish-config SKILL.md contains publish_config, acknowledge_publish, and warnings."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/publish-config")
    content = _read_text(result)

    assert "publish_config" in content
    assert "acknowledge_publish" in content
    assert "config:publish" in content
    assert "不可逆" in content
    assert "preflight-deep-dive" in content


async def test_read_persona_interview() -> None:
    """persona-interview SKILL.md contains the interview flow, capability sketch, and write-tfonto handoff."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/persona-interview")
    content = _read_text(result)

    assert "职业领域" in content
    assert "专业技能" in content
    assert "日常工作" in content
    assert "知识结构" in content
    assert "三张清单" in content  # structured-NL knowledge structure
    assert "能力草图" in content  # Function/Action candidate extraction
    assert "状态流转" in content  # lifecycle probing → ActionDef candidates
    assert "连带变更" in content  # multi-property write probing (Action 核心价值)
    assert "故障经验" in content  # past-failure probing → recall & regression
    assert "ActionDef" in content
    assert "write-tfonto" in content  # conversion handoff
    assert "persona-example" in content  # sub-resource reference
    assert "workspaces" in content  # 落盘到机器人工作区（阶段交接物）
    assert "工具需求" in content  # 自然语言工具需求追问（技术要求）
    assert "选型" in content  # 选型留给阶段二（采访纪律）


async def test_read_write_tfonto() -> None:
    """write-tfonto SKILL.md contains the NL→TFOnto mapping rules and self-check gate."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/write-tfonto")
    content = _read_text(result)

    assert "object_types" in content
    assert "properties" in content
    assert "link_types" in content
    assert "trigger_words" in content
    assert "schema_version" in content
    assert "自检" in content  # delivery gate
    assert "functions" in content  # capability sketch → Function/Action
    assert "capability-layer" in content  # scenario-combination reference
    assert "validate_tfonto" in content  # shipped validator gate
    assert "${TFROBOT_SKILL_DIR}" in content  # A2C script execution (skill.md §9.4)
    assert "persona-interview" in content  # upstream skill reference


async def test_read_persona_example() -> None:
    """persona-example covers all persona sections incl. the three KG lists and capability sketch."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/persona-interview/references/persona-example.md")
    content = _read_text(result)

    assert "职业领域" in content
    assert "专业技能" in content
    assert "日常工作" in content
    assert "概念清单" in content
    assert "数据字段清单" in content
    assert "关系清单" in content
    assert "能力草图" in content
    assert "状态流转" in content  # new collection axis demonstrated
    assert "ActionDef" in content
    assert "工具需求" in content  # natural-language tool needs subsection


async def test_read_teacher_math_example() -> None:
    """write-tfonto example resource embeds the validated .tfo."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/write-tfonto/references/teacher-math-example.md")
    content = _read_text(result)

    assert "schema_version: 7" in content
    assert "namespace: math-teacher" in content
    assert "object_types" in content
    assert "trigger_words" in content
    assert "primary_property" in content
    assert "capability-layer" in content  # pointer to the functions/actions example


async def test_read_engineering_memory_example() -> None:
    """engineering-memory example covers the state-transition and multi-property Action showcase."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/write-tfonto/references/engineering-memory-example.md")
    content = _read_text(result)

    assert "engineering-memory.tfo" in content  # pointer to the complete file
    assert "状态流转" in content  # persona-interview new collection axis
    assert "trace_requirement" in content
    assert "record_incident" in content
    assert "supersede_decision" in content
    assert "4 个属性 + 2 条边" in content  # Action 多属性变更核心价值
    assert "逐个 Property 修改" in content


async def test_read_theseus() -> None:
    """theseus master skill defines the 3-stage pipeline, workspace layout, and dispatch."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/theseus")
    content = _read_text(result)

    assert "三阶段" in content or "阶段一" in content  # 流水线总纲
    assert "persona-interview" in content  # 阶段一分派
    assert "persona-optimize" in content
    assert "plan-config" in content  # 阶段二分派
    assert "apply-config-plan" in content  # 阶段三分派
    assert "~/.theseus/workspaces/" in content  # 工作区约定
    assert "persona-format" in content  # 画像规范格式引用
    assert "workspace-layout" in content  # 布局细则引用
    assert "context-isolation" in content  # 上下文隔离与交接引用
    assert "plan-format" in content  # 计划文件规范格式引用
    assert "安全不变量" in content  # 凭证不出进程、发布显式


async def test_read_persona_optimize() -> None:
    """persona-optimize drives incremental changes with reasons into the changelog section."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/persona-optimize")
    content = _read_text(result)

    assert "变更记录" in content  # changelog section of persona.md
    assert "为什么" in content  # rationale probing
    assert "技术要求" in content  # toolchain-needs impact tagging
    assert "plan-config" in content  # knowledge-graph changes hand off


async def test_read_plan_config() -> None:
    """plan-config: persona → configs/ artifacts + progressive disclosure + SubTask + plan file."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/plan-config")
    content = _read_text(result)

    assert "configs/" in content  # structured config artifacts
    assert "plans/" in content  # plan file output
    assert "SubTask" in content  # progress tracking
    assert "get_config_summary" in content  # progressive disclosure entry
    assert "list_config_nodes" in content
    assert "get_config_detail" in content
    assert "write-tfonto" in content  # atomic skill delegation
    assert "validate_tfonto.py" in content  # validation gate
    assert "context-isolation" in content  # host-isolation mechanism resolved
    assert "toolchain.yaml" in content  # toolchain selection artifact
    assert "in-progress" in content  # plan filename carries status
    assert "并行" in content  # no parallel in-progress plans
    assert "plan-format" in content  # plan format spec reference
    assert "自然语言" in content  # persona tool needs are plain-language; conversion here
    assert "Marketplace" in content  # source 1: official marketplace
    assert "WebSearch" in content  # sources 2/3: open-world search
    assert "自研" in content  # user-provided plugins welcome
    assert "TODO" in content  # heavy details deferred (渐进披露 / TFOnto 本体)


async def test_read_apply_config_plan() -> None:
    """apply-config-plan: plan → Draft → Template decision → validation → publish negotiation."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/apply-config-plan")
    content = _read_text(result)

    assert "create_draft" in content
    assert "update_draft" in content
    assert "save-template" in content  # template negotiation
    assert "validate_draft" in content  # validation gate
    assert "publish_config" in content  # explicit publish negotiation
    assert "不可逆" in content  # publish is irreversible
    assert "in-progress" in content  # reads the single in-progress plan
    assert "强制完成" in content  # force-complete → aborted, user-confirmed
    assert "失败即停" in content  # item failure stops execution, user consulted


async def test_read_enhance() -> None:
    """enhance skill formalizes the feedback loop: redacted context + suggestion → repo Issue."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/enhance")
    content = _read_text(result)

    assert "gh issue create" in content  # filing mechanism
    assert "A2C-SMCP/theseus-kit" in content  # target repo
    assert "脱敏" in content  # redaction discipline
    assert "建议" in content  # problem + suggestion pairing
    assert "TFRobotServer" in content  # upstream proposals welcome


async def test_read_persona_format() -> None:
    """persona-format defines the normative persona.md sections incl. the changelog."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/theseus/references/persona-format.md")
    content = _read_text(result)

    assert "岗位职责" in content
    assert "技术要求" in content
    assert "工作范围" in content
    assert "知识图谱定义" in content
    assert "变更记录" in content  # changelog as standard section
    assert "评审" in content  # user confirmation gate
    assert "必须掌握的工具" in content  # 技术要求用自然语言描述（用户听得懂）
    assert "自然语言" in content  # 工具需求不写专业选型术语，转换在阶段二


async def test_read_context_isolation() -> None:
    """context-isolation: host-agnostic isolation points + per-host guides (Claude Code / Codex / Chain)."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/theseus/references/context-isolation.md")
    content = _read_text(result)

    assert "隔离点位" in content  # when to isolate
    assert ".claude/agents" in content  # Claude Code subagent config example
    assert "theseus-explorer" in content  # concrete subagent definition
    assert "Codex" in content  # same concept, different host
    assert "Chain" in content  # TFRobotServer native isolation unit
    assert "Trace" in content  # trace injection on/off contract
    assert "TODO" in content  # Chain config details deferred until TFRS back + llms.txt evidence
    assert "只依赖文件" in content  # file-based handoff, not session memory


async def test_read_workspace_layout() -> None:
    """workspace-layout details the configs/ artifact design and the plan status-in-filename rule."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/theseus/references/workspace-layout.md")
    content = _read_text(result)

    assert "configs/" in content
    assert "ontology.tfo" in content  # artifact naming example
    assert "validate_tfonto.py" in content  # .tfo gate
    assert "yaml.safe_load" in content  # YAML/JSON gate
    assert "plans/" in content
    assert "in-progress" in content  # plan filename carries completion status
    assert "done" in content  # completed status
    assert "aborted" in content  # force-completed status
    assert "强制完成" in content  # force-complete needs user confirmation
    assert "并行" in content  # at most one in-progress plan
    assert "scripts/" in content
    assert "tmp/" in content
    assert "中间物" in content  # artifacts are intermediate, no auto-sync
    assert "plan-format" in content  # plan format spec pointer


async def test_read_plan_format() -> None:
    """plan-format: 7 fixed sections, artifact-granularity item table, fail-fast status write-back."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/theseus/references/plan-format.md")
    content = _read_text(result)

    assert "目标" in content  # section 1
    assert "依据" in content  # section 2: persona chapters + configs artifacts
    assert "当前配置基线" in content  # section 3: progressive-disclosure survey snapshot
    assert "操作序列" in content  # section 4: item table
    assert "校验门" in content  # section 5: stage-level gates
    assert "发布摘要" in content  # section 6: apply-stage summary
    assert "落实回执" in content  # section 7: apply-stage receipts
    assert "产出物粒度" in content  # item granularity decision
    assert "只追加不重排" in content  # stable item numbering
    assert "失败即停" in content  # fail-fast, discuss with user
    assert "待执行" in content  # item status vocabulary


async def test_read_capability_layer() -> None:
    """capability-layer resource covers FunctionDef × ActionDef concepts, scenarios, and sketch."""
    mcp = create_mcp_server(_settings())
    result = await mcp.read_resource(f"{_SKILL_NS}/write-tfonto/references/capability-layer.md")
    content = _read_text(result)

    assert "FunctionDef" in content
    assert "ActionDef" in content
    assert "逻辑放 Function，治理放 Action" in content
    assert "场景组合表" in content
    assert "口诀" in content
    # capability sketch examples: query 单用 / edit_set 单用 / backing 组合
    assert "diagnose_weak_points" in content
    assert "record_weak_point" in content
    assert "update_mastery" in content
    assert "refresh_mastery" in content
    assert "edit_set" in content
    assert "backing" in content
    assert "side_effects" in content


async def test_sub_resource_mime_types() -> None:
    """Sub-resource MIME types come from the deterministic built-in map (skill.md §6.4)."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    expected = {
        f"{_SKILL_NS}/write-tfonto/references/tfonto-format.md": "text/markdown",
        f"{_SKILL_NS}/write-tfonto/references/teacher-math.tfo": "application/yaml",
        f"{_SKILL_NS}/write-tfonto/references/engineering-memory.tfo": "application/yaml",
        f"{_SKILL_NS}/write-tfonto/scripts/validate_tfonto.py": "text/x-python",
    }
    by_uri = {str(r.uri): r.mimeType for r in resources}
    for uri, mime in expected.items():
        assert by_uri.get(uri) == mime, f"{uri}: expected {mime}, got {by_uri.get(uri)}"


@pytest.mark.parametrize(
    ("example_rel", "expect_marker"),
    [
        ("references/teacher-math.tfo", "math-teacher"),
        ("references/engineering-memory.tfo", "eng-memory"),
    ],
)
def test_shipped_validator_accepts_shipped_examples(tmp_path: Path, example_rel: str, expect_marker: str) -> None:
    """The shipped validator script (executed) accepts every shipped example .tfo."""
    import subprocess
    import sys

    from theseus_kit.skills import build_skill_resource

    script = build_skill_resource("write-tfonto", "scripts/validate_tfonto.py")
    example = build_skill_resource("write-tfonto", example_rel)

    script_path = tmp_path / "validate_tfonto.py"
    script_path.write_text(script, encoding="utf-8")
    example_path = tmp_path / example_rel.rsplit("/", 1)[1]
    example_path.write_text(example, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script_path), str(example_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"validator failed for {example_rel}:\n{proc.stdout}\n{proc.stderr}"
    assert "校验通过" in proc.stdout
    assert expect_marker in example


def test_shipped_validator_rejects_bad_namespace(tmp_path: Path) -> None:
    """The shipped validator rejects an invalid namespace (fail-loud behaviour)."""
    import subprocess
    import sys

    from theseus_kit.skills import build_skill_resource

    script = build_skill_resource("write-tfonto", "scripts/validate_tfonto.py")
    bad = """schema_version: 7
ontology:
  namespace: "bad/namespace"
"""

    script_path = tmp_path / "validate_tfonto.py"
    script_path.write_text(script, encoding="utf-8")
    bad_path = tmp_path / "bad.tfo"
    bad_path.write_text(bad, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script_path), str(bad_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1
    assert "namespace" in proc.stdout


# -- 能力层 AST 形状负向用例（镜像 kinetic.py/expr.py，与平台导入器同语义）--


def _run_validator(tmp_path: Path, name: str, tfo_text: str) -> Any:
    """Write *tfo_text* to tmp and run the shipped validator on it; return the process."""
    import subprocess
    import sys

    from theseus_kit.skills import build_skill_resource

    script = build_skill_resource("write-tfonto", "scripts/validate_tfonto.py")
    script_path = tmp_path / "validate_tfonto.py"
    script_path.write_text(script, encoding="utf-8")
    tfo_path = tmp_path / name
    tfo_path.write_text(tfo_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script_path), str(tfo_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _minimal_action_tfo(*, ops_block: str, extra_blocks: str = "") -> str:
    """Minimal valid envelope with one action whose edit_set.ops is supplied."""
    return f"""schema_version: 7
ontology: {{namespace: t}}
object_types:
  - {{name: T, pos: [noun]}}
properties:
  - {{name: p, domain: [T], data_range: {{type: string}}}}
actions:
  - name: a
    parameters:
      - {{name: x, type: {{type: string}}}}
{extra_blocks}    edit_set:
      ops:
{ops_block}
"""


def test_shipped_validator_rejects_props_on_update_entity(tmp_path: Path) -> None:
    """update_entity's payload is prop_delta — props on it is rejected (the classic mix-up)."""
    proc = _run_validator(
        tmp_path,
        "props_on_update.tfo",
        _minimal_action_tfo(
            ops_block="""        - op: update_entity
          target: {node: param, param: x}
          props: {p: {node: literal, value: hi}}
""",
        ),
    )
    assert proc.returncode == 1
    assert "prop_delta" in proc.stdout  # hint points at the right fix


def test_shipped_validator_rejects_bare_scalar_operand(tmp_path: Path) -> None:
    """Value positions must be operand mappings; bare scalars are rejected."""
    proc = _run_validator(
        tmp_path,
        "bare_scalar.tfo",
        _minimal_action_tfo(
            ops_block="""        - op: update_entity
          target: {node: param, param: x}
          prop_delta:
            - verb: set
              props: {p: hi}
""",
        ),
    )
    assert proc.returncode == 1
    assert "literal" in proc.stdout  # error teaches the {node: literal, value} form


def test_shipped_validator_rejects_prop_verb_conflict(tmp_path: Path) -> None:
    """The same property in two verbs (set+remove) is a semantic ambiguity."""
    proc = _run_validator(
        tmp_path,
        "verb_conflict.tfo",
        _minimal_action_tfo(
            ops_block="""        - op: update_entity
          target: {node: param, param: x}
          prop_delta:
            - verb: set
              props: {p: {node: literal, value: hi}}
            - verb: remove
              props: {p: {node: literal, value: null}}
""",
        ),
    )
    assert proc.returncode == 1
    assert "歧义" in proc.stdout


def test_shipped_validator_rejects_comparison_arity(tmp_path: Path) -> None:
    """Binary comparison operators require a right operand (mirrors kinetic arity rule)."""
    proc = _run_validator(
        tmp_path,
        "arity.tfo",
        _minimal_action_tfo(
            ops_block="""        - op: update_entity
          target: {node: param, param: x}
          prop_delta:
            - verb: set
              props: {p: {node: literal, value: hi}}
""",
            extra_blocks="""    submission_criteria:
      - condition: {node: compare, op: eq, left: {node: param, param: x}}
        message: 需要右操作数
""",
        ),
    )
    assert proc.returncode == 1
    assert "right" in proc.stdout


def test_shipped_validator_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    """Unknown envelope keys fail-loud (typo'd `object_type:` silently evaporates otherwise)."""
    proc = _run_validator(
        tmp_path,
        "bad_envelope.tfo",
        """schema_version: 7
ontology: {namespace: t}
object_type:
  - {name: T, pos: [noun]}
""",
    )
    assert proc.returncode == 1
    assert "object_type" in proc.stdout


def test_shipped_validator_warns_undeclared_param_reference(tmp_path: Path) -> None:
    """Undeclared param operands are import-accepted but binding-fatal — warn, don't fail."""
    proc = _run_validator(
        tmp_path,
        "undeclared_param.tfo",
        _minimal_action_tfo(
            ops_block="""        - op: update_entity
          target: {node: param, param: y}
          prop_delta:
            - verb: set
              props: {p: {node: literal, value: hi}}
""",
        ),
    )
    assert proc.returncode == 0, f"undeclared param must stay a warning:\n{proc.stdout}"
    assert "参数引用 'y'" in proc.stdout


async def test_read_sub_resource() -> None:
    """Each sub-resource can be read independently."""
    mcp = create_mcp_server(_settings())

    sub_uris = [
        f"{_SKILL_NS}/analyze-config/references/llmtext-strategy.md",
        f"{_SKILL_NS}/analyze-config/references/needs-extraction.md",
        f"{_SKILL_NS}/manage-topology/references/factory-selection.md",
        f"{_SKILL_NS}/tune-config/references/field-design.md",
        f"{_SKILL_NS}/tune-config/references/conflict-resolution.md",
        f"{_SKILL_NS}/tune-config/references/validation-strategy.md",
        f"{_SKILL_NS}/publish-config/references/preflight-deep-dive.md",
    ]
    for uri in sub_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        assert len(content) > 0, f"Empty sub-resource: {uri}"


# -- Sensitive-field safety ------------------------------------------------


async def test_skill_content_no_secrets() -> None:
    """No skill content leaks PAT / token / secret values."""
    mcp = create_mcp_server(_settings())

    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for uri in skill_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        for banned in ("tfp_", "Bearer ", "eyJ", "secret:", "password:"):
            assert banned not in content, f"{uri} leaked: {banned!r}"


# -- URI format ------------------------------------------------------------


async def test_skill_uri_format() -> None:
    """All skill URIs follow the no-query conformance rule."""
    from urllib.parse import urlparse

    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for uri in skill_uris:
        parsed = urlparse(uri)
        assert parsed.query == "", f"{uri} must not contain query parameters"
        assert parsed.scheme == "skill", f"{uri} must use skill:// scheme"


# -- Content validity ------------------------------------------------------


async def test_all_skills_utf8() -> None:
    """All skill content is valid UTF-8 text."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]

    for uri in skill_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        content.encode("utf-8").decode("utf-8")
        assert len(content) > 0, f"{uri} is empty"


async def test_each_main_skill_within_8kib() -> None:
    """Each main SKILL.md is <= 8 KiB."""
    mcp = create_mcp_server(_settings())

    for skill_name in _NEW_SKILLS:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 8192, f"{skill_name}: {size_bytes} bytes exceeds 8 KiB"


async def test_each_reference_md_within_4kib() -> None:
    """Each references/*.md sub-resource is <= 4 KiB (context-read prose documents)."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    sub_uris = [
        str(r.uri)
        for r in resources
        if str(r.uri).startswith(_SKILL_NS) and "/references/" in str(r.uri) and str(r.uri).endswith(".md")
    ]

    for uri in sub_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 4096, f"{uri}: {size_bytes} bytes exceeds 4 KiB"


async def test_each_reference_tfo_within_8kib() -> None:
    """Each references/*.tfo example is <= 8 KiB (knowledge-graph data artifacts).

    .tfo 范例是平台导入的数据工件（YAML 本体），不是散文引用——Def 结构与
    能力草图的完整性优先于压到 4 KiB；预算档位与主 SKILL.md 对齐（scripts/
    则单独放宽到 64 KiB）。4 KiB 档仅约束 .md 散文引用。
    """
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    sub_uris = [
        str(r.uri)
        for r in resources
        if str(r.uri).startswith(_SKILL_NS) and "/references/" in str(r.uri) and str(r.uri).endswith(".tfo")
    ]

    assert sub_uris, "expected at least one references/*.tfo example"
    for uri in sub_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 8192, f"{uri}: {size_bytes} bytes exceeds 8 KiB"


async def test_each_script_within_64kib() -> None:
    """Each scripts/* sub-resource is <= 64 KiB (executed, not context-read)."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    script_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS) and "/scripts/" in str(r.uri)]

    assert script_uris, "expected at least one scripts/ sub-resource"
    for uri in script_uris:
        result = await mcp.read_resource(uri)
        content = _read_text(result)
        size_bytes = len(content.encode("utf-8"))
        assert size_bytes <= 65536, f"{uri}: {size_bytes} bytes exceeds 64 KiB"


async def test_skill_content_has_frontmatter() -> None:
    """Each main SKILL.md starts with YAML frontmatter (name + description)."""
    mcp = create_mcp_server(_settings())

    for skill_name in _NEW_SKILLS:
        result = await mcp.read_resource(f"{_SKILL_NS}/{skill_name}")
        content = _read_text(result)
        assert content.startswith("---"), f"{skill_name}: missing frontmatter"
        assert "name:" in content, f"{skill_name}: missing name"
        assert "description:" in content, f"{skill_name}: missing description"


async def test_skill_resource_names_are_unique() -> None:
    """No duplicate skill resource URIs."""
    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()
    skill_uris = [str(r.uri) for r in resources if str(r.uri).startswith(_SKILL_NS)]
    assert len(skill_uris) == len(set(skill_uris)), f"Duplicates: {sorted(skill_uris)}"


async def test_non_existent_skill_returns_error() -> None:
    """Reading a non-existent skill URI surfaces an error."""
    mcp = create_mcp_server(_settings())
    with pytest.raises(ValueError):
        await mcp.read_resource(f"{_SKILL_NS}/nonexistent-skill")


# -- Legacy backward compatibility -----------------------------------------


async def test_legacy_content_matches_new() -> None:
    """Legacy edit-robot-draft content equals new tune-config content."""
    mcp = create_mcp_server(_settings())

    legacy = _read_text(await mcp.read_resource(f"{_SKILL_NS}/edit-robot-draft"))
    new = _read_text(await mcp.read_resource(f"{_SKILL_NS}/tune-config"))
    assert legacy == new


# -- Registry integrity ----------------------------------------------------


async def test_skill_registry_matches_resource_list() -> None:
    """SkillRegistry.all() matches the resources actually registered."""
    from theseus_kit.skills import SkillRegistry

    mcp = create_mcp_server(_settings())
    resources = await mcp.list_resources()

    # All new skill names in registry should have a resource
    for skill in SkillRegistry.all():
        uri = f"{_SKILL_NS}/{skill.name}"
        assert any(str(r.uri) == uri for r in resources), f"Missing resource for {skill.name}"

        # SKILL.md itself is staged from the sub-resource (mode C registrable shape).
        md_uri = f"{_SKILL_NS}/{skill.name}/SKILL.md"
        assert any(str(r.uri) == md_uri for r in resources), f"Missing SKILL.md sub-resource: {md_uri}"

        # And all sub-resources
        for rel_path in skill.sub_resources:
            sub_uri = f"{_SKILL_NS}/{skill.name}/{rel_path}"
            assert any(str(r.uri) == sub_uri for r in resources), f"Missing sub-resource: {sub_uri}"


# -- build_skill_resource direct -------------------------------------------


def test_build_skill_resource_main() -> None:
    """Direct call to build_skill_resource returns non-empty markdown."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("analyze-config")
    assert len(content) > 0
    assert "get_config_summary" in content
    assert content.startswith("---")


def test_build_skill_resource_sub() -> None:
    """build_skill_resource with rel_path returns sub-resource content."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("tune-config", "references/field-design.md")
    assert len(content) > 0
    assert "LLMTEXT" in content


def test_build_skill_resource_legacy() -> None:
    """build_skill_resource resolves legacy skill names."""
    from theseus_kit.skills import build_skill_resource

    content = build_skill_resource("edit-robot-draft")
    assert len(content) > 0
    assert "update_draft" in content


def test_build_skill_resource_unknown_name() -> None:
    """build_skill_resource raises ValueError for unknown names."""
    from theseus_kit.skills import build_skill_resource

    with pytest.raises(ValueError, match="Unknown skill"):
        build_skill_resource("nonexistent-skill")


def test_build_skill_resource_unknown_sub_resource() -> None:
    """build_skill_resource raises ValueError for unknown sub-resource paths."""
    from theseus_kit.skills import build_skill_resource

    with pytest.raises(ValueError, match="Unknown sub-resource"):
        build_skill_resource("tune-config", "references/nonexistent.md")


def test_package_skill_walk_skips_runtime_artifacts(tmp_path: Path) -> None:
    """__pycache__ and dotfiles are never skill content.

    Regression for the wheel-install crash: pip byte-compiles every ``.py``
    at install time, so an installed skill package carries a
    ``__pycache__/…pyc`` that a source checkout never has — the content walk
    must not try to read it as UTF-8.
    """
    from theseus_kit.skills._registry import _iter_content_files

    (tmp_path / "SKILL.md").write_text("# S\n", encoding="utf-8")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "fake.cpython-311.pyc").write_bytes(b"\xa7\x0d\x0d\x0a" + b"\x00" * 12)
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x01\x02")
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / ".hidden_dir" / "x.md").write_text("x\n", encoding="utf-8")

    rel_paths = {p.relative_to(tmp_path).as_posix() for p in _iter_content_files(tmp_path)}

    assert rel_paths == {"SKILL.md", "references/a.md", "scripts/run.py"}
