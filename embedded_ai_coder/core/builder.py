"""
构建:Builder(M1 最小实现)。

按 config.project.build_command 调 make / cmake;空命令则跳过(M1 可不构建,
直接用已有固件烧录,或 dry-run 时整体跳过)。编译错误回喂 AI 自愈留 F-08(M2)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .interfaces import Builder

logger = logging.getLogger(__name__)


class MakeBuilder(Builder):
    """make / cmake 构建器(最小实现)。"""

    def __init__(self, project_root: str | Path, build_command: str = "",
                 *, dry_run: bool = False, toolchain_bin: str = ""):
        self.root = Path(project_root)
        self.build_command = build_command.strip()
        self.dry_run = dry_run
        # arm-none-eabi-gcc 所在 bin;构建时临时加到 PATH,免去配系统 PATH
        self.toolchain_bin = toolchain_bin

    def _env_with_toolchain(self) -> dict:
        """把 toolchain_bin 前置到 PATH(仅本次构建子进程)。"""
        env = os.environ.copy()
        if self.toolchain_bin:
            env["PATH"] = self.toolchain_bin + os.pathsep + env.get("PATH", "")
        return env

    def build(self) -> tuple[bool, str]:
        """执行构建,返回 (是否成功, 输出日志)。无命令则视为跳过(成功)。"""
        if not self.build_command:
            logger.info("未配置 build_command,跳过构建(M1 可选)")
            return True, "(未配置构建命令,跳过)"
        if self.dry_run:
            logger.info("[dry-run] 跳过真实构建,模拟成功")
            return True, "(dry-run: 跳过构建)"
        logger.info("[build] %s(cwd=%s, toolchain_bin=%s)", self.build_command,
                    self.root, self.toolchain_bin or "(用系统 PATH)")
        try:
            proc = subprocess.run(
                self.build_command, shell=True, cwd=str(self.root),
                env=self._env_with_toolchain(),
                capture_output=True, text=True, timeout=300.0,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return False, "构建超时"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return proc.returncode == 0, out


def make_builder(config: dict, *, dry_run: bool = False) -> Builder:
    project = config.get("project", {}) or {}
    build = config.get("build", {}) or {}
    # toolchain_bin:显式配置优先;否则 preflight 自动发现(常见安装目录)
    tb = (build.get("toolchain_bin") or "").strip()
    if not tb:
        try:
            from ..preflight import find_toolchain_bin
            tb = find_toolchain_bin()
        except Exception:  # noqa: BLE001
            tb = ""
    return MakeBuilder(
        project_root=project.get("root", "."),
        build_command=project.get("build_command", ""),
        dry_run=dry_run,
        toolchain_bin=tb,
    )
