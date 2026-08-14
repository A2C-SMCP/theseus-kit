"""Skill: enhance — 使用反馈闭环：向 theseus-kit 提交改进 Issue。

The feedback loop that keeps the three-stage pipeline honest.  When the user
hits a bad experience or a product defect, collect redacted context, pair the
problem with a concrete suggestion, and file an Issue against this repository
via ``gh`` — the maintainer reviews every one.  Upstream problems
(TFRobotServer / tfrs) are equally welcome: ownership is shared, proposals
are routed by the maintainer.
"""

from __future__ import annotations

from ._registry import SkillDef

SKILL = SkillDef(
    name="enhance",
    description="使用反馈闭环——用户体验不佳或发现产品缺陷时，脱敏收集上下文并用 gh 向 theseus-kit 仓库提交改进 Issue，由维护者审核处理。",
    content={
        "SKILL.md": """---
name: enhance
description: 使用反馈闭环——机器人用户体验不佳或发现本仓库产品缺陷（技能、工具、校验器、文档）时，脱敏收集上下文、问题与建议成对，用 gh 向 theseus-kit 仓库提交改进 Issue，由维护者审核处理。当用户说「这里体验不好 / 有个问题 / 提个 Issue」或发现缺陷时使用。
---

# Enhance —— 向 theseus-kit 提交改进 Issue

## 使命

把「体验不佳」变成可审核、可追踪的 Issue。本仓库（`github.com/A2C-SMCP/theseus-kit`）与上游 TFRobotServer / tfrs 相关库均由同一维护者维护——发现问题不要憋着，合理建议都会被认真审核（CLAUDE.md 三段式开发思路：上下游都可以提意见）。

## 流程

1. **收集上下文**：哪个技能/工具、输入是什么、期望行为、实际行为、复现步骤、相关日志摘录（**脱敏后**）
2. **先想建议**：问题与建议成对提交——只报问题不报方案，处理周期更长
3. **提交 Issue**：用 gh 向本仓库提交：

   ```bash
   gh issue create -R A2C-SMCP/theseus-kit --title "<哪个面 + 什么现象>" --body "<正文>"
   ```

   正文建议结构：现象 / 复现步骤 / 期望行为 / 建议方案 / 影响面
4. **告知用户**：给出 Issue 编号与链接，由维护者审核处理

## 提交纪律

- 任何令牌形态（PAT、JWT 等）禁止进入 Issue——提交前做脱敏检查
- 不贴大段配置原文：给结构摘要 + 最小复现片段
- 涉及上游（TFRobotServer / tfrs）的改进建议同样提本仓库 Issue，由维护者裁决归属与推动
- 一 Issue 一个问题；多个问题拆多个
""",
    },
)
