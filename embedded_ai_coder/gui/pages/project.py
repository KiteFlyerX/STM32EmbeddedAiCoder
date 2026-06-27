"""⑥ 工程:工程路径 / 芯片 / 构建命令 + 源码树浏览。

改动可「保存到 local.yaml」(同步更新 hub.config 与 AI 的 tokenbase_dir),并刷新源码树。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QTreeWidgetItem,
    QVBoxLayout,
)

from qfluentwidgets import (
    BodyLabel,
    ElevatedCardWidget,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TreeWidget,
)

from ...config import save_local_overlay
from .base import PlaceholderPage

_SOURCE_SUFFIXES = {".c", ".h", ".cpp", ".hpp", ".s", ".ld"}
_SKIP_DIRS = {".git", ".venv", "venv", "build", "Debug", "Release", "Drivers", "Middlewares"}
_MAX_FILES = 600


class ProjectPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        super().__init__(
            "工程",
            "配置与浏览 STM32 工程:根目录、芯片型号、构建方式、源码树,供 AI 提取上下文。",
            parent,
        )

    def _build_content(self, layout) -> None:
        cfg = self.hub.config
        proj = cfg.get("project", {}) or {}

        cfgCard = ElevatedCardWidget(self)
        cl = QVBoxLayout(cfgCard)
        cl.setContentsMargins(16, 12, 16, 16)
        cl.setSpacing(8)
        cl.addWidget(SubtitleLabel("工程配置", cfgCard))

        cl.addWidget(BodyLabel("工程根目录:", cfgCard))
        row = QHBoxLayout()
        self.rootEdit = LineEdit(cfgCard)
        self.rootEdit.setText(proj.get("root", "") or "")
        self.rootEdit.setPlaceholderText("如 E:/work/my_stm32")
        self.btnBrowse = PushButton("浏览…", cfgCard)
        row.addWidget(self.rootEdit, 1)
        row.addWidget(self.btnBrowse)
        cl.addLayout(row)

        cl.addWidget(BodyLabel("芯片型号:", cfgCard))
        self.chipEdit = LineEdit(cfgCard)
        self.chipEdit.setText(proj.get("chip", "") or "")
        self.chipEdit.setPlaceholderText("如 STM32H743 / STM32F407")
        cl.addWidget(self.chipEdit)

        cl.addWidget(BodyLabel("构建命令:", cfgCard))
        self.buildEdit = LineEdit(cfgCard)
        self.buildEdit.setText(proj.get("build_command", "") or "")
        self.buildEdit.setPlaceholderText("如 make  或  cmake --build build")
        cl.addWidget(self.buildEdit)

        saveRow = QHBoxLayout()
        self.btnSave = PrimaryPushButton("💾 保存到 local.yaml", cfgCard)
        self.btnRefresh = PushButton("刷新源码树", cfgCard)
        saveRow.addWidget(self.btnSave)
        saveRow.addWidget(self.btnRefresh)
        saveRow.addStretch(1)
        cl.addLayout(saveRow)
        layout.addWidget(cfgCard)

        treeCard = ElevatedCardWidget(self)
        tl = QVBoxLayout(treeCard)
        tl.setContentsMargins(16, 12, 16, 16)
        tl.addWidget(SubtitleLabel("源码树(.c/.h/.s/.ld)", treeCard))
        self.tree = TreeWidget(treeCard)
        self.tree.setHeaderHidden(True)
        tl.addWidget(self.tree, 1)
        layout.addWidget(treeCard, 1)

        # 绑定
        self.btnBrowse.clicked.connect(self._on_browse)
        self.btnSave.clicked.connect(self._on_save)
        self.btnRefresh.clicked.connect(self._refresh_tree)
        # 初次填充源码树
        self._refresh_tree()

    # ---------- 动作 ----------
    def _on_browse(self) -> None:
        start = self.rootEdit.text().strip() or ""
        path = QFileDialog.getExistingDirectory(self, "选择 STM32 工程根目录", start)
        if path:
            self.rootEdit.setText(path.replace("\\", "/"))
            self._refresh_tree()

    def _on_save(self) -> None:
        root = self.rootEdit.text().strip()
        chip = self.chipEdit.text().strip()
        build_cmd = self.buildEdit.text().strip()
        overlay = {
            "project": {"root": root, "chip": chip, "build_command": build_cmd},
            "ai": {"tokenbase_dir": root} if root else {},
        }
        try:
            save_local_overlay(overlay)
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("保存失败", str(exc), parent=self.window(), duration=4000)
            return
        # 同步到内存 config(深合并)
        merged_proj = dict(self.hub.config.get("project", {}) or {})
        merged_proj.update({"root": root, "chip": chip, "build_command": build_cmd})
        self.hub.config["project"] = merged_proj
        if root:
            self.hub.config.setdefault("ai", {})["tokenbase_dir"] = root
        InfoBar.success("已保存", "工程配置已写入 local.yaml。", parent=self.window(), duration=3000)
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        self.tree.clear()
        root = self.rootEdit.text().strip()
        if not root:
            return
        root_path = Path(root)
        if not root_path.exists():
            return
        files: list[str] = []
        try:
            for p in root_path.rglob("*"):
                if any(part in _SKIP_DIRS for part in p.relative_to(root_path).parts[:-1]):
                    continue
                if p.suffix.lower() in _SOURCE_SUFFIXES:
                    files.append(str(p.relative_to(root_path)).replace("\\", "/"))
                if len(files) >= _MAX_FILES:
                    break
        except Exception:  # noqa: BLE001
            return
        files.sort()
        # 按目录构建树
        for rel in files:
            parts = rel.split("/")
            parent = self.tree.invisibleRootItem()
            for i, part in enumerate(parts):
                item = self._find_or_create(parent, part, is_file=(i == len(parts) - 1))
                parent = item

    @staticmethod
    def _find_or_create(parent, text: str, is_file: bool) -> QTreeWidgetItem:
        marker = "file" if is_file else "dir"
        for i in range(parent.childCount()):
            ch = parent.child(i)
            if ch.text(0) == text and ch.data(0, Qt.ItemDataRole.UserRole) == marker:
                return ch
        item = QTreeWidgetItem(parent)
        item.setText(0, text)
        item.setData(0, Qt.ItemDataRole.UserRole, marker)
        return item
