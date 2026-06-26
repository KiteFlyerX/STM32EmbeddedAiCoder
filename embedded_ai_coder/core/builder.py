"""
构建:Builder(M1 最小实现)。

按 config.project.build_command 调 make / cmake;空命令则跳过(M1 可不构建,
直接用已有固件烧录,或 dry-run 时整体跳过)。编译错误回喂 AI 自愈留 F-08(M2)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .interfaces import Builder

logger = logging.getLogger(__name__)


class MakeBuilder(Builder):
    """make / cmake 构建器(最小实现)。"""

    def __init__(self, project_root: str | Path, build_command: str = "",
                 *, dry_run: bool = False):
        self.root = Path(project_root)
        self.build_command = build_command.strip()
        self.dry_run = dry_run

    def build(self) -> tuple[bool, str]:
        """执行构建,返回 (是否成功, 输出日志)。无命令则视为跳过(成功)。"""
        if not self.build_command:
            logger.info("未配置 build_command,跳过构建(M1 可选)")
            return True, "(未配置构建命令,跳过)"
        if self.dry_run:
            logger.info("[dry-run] 跳过真实构建,模拟成功")
            return True, "(dry-run: 跳过构建)"
        logger.info("[build] %s(cwd=%s)", self.build_command, self.root)
        try:
            proc = subprocess.run(
                self.build_command, shell=True, cwd=str(self.root),
                capture_output=True, text=True, timeout=300.0,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return False, "构建超时"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return proc.returncode == 0, out


def make_builder(config: dict, *, dry_run: bool = False) -> Builder:
    project = config.get("project", {}) or {}
    return MakeBuilder(
        project_root=project.get("root", "."),
        build_command=project.get("build_command", ""),
        dry_run=dry_run,
    )
