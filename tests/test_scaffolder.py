"""scaffold 单测:用真实 STM32 SDK 验 plan/apply 产物 + Makefile 派生 +(可选)make 出 .elf。

纯 assert,可直接 ``python tests/test_scaffolder.py`` 跑,也兼容 pytest 收集。
无 SDK 时函数 early-return(设环境变量 EAC_SDK_ROOT 指向官方 SDK 包根)。
SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SDK = os.environ.get("EAC_SDK_ROOT") or r"E:\AiTool\STM32_SDK\STM32Cube_FW_G0_V1.6.0"
HAS_SDK = Path(SDK).exists() and Path(SDK).is_dir()


def test_plan_g0b1_abi_and_paths() -> None:
    if not HAS_SDK:
        print("skip(无 SDK)"); return
    from embedded_ai_coder.core.scaffolder import Scaffolder
    with tempfile.TemporaryDirectory() as td:
        plan = Scaffolder(SDK, Path(td) / "fw").plan(
            "STM32G0B1", ["gpio", "rcc", "cortex", "uart", "i2c"])
        assert plan.chip == "STM32G0B1"
        assert plan.family == "STM32G0"
        assert plan.abi["cpu"] == "cortex-m0plus"
        assert plan.abi["float_abi"] == "soft"
        assert "startup" in plan.sdk_paths
        assert "ld" in plan.sdk_paths
        tos = [c["to"] for c in plan.copy_files]
        assert any("startup_stm32g0b1xx.s" in t for t in tos)
        assert any(t.endswith(".ld") for t in tos)
        paths = [g["path"] for g in plan.gen_files]
        assert "Makefile" in paths
        assert "Core/Src/main.c" in paths


def test_makefile_derivation() -> None:
    if not HAS_SDK:
        print("skip(无 SDK)"); return
    from embedded_ai_coder.core.scaffolder import Scaffolder
    with tempfile.TemporaryDirectory() as td:
        mk = next(g["content"] for g in Scaffolder(SDK, Path(td) / "fw").plan("STM32G0B1").gen_files
                  if g["path"] == "Makefile")
        assert "cortex-m0plus" in mk
        assert "-mfloat-abi=soft" in mk
        assert "STM32G0B1xx" in mk
        assert "FLASH.ld" in mk
        assert "SDK_ROOT" in mk
        assert "%_template.c" in mk


def test_apply_writes_files() -> None:
    if not HAS_SDK:
        print("skip(无 SDK)"); return
    from embedded_ai_coder.core.coder import CoderImpl
    from embedded_ai_coder.core.scaffolder import Scaffolder
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "fw"
        root.mkdir()
        sc = Scaffolder(SDK, root)
        out = sc.apply(sc.plan("STM32G0B1"), CoderImpl(root))
        assert out["written"] >= 9
        for p in ["Makefile", "Core/Src/main.c", "Core/Inc/main.h",
                  "Core/Startup/startup_stm32g0b1xx.s"]:
            assert (root / p).exists(), p
        assert list(root.glob("*FLASH.ld"))


def test_apply_makes_elf_if_toolchain() -> None:
    if not HAS_SDK:
        print("skip(无 SDK)"); return
    if not (shutil.which("arm-none-eabi-gcc") and shutil.which("make")):
        print("skip(无 arm-gcc/make)"); return
    from embedded_ai_coder.core.coder import CoderImpl
    from embedded_ai_coder.core.scaffolder import Scaffolder
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "fw"
        root.mkdir()
        sc = Scaffolder(SDK, root)
        sc.apply(sc.plan("STM32G0B1"), CoderImpl(root))
        r = subprocess.run(["make"], cwd=str(root), capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-1500:]
        assert (root / "build" / "fw.elf").exists()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for name in [n for n in globals() if n.startswith("test_")]:
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
