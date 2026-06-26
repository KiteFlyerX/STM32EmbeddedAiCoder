"""
STM32_TokenBase 桥接:通过 subprocess 调 tokenbase 的 index/query。

解耦原则:不混 venv,直接用 STM32_TokenBase 自带解释器跑 ``python -m tokenbase``。
返回的 signature Lens + provenance 文本会被拼进 AI prompt(省 token 的实战接入)。
对应需求 F-07。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# STM32_TokenBase 仓库根与专用解释器(解耦 venv)
TOKENBASE_REPO = Path(r"E:/AiTool/STM32_TokenBase")
_TOKENBASE_PYTHON = TOKENBASE_REPO / ".venv" / "Scripts" / "python.exe"


def tokenbase_python() -> str:
    """返回跑 tokenbase 用的 python 解释器路径。

    优先用 STM32_TokenBase 自带 venv;不可用则回退到系统 python(假设已装包)。
    """
    if _TOKENBASE_PYTHON.exists():
        return str(_TOKENBASE_PYTHON)
    sys_python = shutil.which("python") or shutil.which("python3")
    return sys_python or "python"


@dataclass
class QueryResult:
    """一次 tokenbase query 的结果。"""
    symbol: str
    directory: str
    ok: bool                # 是否查询成功(返回 0)
    output: str             # tokenbase 的 stdout 文本(含 Lens + provenance + token 对比)
    command: list[str]      # 实际执行的命令(用于回显/审计)


def index(directory: str | Path, *, project: str | None = None, force: bool = False,
          timeout: float = 120.0) -> tuple[bool, str]:
    """对目录建索引。返回 (是否成功, stdout 文本)。"""
    directory = str(Path(directory).resolve())
    cmd = [tokenbase_python(), "-m", "tokenbase", "index", directory]
    if project:
        cmd += ["--project", project]
    if force:
        cmd.append("--force")
    logger.info("[tokenbase] index: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        return False, f"tokenbase 解释器不可用:{exc}"
    except subprocess.TimeoutExpired:
        return False, f"tokenbase index 超时({timeout}s)"
    ok = proc.returncode == 0
    out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    return ok, out


def query(symbol: str, directory: str | Path, *, timeout: float = 30.0) -> QueryResult:
    """查询单个符号。返回 QueryResult。"""
    directory = str(Path(directory).resolve())
    cmd = [tokenbase_python(), "-m", "tokenbase", "query", symbol, directory]
    logger.info("[tokenbase] query: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        return QueryResult(symbol, directory, False,
                           f"tokenbase 解释器不可用:{exc}", cmd)
    except subprocess.TimeoutExpired:
        return QueryResult(symbol, directory, False,
                           f"tokenbase query 超时({timeout}s)", cmd)
    out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    return QueryResult(symbol, directory, proc.returncode == 0, out, cmd)


def query_many(symbols: list[str], directory: str | Path,
               *, timeout: float = 30.0) -> list[QueryResult]:
    """批量查询。"""
    return [query(s, directory, timeout=timeout) for s in symbols if s]


def render_lens_block(results: list[QueryResult]) -> str:
    """把多个 query 结果渲染成 prompt 用的「上下文块」。

    成功的把 Lens + provenance 列出;失败/未命中的列一行备注,便于 AI 感知缺失。
    """
    if not results:
        return "(无 tokenbase 上下文)"
    blocks: list[str] = ["# 来自 STM32_TokenBase 的源码上下文(signature Lens + provenance)"]
    for r in results:
        if r.ok and r.output.strip():
            blocks.append(f"\n## 符号 `{r.symbol}`(query {r.directory})")
            blocks.append("```")
            blocks.append(r.output.strip())
            blocks.append("```")
        else:
            blocks.append(f"\n## 符号 `{r.symbol}`:(未在 tokenbase 索引中命中)")
    return "\n".join(blocks)
