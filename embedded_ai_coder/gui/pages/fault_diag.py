"""③ 故障诊断:Cortex-M HardFault 解码 + 栈回溯表 + 源码定位。

接 hub.iterationDone:自动取最新 fault_fragment;「解码」调 FaultDecoderImpl,
解析 CFSR/HFSR/MMFAR/BFAR 位义 + 栈帧(file:line)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from qfluentwidgets import (
    BodyLabel,
    ElevatedCardWidget,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from ...core.fault_decoder import FaultDecoderImpl
from .base import PlaceholderPage


class FaultDiagPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        self._latest_fragment = ""
        self._decoder = FaultDecoderImpl()
        super().__init__(
            "故障诊断",
            "解码 HardFault:fault 寄存器(CFSR/HFSR/MMFAR/BFAR)逐位译义 + 栈回溯(file:line),辅助定位根因。",
            parent,
        )

    def _build_content(self, layout) -> None:
        # 操作栏
        bar = QHBoxLayout()
        self.btnDecode = PrimaryPushButton("🔍 解码", self)
        self.btnLatest = PushButton("从最新日志取 fault", self)
        bar.addWidget(self.btnDecode)
        bar.addWidget(self.btnLatest)
        bar.addStretch(1)
        layout.addLayout(bar)

        # 根因
        self.reasonLabel = StrongBodyLabel("根因:尚未解码", self)
        self.reasonLabel.setWordWrap(True)
        layout.addWidget(self.reasonLabel)

        # 左:fault 文本(可粘贴编辑);右:寄存器 + 栈帧表
        split = QHBoxLayout()

        leftCard = ElevatedCardWidget(self)
        ll = QVBoxLayout(leftCard)
        ll.setContentsMargins(12, 10, 12, 12)
        ll.addWidget(SubtitleLabel("fault 现场(可粘贴编辑)", leftCard))
        from PySide6.QtWidgets import QPlainTextEdit
        self.dumpEdit = QPlainTextEdit(leftCard)
        self.dumpEdit.setPlaceholderText(
            "粘贴含 CFSR=0x... / HFSR=0x... 与 #0 0xADDR symbol + off (file:line) 的 fault 文本…"
        )
        font = self.dumpEdit.font()
        font.setFamily("Consolas, Courier New, monospace")
        font.setPointSize(9)
        self.dumpEdit.setFont(font)
        ll.addWidget(self.dumpEdit)
        split.addWidget(leftCard, 1)

        rightCard = ElevatedCardWidget(self)
        rl = QVBoxLayout(rightCard)
        rl.setContentsMargins(12, 10, 12, 12)
        rl.addWidget(SubtitleLabel("寄存器 / 栈回溯", rightCard))
        self.regLabel = BodyLabel("—", rightCard)
        self.regLabel.setWordWrap(True)
        self.regLabel.setTextFormat(Qt.TextFormat.RichText)
        rl.addWidget(self.regLabel)
        self.stackTable = QTableWidget(0, 5, rightCard)
        self.stackTable.setHorizontalHeaderLabels(["#", "地址", "符号", "+偏移", "文件:行"])
        self.stackTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stackTable.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rl.addWidget(self.stackTable, 1)
        split.addWidget(rightCard, 1)

        layout.addLayout(split, 1)

        # 绑定
        self.btnDecode.clicked.connect(self._on_decode)
        self.btnLatest.clicked.connect(self._on_latest)
        self.hub.iterationDone.connect(self._on_iteration)

    # ---------- 回调 ----------
    def _on_iteration(self, payload: dict) -> None:
        frag = payload.get("fault_fragment", "") or ""
        if frag:
            self._latest_fragment = frag

    def _on_latest(self) -> None:
        if self._latest_fragment:
            self.dumpEdit.setPlainText(self._latest_fragment)
            self._on_decode()
        else:
            InfoBar.warning("无 fault", "尚未采集到含 fault 的日志片段。", parent=self.window(), duration=3000)

    def _on_decode(self) -> None:
        dump = self.dumpEdit.toPlainText().strip()
        if not dump:
            InfoBar.warning("无内容", "请粘贴或取入 fault 文本后再解码。", parent=self.window(), duration=3000)
            return
        result = self._decoder.decode(dump)
        self.reasonLabel.setText("根因:" + result["reason"])

        # 寄存器
        regs = result.get("registers", {}) or {}
        if regs:
            lines = []
            for name, info in regs.items():
                flags = info.get("flags", [])
                lines.append(f"<b>{name}={info.get('hex','')}</b>")
                for f in flags:
                    lines.append(f"&nbsp;&nbsp;• {f}")
            self.regLabel.setText("<br>".join(lines))
        else:
            self.regLabel.setText("—")

        # 栈帧表
        stack = result.get("stack", []) or []
        self.stackTable.setRowCount(len(stack))
        for r, fr in enumerate(stack):
            file_line = fr.get("file", "") or ""
            if fr.get("line"):
                file_line = f"{file_line}:{fr['line']}"
            vals = [
                str(fr.get("index", "")),
                fr.get("addr", ""),
                fr.get("symbol", ""),
                f"+{fr.get('offset', 0)}",
                file_line,
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in (1, 4):
                    item.setForeground(Qt.GlobalColor.darkCyan)
                self.stackTable.setItem(r, c, item)
