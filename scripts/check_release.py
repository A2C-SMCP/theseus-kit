"""校验发布 Tag、项目版本与构建产物版本一致。"""

from __future__ import annotations

import argparse
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Literal

from packaging.version import InvalidVersion, Version

ReleaseType = Literal["prerelease", "final"]


def normalize_tag(tag: str) -> str:
    """将 ``v1.2.3`` 形式的 Tag 转成包版本。"""
    normalized = tag.removeprefix("v")
    if not normalized:
        raise ValueError("发布 Tag 不能为空")
    return normalized


def project_version(pyproject: Path) -> str:
    """读取 pyproject.toml 中的项目版本。"""
    with pyproject.open("rb") as stream:
        data = tomllib.load(stream)
    return str(data["project"]["version"])


def wheel_version(wheel: Path) -> str:
    """从 Wheel 的 METADATA 读取实际打包版本。"""
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError(f"Wheel 必须且只能包含一份 METADATA，实际为 {len(metadata_names)}")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    for line in metadata.splitlines():
        if line.startswith("Version: "):
            return line.removeprefix("Version: ").strip()
    raise ValueError("Wheel METADATA 缺少 Version 字段")


def validate_release_type(version: str, release_type: ReleaseType) -> None:
    """校验 PEP 440 版本类别与 GitHub Release 类型严格一致。"""
    parsed = Version(version)
    is_prerelease = parsed.is_prerelease or parsed.is_devrelease
    if release_type == "prerelease" and not is_prerelease:
        raise ValueError(f"正式版本 {version} 不能通过 GitHub Pre-release 发布到 TestPyPI")
    if release_type == "final" and is_prerelease:
        raise ValueError(f"预发行版本 {version} 不能通过 GitHub 正式 Release 发布到 PyPI")


def validate(tag: str, pyproject: Path, release_type: ReleaseType, wheel: Path | None = None) -> None:
    """校验 Tag、项目版本、Release 类型，并可选校验 Wheel 版本。"""
    expected = normalize_tag(tag)
    actual = project_version(pyproject)
    if actual != expected:
        raise ValueError(f"版本不一致：pyproject={actual}，tag={expected}")
    validate_release_type(expected, release_type)
    if wheel is not None:
        packaged = wheel_version(wheel)
        if packaged != expected:
            raise ValueError(f"构建产物版本不一致：wheel={packaged}，tag={expected}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="GitHub Release Tag，例如 v0.1.0rc1")
    parser.add_argument("--release-type", choices=("prerelease", "final"), required=True)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--wheel", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate(args.tag, args.pyproject, args.release_type, args.wheel)
    except (InvalidVersion, KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"release version validated: {normalize_tag(args.tag)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
