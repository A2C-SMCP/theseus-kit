from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.check_release import normalize_tag, validate, validate_release_type, wheel_version


def test_normalize_tag() -> None:
    assert normalize_tag("v0.1.0rc1") == "0.1.0rc1"
    assert normalize_tag("0.1.0") == "0.1.0"


def test_normalize_tag_rejects_empty_value() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        normalize_tag("v")


def test_validate_matches_pyproject(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.1.0.dev0"\n', encoding="utf-8")
    validate("v0.1.0.dev0", pyproject, "prerelease")


def test_validate_rejects_mismatched_tag(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.1.0.dev0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="版本不一致"):
        validate("v0.1.0", pyproject, "final")


@pytest.mark.parametrize("version", ["0.1.0.dev1", "0.1.0a1", "0.1.0b1", "0.1.0rc1"])
def test_prerelease_versions_require_github_prerelease(version: str) -> None:
    validate_release_type(version, "prerelease")
    with pytest.raises(ValueError, match="不能通过 GitHub 正式 Release"):
        validate_release_type(version, "final")


def test_final_version_requires_github_final_release() -> None:
    validate_release_type("0.1.0", "final")
    with pytest.raises(ValueError, match="不能通过 GitHub Pre-release"):
        validate_release_type("0.1.0", "prerelease")


def test_wheel_version_reads_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "theseus_kit-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("theseus_kit-0.1.0.dist-info/METADATA", "Name: theseus-kit\nVersion: 0.1.0\n")
    assert wheel_version(wheel) == "0.1.0"
