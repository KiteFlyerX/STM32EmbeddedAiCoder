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
    CaptionLabel,
    ElevatedCardWidget,
    InfoBar,
    LineEdit,
    PlainTextEdit,
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

        cl.addWidget(BodyLabel("官方 SDK 包根(一键生成 scaffold 用):", cfgCard))
        sdkRow = QHBoxLayout()
        self.sdkEdit = LineEdit(cfgCard)
        self.sdkEdit.setText(proj.get("sdk_path", "") or "")
        self.sdkEdit.setPlaceholderText("如 E:/AiTool/STM32_SDK/STM32Cube_FW_G0_V1.6.0")
        self.btnBrowseSdk = PushButton("浏览…", cfgCard)
        sdkRow.addWidget(self.sdkEdit, 1)
        sdkRow.addWidget(self.btnBrowseSdk)
        cl.addLayout(sdkRow)

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

        layout.addWidget(self._build_docs_card(cfg))

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
        self.btnBrowseSdk.clicked.connect(self._on_browse_sdk)
        self.btnSave.clicked.connect(self._on_save)
        self.btnRefresh.clicked.connect(self._refresh_tree)
        # 初次填充源码树
        self._refresh_tree()

    # ---------- 项目文档卡片(F-24 预读)----------
    def _build_docs_card(self, cfg) -> ElevatedCardWidget:
        """原理图 / 需求文档:选择 → 预读预览 → 保存到 local.yaml。"""
        docs_cfg = cfg.get("docs", {}) or {}
        self._sch_paths: list[str] = list(docs_cfg.get("schematics") or [])
        self._req_paths: list[str] = list(docs_cfg.get("requirements") or [])

        card = ElevatedCardWidget(self)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 16)
        cl.setSpacing(8)
        cl.addWidget(SubtitleLabel("项目文档(预读注入 AI 上下文)", card))
        cl.addWidget(CaptionLabel(
            "原理图走多模态视觉(图片 png/jpg 直传;pdf 需可选 pymupdf);"
            "需求文档纯标准库抽取(md/txt/docx;pdf 仅记元信息)。", card))

        # 原理图
        cl.addWidget(BodyLabel("原理图(可多选,图片/PDF):", card))
        schRow = QHBoxLayout()
        self.schEdit = LineEdit(card)
        self.schEdit.setReadOnly(True)
        self.schEdit.setPlaceholderText("点击「添加…」选择原理图文件")
        self._refresh_path_edit(self.schEdit, self._sch_paths)
        self.btnAddSch = PushButton("添加…", card)
        self.btnClrSch = PushButton("清空", card)
        schRow.addWidget(self.schEdit, 1)
        schRow.addWidget(self.btnAddSch)
        schRow.addWidget(self.btnClrSch)
        cl.addLayout(schRow)

        # 需求文档
        cl.addWidget(BodyLabel("需求文档(可多选,md/txt/docx/pdf):", card))
        reqRow = QHBoxLayout()
        self.reqEdit = LineEdit(card)
        self.reqEdit.setReadOnly(True)
        self.reqEdit.setPlaceholderText("点击「添加…」选择需求文档")
        self._refresh_path_edit(self.reqEdit, self._req_paths)
        self.btnAddReq = PushButton("添加…", card)
        self.btnClrReq = PushButton("清空", card)
        reqRow.addWidget(self.reqEdit, 1)
        reqRow.addWidget(self.btnAddReq)
        reqRow.addWidget(self.btnClrReq)
        cl.addLayout(reqRow)

        # 引脚表(F-25 一键生成主路径 + 视觉兜底)
        cl.addWidget(BodyLabel("引脚表(结构化 .md,一键生成用):", card))
        pinRow = QHBoxLayout()
        self.pinmapEdit = LineEdit(card)
        self.pinmapEdit.setReadOnly(True)
        self.pinmapEdit.setPlaceholderText("点击「选择…」选引脚表文件(主路径,优先于视觉)")
        self._pinmap_path = docs_cfg.get("pinmap", "") or ""
        if self._pinmap_path:
            self.pinmapEdit.setText(self._pinmap_path.replace("\\", "/"))
        self.btnPinmap = PushButton("选择…", card)
        self.btnClrPinmap = PushButton("清空", card)
        pinRow.addWidget(self.pinmapEdit, 1)
        pinRow.addWidget(self.btnPinmap)
        pinRow.addWidget(self.btnClrPinmap)
        cl.addLayout(pinRow)

        # 预读 + 预览
        opRow = QHBoxLayout()
        self.btnPreread = PushButton("📖 预读文档", card)
        opRow.addWidget(self.btnPreread)
        opRow.addStretch(1)
        cl.addLayout(opRow)
        self.previewEdit = PlainTextEdit(card)
        self.previewEdit.setReadOnly(True)
        self.previewEdit.setPlaceholderText("「预读文档」后在此查看抽取结果摘要。")
        self.previewEdit.setFixedHeight(150)
        cl.addWidget(self.previewEdit)

        # 绑定
        self.btnAddSch.clicked.connect(lambda: self._add_files("schematic"))
        self.btnClrSch.clicked.connect(lambda: self._clear_files("schematic"))
        self.btnAddReq.clicked.connect(lambda: self._add_files("requirement"))
        self.btnClrReq.clicked.connect(lambda: self._clear_files("requirement"))
        self.btnPinmap.clicked.connect(self._on_pick_pinmap)
        self.btnClrPinmap.clicked.connect(self._on_clear_pinmap)
        self.btnPreread.clicked.connect(self._on_preread)
        return card

    @staticmethod
    def _refresh_path_edit(edit: LineEdit, paths: list[str]) -> None:
        edit.setText(", ".join(p.replace("\\", "/") for p in paths))

    def _add_files(self, kind: str) -> None:
        if kind == "schematic":
            flt = "原理图 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.pdf);;所有文件 (*)"
            paths, _ = QFileDialog.getOpenFileNames(self, "选择原理图", "", flt)
            target = self._sch_paths
        else:
            flt = ("需求文档 (*.md *.txt *.rst *.docx *.pdf *.c *.h *.json *.yaml *.csv);;"
                   "所有文件 (*)")
            paths, _ = QFileDialog.getOpenFileNames(self, "选择需求文档", "", flt)
            target = self._req_paths
        for p in paths:
            p = p.replace("\\", "/")
            if p and p not in target:
                target.append(p)
        edit = self.schEdit if kind == "schematic" else self.reqEdit
        self._refresh_path_edit(edit, target)

    def _clear_files(self, kind: str) -> None:
        if kind == "schematic":
            self._sch_paths.clear()
            self._refresh_path_edit(self.schEdit, self._sch_paths)
        else:
            self._req_paths.clear()
            self._refresh_path_edit(self.reqEdit, self._req_paths)

    def _on_preread(self) -> None:
        """在 GUI 线程直接预读(本地文件,快),展示抽取摘要。"""
        from ...core.docs_context import DocsContextReader, make_docs_config

        base = dict(self.hub.config.get("docs", {}) or {})
        base["schematics"] = list(self._sch_paths)
        base["requirements"] = list(self._req_paths)
        reader = DocsContextReader(make_docs_config({"docs": base}))
        try:
            items, echo = reader.read_all()
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("预读失败", str(exc), parent=self.window(), duration=4000)
            return
        n_img = len(reader.render_images(items))
        n_text = sum(1 for it in items if it.role == "text")
        preview_lines = [
            f"预读完成:{len(items)} 份(文本 {n_text} / 图片 {n_img})。",
            "",
        ]
        for it in items:
            name = Path(it.path).name
            tag = {"text": "文本", "image": "图片", "meta_only": "元信息"}[it.role]
            line = f"[{it.kind}] {name}  →  {tag}"
            if it.text:
                line += f"  ({len(it.text)}字符)"
            if it.note:
                line += f"  备注:{it.note}"
            preview_lines.append(line)
            # 文本类附前几行预览
            if it.role == "text" and it.text:
                snippet = it.text.strip().splitlines()[:3]
                for s in snippet:
                    preview_lines.append("    " + s[:120])
        self.previewEdit.setPlainText("\n".join(preview_lines))
        InfoBar.success("预读完成",
                        f"{len(items)} 份文档(图片 {n_img});保存后将在闭环中注入 AI 上下文。",
                        parent=self.window(), duration=3500)

    # ---------- 动作 ----------
    def _on_browse(self) -> None:
        start = self.rootEdit.text().strip() or ""
        path = QFileDialog.getExistingDirectory(self, "选择 STM32 工程根目录", start)
        if path:
            self.rootEdit.setText(path.replace("\\", "/"))
            self._refresh_tree()

    def _on_browse_sdk(self) -> None:
        start = self.sdkEdit.text().strip() or ""
        path = QFileDialog.getExistingDirectory(self, "选择官方 SDK 包根目录", start)
        if path:
            self.sdkEdit.setText(path.replace("\\", "/"))

    def _on_pick_pinmap(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择引脚表", "", "引脚表 (*.md *.txt);;所有文件 (*)")
        if path:
            self._pinmap_path = path.replace("\\", "/")
            self.pinmapEdit.setText(self._pinmap_path)

    def _on_clear_pinmap(self) -> None:
        self._pinmap_path = ""
        self.pinmapEdit.setText("")

    def _on_save(self) -> None:
        root = self.rootEdit.text().strip()
        chip = self.chipEdit.text().strip()
        build_cmd = self.buildEdit.text().strip()
        sdk_path = self.sdkEdit.text().strip()
        pinmap = (self._pinmap_path or "").strip()
        overlay = {
            "project": {"root": root, "chip": chip,
                        "build_command": build_cmd, "sdk_path": sdk_path},
            "ai": {"tokenbase_dir": root} if root else {},
            "docs": {
                "schematics": list(self._sch_paths),
                "requirements": list(self._req_paths),
                "pinmap": pinmap,
            },
        }
        try:
            save_local_overlay(overlay)
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("保存失败", str(exc), parent=self.window(), duration=4000)
            return
        # 同步到内存 config(深合并)
        merged_proj = dict(self.hub.config.get("project", {}) or {})
        merged_proj.update({"root": root, "chip": chip,
                            "build_command": build_cmd, "sdk_path": sdk_path})
        self.hub.config["project"] = merged_proj
        if root:
            self.hub.config.setdefault("ai", {})["tokenbase_dir"] = root
        merged_docs = dict(self.hub.config.get("docs", {}) or {})
        merged_docs["schematics"] = list(self._sch_paths)
        merged_docs["requirements"] = list(self._req_paths)
        merged_docs["pinmap"] = pinmap
        self.hub.config["docs"] = merged_docs
        InfoBar.success("已保存", "工程配置与项目文档已写入 local.yaml。",
                        parent=self.window(), duration=3000)
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
