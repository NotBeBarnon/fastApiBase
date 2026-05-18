# -*- coding: utf-8 -*-
# @Description : cx_Freeze 编译脚本（Python 3.12）
from __future__ import annotations

import platform
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from cx_Freeze import Executable, setup

from src.version import VERSION

PROJECT_DIR: Path = Path(__file__).parent


def get_python_packages(file_path: Path) -> Iterator[str]:
    """查找目录下所有 python 包"""
    if not file_path.is_dir():
        return
    for child in file_path.iterdir():
        if child.is_dir() and any(child.glob("__init__.py")):
            yield child.name


def get_all_packages() -> Iterator[str]:
    """获取 site-packages 中所有的 python 包"""
    site_regex = re.compile(r"[\\/]site-packages")
    site_path: Path | None = None
    for path in sys.path:
        if site_regex.search(path):
            site_path = Path(path)
    if site_path:
        yield from get_python_packages(site_path)


def get_all_requirements() -> Iterator[str]:
    package_regex = re.compile(r"[a-zA-Z\-_]+")
    with (PROJECT_DIR / "requirements.txt").open(encoding="utf-8") as f:
        for line in f:
            m = package_regex.match(line)
            if m:
                yield m.group().replace("-", "_")


packages: list[str] = []
if platform.system() == "Linux":
    packages.append("uvloop")

requirements_str = "-".join(get_all_requirements())
for pack in get_all_packages():
    if pack in requirements_str:
        packages.append(pack)

build_exe_options = {
    "include_files": ["project_env", "pyproject.toml"],
    "packages": packages,
    "excludes": [],
}

setup(
    name="FastSample",
    version=VERSION,
    description="FastAPI Sample",
    options={"build_exe": build_exe_options},
    executables=[Executable("main.py")],
)
