"""日志配置:控制台 + 滚动日志文件(UTF-8),CLI 与 GUI 共用。

日志文件默认落在包上级 ``logs/`` 下(已 gitignore),用于排查闭环 / 自动化问题。
重复调用幂等(只首次添加 handler,后续仅调整级别)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_configured = False


def setup_logging(verbose: bool = False, log_dir: str | Path | None = None,
                  console: bool = True) -> Path:
    """配置根 logger:滚动日志文件(+ 可选控制台)。返回日志文件路径。

    - 文件:``<log_dir>/embedded_ai_coder.log``,5MB × 3 份滚动,UTF-8。
    - verbose=True 时级别 DEBUG,否则 INFO。
    - 幂等:重复调用只调整级别,不重复加 handler。
    """
    global _configured
    level = logging.DEBUG if verbose else logging.INFO
    log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path(".")
    log_path = log_dir / "embedded_ai_coder.log"

    root = logging.getLogger()
    root.setLevel(level)
    if not _configured:
        fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATEFMT)
        # 文件(滚动)
        try:
            fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024,
                                     backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(level)
            root.addHandler(fh)
        except OSError:
            pass  # 目录不可写时仅退化为控制台
        # 控制台
        if console:
            ch = logging.StreamHandler()
            ch.setFormatter(fmt)
            ch.setLevel(level)
            root.addHandler(ch)
        # Windows 控制台 UTF-8(中文不乱码)
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass
        _configured = True
    else:
        for h in root.handlers:
            h.setLevel(level)

    logging.getLogger(__name__).info("日志文件: %s", log_path)
    return log_path


def get_log_path(log_dir: str | Path | None = None) -> Path:
    """返回默认日志文件路径(不创建)。"""
    d = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    return d / "embedded_ai_coder.log"
