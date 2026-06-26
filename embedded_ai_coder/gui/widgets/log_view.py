"""
实时日志视图:LiveLogView(QPlainTextEdit)+ LogHighlighter。

- LiveLogView:高频日志行先入缓冲,QTimer(~100ms)合并 append,避免逐行刷新卡顿;
  超过上限自动裁头;支持暂停自动滚动 / 关键字过滤 / 清空。
- LogHighlighter:QSyntaxHighlighter,error/fault/assert 红、warn 橙、地址/file:line 高亮。

Dashboard 与 LogMonitor 共用本组件。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtGui import QSyntaxHighlighter
from PySide6.QtWidgets import QPlainTextEdit

# 各规则:正则 + 颜色 + 是否加粗
_RULES = [
    (re.compile(r"\b(error|fault|hardfault|hard\s*fault|panic|assert|halted|abort|cfsr|hfsr|mmfar|bfar|preciserr|impreciserr|stack\s*overflow|undefined\s*instruction)\b", re.IGNORECASE), "#C42B2B", True),
    (re.compile(r"\b(warn|warning|timeout|overflow)\b", re.IGNORECASE), "#C77F00", False),
    (re.compile(r"\b(i2c|nack|spi|uart)\s*(err|nack|fail)\b", re.IGNORECASE), "#C42B2B", False),
    (re.compile(r"\b0x[0-9A-Fa-f]{4,}\b"), "#1F6FEB", False),     # 地址
    (re.compile(r"[\w/\\]+\.[ch]:\d+"), "#1A7F37", False),        # file:line
]


class LogHighlighter(QSyntaxHighlighter):
    """日志着色器。"""

    def __init__(self, document: QTextDocument):
        super().__init__(document)
        self._formats: list[tuple[re.Pattern, QTextCharFormat]] = []
        for pat, color, bold in _RULES:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            self._formats.append((pat, fmt))

    def highlightBlock(self, text: str) -> None:
        for pat, fmt in self._formats:
            start = 0
            while True:
                m = pat.search(text, start)
                if not m:
                    break
                self.setFormat(m.start(), m.end() - m.start(), fmt)
                start = m.end()


class LiveLogView(QPlainTextEdit):
    """带缓冲/节流/裁头/过滤的实时日志控件。"""

    MAX_BLOCKS = 5000
    FLUSH_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(0)  # 自己管裁头(用 maximumBlockCount 会丢高亮上下文,故手动)
        font = self.font()
        font.setFamily("Consolas, Cascadia Code, Courier New, monospace")
        font.setPointSize(9)
        self.setFont(font)
        # 背景跟随主题;给点纸面感
        self._highlighter = LogHighlighter(self.document())

        self._buffer: list[str] = []
        self._autoscroll = True
        self._filter = ""

        self._timer = QTimer(self)
        self._timer.setInterval(self.FLUSH_MS)
        self._timer.timeout.connect(self._flush)

    # ---------- 对外 ----------
    @Slot(str)
    def append_line(self, line: str) -> None:
        """接收一行日志入缓冲(高频调用,不直接刷新)。"""
        if not self._timer.isActive():
            self._timer.start()
        self._buffer.append(line)

    def set_autoscroll(self, on: bool) -> None:
        self._autoscroll = on

    def set_filter(self, text: str) -> None:
        self._filter = text.strip()

    def clear_log(self) -> None:
        self._buffer.clear()
        self.clear()

    # ---------- 内部 ----------
    def _flush(self) -> None:
        if not self._buffer:
            self._timer.stop()
            return
        # 过滤
        buf = self._buffer
        self._buffer = []
        if self._filter:
            buf = [ln for ln in buf if self._filter.lower() in ln.lower()]
        if not buf:
            if not self._buffer:
                self._timer.stop()
            return
        chunk = "\n".join(buf)
        at_end = self.textCursor().atEnd()
        self.appendPlainText(chunk)
        # 裁头
        if self.blockCount() > self.MAX_BLOCKS:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            # 删除超出上限的整块
            excess = self.blockCount() - self.MAX_BLOCKS
            cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, excess)
            cursor.removeSelectedText()
            cursor.deleteChar()
        if self._autoscroll and at_end:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        if not self._buffer:
            self._timer.stop()
