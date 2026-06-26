"""
日志过滤:LogFilter(M1)。

关键字 / 正则过滤 error / fault / panic / assert / hardfault 等,
输出"值得送 AI"的日志片段(命中行 + 上下文窗口)。
对应需求 F-02 / 核心模块「过滤与上下文」。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 默认关键字集:覆盖 Cortex-M 常见 fault 文本(小写匹配)
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "error",
    "fault",
    "hardfault",
    "hard fault",
    "panic",
    "assert",
    "cfsr",
    "hfsr",
    "mmfar",
    "bfar",
    "preciserr",
    "impreciserr",
    "stack overflow",
    "undefined instruction",
)

# 默认正则:函数名 / 文件:行 / 地址(stack 帧、PC)
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"0x[0-9A-Fa-f]{6,}",            # 地址
    r"[A-Za-z_]\w*\s*\+\s*\d+",      # symbol + offset
    r"[\w/]+\.(c|h):\d+",            # file:line
)


@dataclass
class FilterConfig:
    """过滤配置。"""
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    patterns: list[str] = field(default_factory=lambda: list(DEFAULT_PATTERNS))
    context_lines: int = 2   # 命中行前后各保留的上下文行数
    case_insensitive: bool = True


class LogFilter:
    """日志过滤器:从原始行流里筛出 fault 片段。"""

    def __init__(self, config: FilterConfig | None = None):
        self.config = config or FilterConfig()
        flags = re.IGNORECASE if self.config.case_insensitive else 0
        self._kw_re = re.compile(
            "|".join(re.escape(k) for k in self.config.keywords), flags
        )
        self._pat_re = re.compile(
            "|".join(self.config.patterns), flags
        )
        self._compiled_patterns = [re.compile(p, flags) for p in self.config.patterns]

    # ---------- 单行判定 ----------
    def is_interesting(self, line: str) -> bool:
        """该行是否命中关键字 / 正则。"""
        if self._kw_re.search(line):
            return True
        return any(p.search(line) for p in self._compiled_patterns)

    # ---------- 片段提取 ----------
    def extract(self, lines: list[str]) -> str:
        """从一批行里提取「值得送 AI」的文本片段(带上下文窗口)。

        多个命中若窗口重叠则合并,避免重复。
        """
        if not lines:
            return ""
        n = len(lines)
        hit_idx = [i for i, ln in enumerate(lines) if self.is_interesting(ln)]
        if not hit_idx:
            return ""

        ctx = self.config.context_lines
        # 合并相邻/重叠窗口
        ranges: list[tuple[int, int]] = []
        for i in hit_idx:
            lo = max(0, i - ctx)
            hi = min(n - 1, i + ctx)
            if ranges and lo <= ranges[-1][1] + 1:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], hi))
            else:
                ranges.append((lo, hi))

        out: list[str] = []
        for lo, hi in ranges:
            for i in range(lo, hi + 1):
                marker = ">>" if i in hit_idx else "  "
                out.append(f"{marker} {lines[i]}")
        return "\n".join(out)

    # ---------- 提取符号名(喂 tokenbase query)----------
    def extract_symbols(self, fragment: str) -> list[str]:
        """从 fault 片段里提取候选符号名,供 tokenbase query。

        规则:``symbol + offset`` 形态里的 symbol(如 ``sensor_samples_read + 48``);
        再用启发式过滤(含字母、非纯 hex 地址、非关键字)。
        """
        syms: list[str] = []
        # symbol + offset
        for m in re.finditer(r"([A-Za-z_]\w*)\s*\+\s*\d+", fragment):
            name = m.group(1)
            if name.lower() not in self.config.keywords:
                syms.append(name)
        # 去重保序
        seen: set[str] = set()
        uniq = []
        for s in syms:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq


def make_filter(config: dict) -> LogFilter:
    """根据 config 构造过滤器(M1 暂未在 yaml 暴露过滤项,用默认)。"""
    filt_cfg = config.get("filter", {}) or {}
    fc = FilterConfig(
        keywords=filt_cfg.get("keywords", list(DEFAULT_KEYWORDS)),
        patterns=filt_cfg.get("patterns", list(DEFAULT_PATTERNS)),
        context_lines=int(filt_cfg.get("context_lines", 2)),
    )
    return LogFilter(fc)
