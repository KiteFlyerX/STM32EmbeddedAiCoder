"""
烧录与复位:PyOcdFlasher(M1)。

pyOCD 烧录 .elf/.bin/.hex + 复位。从 config 读 backend/firmware/probe。
- 优先用 ``pyocd`` 命令行(subprocess,稳定且与 pyOCD 版本解耦);
- 不可用(pyocd 未装 / 无硬件)时优雅降级:日志提示,不抛异常,返回 False。
对应需求 F-05。基于 interfaces.PyOcdFlasher。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .interfaces import Flasher

logger = logging.getLogger(__name__)


class PyOcdFlasher(Flasher):
    """pyOCD 烧录器(命令行封装)。

    构造参数:
      probe: 探针(stlink/jlink/cmsis-dap);
      target: 目标芯片(part number,如 stm32g0b1re);空则让 pyocd 自动识别;
      backend: 预留(pyocd/stm32cubeprogrammer/openocd),M1 只实现 pyocd;
      dry_run: True 时只模拟烧录,不真调 pyocd。
    """

    def __init__(self, probe: str = "stlink", target: str = "",
                 backend: str = "pyocd", firmware: str = "",
                 dry_run: bool = False):
        self.probe = probe
        self.target = target
        self.backend = backend
        self.firmware = firmware  # 默认固件路径(可被 flash(fw=) 覆盖)
        self.dry_run = dry_run

    def flash(self, firmware: str, reset: bool = True) -> bool:
        """烧录固件。返回是否成功。"""
        fw = Path(firmware)
        if not fw.exists():
            logger.error("固件不存在:%s", firmware)
            return False

        if self.dry_run:
            logger.info("[dry-run] 跳过真实烧录,模拟成功:%s", fw.name)
            return True

        if not self._pyocd_available():
            logger.warning("pyocd 不可用(未安装或无硬件),烧录降级跳过:%s", fw.name)
            return False

        cmd = self._build_cmd(fw, reset)
        logger.info("[flash] %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120.0,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            logger.error("烧录超时")
            return False
        except FileNotFoundError as exc:
            logger.error("调用 pyocd 失败:%s", exc)
            return False

        if proc.returncode != 0:
            logger.error("烧录失败(rc=%d):\n%s", proc.returncode,
                         (proc.stderr or proc.stdout)[-800:])
            return False
        logger.info("烧录成功:%s", fw.name)
        if proc.stdout.strip():
            logger.debug("[flash stdout] %s", proc.stdout[-400:])
        return True

    # ---------- 内部 ----------
    def _build_cmd(self, fw: Path, reset: bool) -> list[str]:
        cmd = ["pyocd", "flash", "--probe", self.probe]
        if self.target:
            cmd += ["-t", self.target]
        cmd.append(str(fw))
        if reset:
            cmd.append("--reset")
        return cmd

    def _pyocd_available(self) -> bool:
        """pyocd 命令行是否可用。"""
        if not shutil.which("pyocd"):
            # 尝试用模块入口
            return False
        return True


def make_flasher(config: dict, *, dry_run: bool = False) -> PyOcdFlasher:
    """从 config 构造 Flasher。"""
    flasher_cfg = config.get("flasher", {}) or {}
    debugger_cfg = config.get("debugger", {}) or {}
    project_cfg = config.get("project", {}) or {}
    return PyOcdFlasher(
        probe=debugger_cfg.get("probe", "stlink"),
        target=project_cfg.get("chip", ""),
        backend=flasher_cfg.get("backend", "pyocd"),
        firmware=flasher_cfg.get("firmware", ""),
        dry_run=dry_run,
    )
