"""
代码 diff 预览:把 AI 补丁 {file, anchor, old, new} 渲染成着色的 unified diff。

AI 编码页用它展示「新增绿 / 删除红 / hunk 头青」的只读 diff。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import difflib
from html import escape

from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QTextEdit


def patch_to_html(patch: dict) -> str:
    """单个补丁 → 着色 HTML(unified diff)。"""
    old = (patch.get("old") or "").splitlines()
    new = (patch.get("new") or "").splitlines()
    file_ = escape(patch.get("file") or "(未指定文件)")
    anchor = escape(patch.get("anchor") or "")
    reason = escape(patch.get("reason") or "")

    diff = difflib.unified_diff(old, new, lineterm="", n=2)
    rows: list[str] = []
    for line in diff:
        ln = escape(line)
        if line.startswith("+++") or line.startswith("---"):
            cls = "h"
        elif line.startswith("@@"):
            cls = "m"
        elif line.startswith("+"):
            cls = "a"
        elif line.startswith("-"):
            cls = "d"
        else:
            cls = "c"
        rows.append(f'<span class="{cls}">{ln}</span>')

    body = "\n".join(rows) if rows else "<i>(old/new 相同,无差异)</i>"
    head = f"<b>{file_}</b>" + (f" &nbsp;· 函数 <code>{anchor}</code>" if anchor else "")
    if reason:
        head += f"<br><span class='r'>理由:{reason}</span>"
    return (
        "<style>"
        ".a{color:#1A7F37} .d{color:#C42B2B} .m{color:#1F6FEB}"
        ".h{color:#888} .c{color:inherit} .r{color:#666}"
        "</style>"
        f"<pre style='font-family:Consolas,Cascadia Code,monospace;font-size:9pt;"
        f"white-space:pre-wrap;margin:0'>{head}\n{body}</pre>"
    )


class DiffView(QTextEdit):
    """只读 diff 预览控件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)

    def show_patch(self, patch: dict) -> None:
        self.setHtml(patch_to_html(patch))

    def show_message(self, text: str) -> None:
        self.setHtml(f"<i>{text}</i>")
