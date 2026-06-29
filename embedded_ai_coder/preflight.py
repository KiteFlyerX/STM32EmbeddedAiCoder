"""环境自检:检测构建/烧录/定位所需工具链是否就绪(Windows 尤为有用)。

编译 STM32 C 本质需要 arm-none-eabi-gcc + 构建驱动(make/cmake);烧录用 pyOCD(pip);
故障定位用 arm-none-eabi-addr2line。本模块检测这些工具是否在 PATH / 已安装,
并给出 Windows 安装提示,避免 "'make' 不是内部命令" 之类让人懵的报错。

CLI:``embedded-ai-coder check-env``;闭环启动前也会跑一遍,缺关键项写日志。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import shutil
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolCheck:
    name: str            # 工具名
    purpose: str         # 用途
    found: bool          # 是否就绪
    where: str           # 路径 / 版本 / "未安装"
    critical: bool       # 缺了是否阻断核心流程
    windows_hint: str    # Windows 安装提示


def _which(name: str) -> str:
    """返回工具在 PATH 的路径;Windows 自动补 .exe/.bat。未找到返回 ''。"""
    return shutil.which(name) or ""


# Windows 上 Arm GNU Toolchain 常见安装根(用于 PATH 未配置时自动发现)
_ARM_INSTALL_ROOTS = [
    r"C:\Program Files (x86)\Arm",
    r"C:\Program Files\Arm",
    r"C:\Arm",
    r"D:\Arm",
]


def find_toolchain_bin(configured: str = "") -> str:
    """定位 arm-none-eabi-gcc 所在 bin 目录,供构建时加到 PATH(免去配系统 PATH)。

    优先级:显式 configured > PATH > 常见安装目录递归探测 > ''。
    """
    import glob
    if configured:
        cand = Path(configured)
        if (cand / "arm-none-eabi-gcc.exe").exists() or (cand / "arm-none-eabi-gcc").exists():
            return str(cand)
    p = _which("arm-none-eabi-gcc")
    if p:
        return str(Path(p).parent)
    for base in _ARM_INSTALL_ROOTS:
        if Path(base).exists():
            for exe in glob.glob(base + r"\**\bin\arm-none-eabi-gcc.exe", recursive=True):
                return str(Path(exe).parent)
    return ""


def check_env() -> list[ToolCheck]:
    """检测全部工具,返回 ToolCheck 列表。"""
    checks: list[ToolCheck] = []

    def add(name: str, purpose: str, critical: bool, hint: str,
            locate: Callable[[], str]):
        where = locate()
        checks.append(ToolCheck(name, purpose, bool(where), where or "未安装",
                                critical, hint))

    add("arm-none-eabi-gcc", "ARM 交叉编译器(编译 STM32 C)", True,
        "装 Arm GNU Toolchain(arm-gnu.toolchain.windows);加 bin 到 PATH",
        lambda: find_toolchain_bin() or "")
    add("make", "构建驱动(Makefile 工程)", False,
        "MSYS2: pacman -S make;或 choco install make;或用 cmake 替代",
        lambda: _which("make"))
    add("cmake", "构建驱动(CMake 工程)", False,
        "cmake.org 下载 / choco install cmake;配 Ninja 更快",
        lambda: _which("cmake"))
    add("ninja", "CMake 后端(可选,更快)", False,
        "choco install ninja 或 pip install ninja",
        lambda: _which("ninja"))
    add("arm-none-eabi-objcopy", "生成 .bin/.hex(工具链自带)", False,
        "随 Arm GNU Toolchain 安装",
        lambda: _which("arm-none-eabi-objcopy"))
    add("arm-none-eabi-addr2line", "HardFault 栈地址→源码行(F-09)", False,
        "随 Arm GNU Toolchain 安装",
        lambda: _which("arm-none-eabi-addr2line"))
    # pyOCD:优先 python 包,次之 CLI
    def _pyocd() -> str:
        if importlib.util.find_spec("pyocd"):
            return "python 包 pyocd(已安装)"
        cli = _which("pyocd")
        return cli
    add("pyOCD", "SWD 烧录 + RTT(pyOCD)", True,
        "pip install pyocd(本项目依赖,应已装)",
        _pyocd)
    add("STM32_Programmer_CLI", "可选烧录后端(STM32CubeProgrammer)", False,
        "装 STM32CubeProgrammer;加 bin 到 PATH",
        lambda: _which("STM32_Programmer_CLI"))
    return checks


def format_report(checks: list[ToolCheck]) -> str:
    """渲染成可读文本。"""
    lines = ["环境自检:"]
    for c in checks:
        mark = "✓" if c.found else ("✗" if c.critical else "·")
        lines.append(f"  {mark} {c.name:<26} {c.purpose}")
        if c.found:
            lines.append(f"      → {c.where}")
        else:
            tag = "[阻断]" if c.critical else "[可选]"
            lines.append(f"      {tag} 未就绪 — {c.windows_hint}")
    missing_critical = [c.name for c in checks if c.critical and not c.found]
    if missing_critical:
        lines.append(f"\n⚠ 缺少关键项:{', '.join(missing_critical)} —— "
                     "补齐前自动编译/烧录无法真跑(可在演示模式下验证流程)。")
    else:
        lines.append("\n✓ 关键工具就绪,可跑真实自动编译/烧录。")
    return "\n".join(lines)


def log_warnings(checks: list[ToolCheck] | None = None) -> list[str]:
    """把缺失的关键工具写进日志,返回缺失关键项名称列表。"""
    checks = checks if checks is not None else check_env()
    missing = [c.name for c in checks if c.critical and not c.found]
    for c in checks:
        if not c.found:
            logger.warning("环境缺 %s(%s):%s", c.name, c.purpose, c.windows_hint)
    if missing:
        logger.warning("缺少关键工具 %s,自动编译/烧录将无法真跑", missing)
    return missing
