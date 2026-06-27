"""⑧ 历史:每轮迭代结果表格 + 导出 JSON 回放。

接 hub.iterationDone:累积每轮(是否 fault / 补丁 / 应用 / 构建 / 烧录 / verified / 备注)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import json

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    ElevatedCardWidget,
    InfoBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from .base import PlaceholderPage


def _tri(v) -> str:
    if v is True:
        return "✓"
    if v is False:
        return "✗"
    return "—"


class HistoryPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        self._rows: list[dict] = []
        super().__init__(
            "历史",
            "回溯每一次迭代:是否命中 fault、补丁数、构建/烧录/验证结果,支持导出 JSON 复现。",
            parent,
        )

    def _build_content(self, layout) -> None:
        bar = QHBoxLayout()
        self.btnExport = PushButton("导出 JSON…", self)
        self.btnClear = PushButton("清空", self)
        bar.addWidget(self.btnExport)
        bar.addWidget(self.btnClear)
        bar.addStretch(1)
        layout.addLayout(bar)

        card = ElevatedCardWidget(self)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.addWidget(SubtitleLabel("迭代历史", card))
        self.table = QTableWidget(0, 8, card)
        self.table.setHorizontalHeaderLabels(
            ["轮次", "fault", "补丁", "应用", "构建", "烧录", "验证", "备注"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        cl.addWidget(self.table, 1)
        layout.addWidget(card, 1)

        self.placeholder = StrongBodyLabel("", self)  # 占位,无数据时提示
        layout.addWidget(BodyLabel("启动闭环后,每轮结果会自动累积于此。", self))

        self.hub.iterationDone.connect(self._on_iteration)
        self.btnExport.clicked.connect(self._on_export)
        self.btnClear.clicked.connect(self._on_clear)

    # ---------- 回调 ----------
    def _on_iteration(self, payload: dict) -> None:
        self._rows.append(payload)
        r = self.table.rowCount()
        self.table.insertRow(r)
        vals = [
            str(payload.get("iteration", "")),
            "有" if payload.get("fault_fragment") else "无",
            str(len(payload.get("patches", []) or [])),
            f"{payload.get('patches_applied', 0)}/{payload.get('patches_failed', 0)}",
            _tri(payload.get("build_ok")),
            _tri(payload.get("flashed")),
            _tri(payload.get("verified")),
            payload.get("note", "") or "",
        ]
        for c, v in enumerate(vals):
            self.table.setItem(r, c, QTableWidgetItem(v))

    def _on_export(self) -> None:
        if not self._rows:
            InfoBar.warning("无数据", "尚无迭代记录可导出。", parent=self.window(), duration=3000)
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出迭代历史", "embedded_aicoder_history.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._rows, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("导出失败", str(exc), parent=self.window(), duration=4000)
            return
        InfoBar.success("已导出", path, parent=self.window(), duration=3000)

    def _on_clear(self) -> None:
        self._rows.clear()
        self.table.setRowCount(0)
