"""④ AI 编码:AI 补丁 diff 预览 + 应用 / 回滚 + 诊断。

接 hub.aiPatches(patches, diagnosis, mock):列出每个补丁,选中看 diff;
「应用选中」→ hub.apply_patch;「全部回滚」→ hub.rollback。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ElevatedCardWidget,
    InfoBar,
    LineEdit,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from ..widgets import DiffView
from .base import PlaceholderPage


class AiCoderPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        self._patches: list[dict] = []
        super().__init__(
            "AI 编码",
            "查看 AI 诊断与生成的 C 代码修改,逐项 diff 预览后应用或回滚。",
            parent,
        )

    def _build_content(self, layout) -> None:
        # 诊断
        self.diagLabel = StrongBodyLabel("诊断:尚未运行闭环。", self)
        self.diagLabel.setWordWrap(True)
        self.diagLabel.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.diagLabel)

        # AI 实现(读原理图+需求文档 → 生成固件)
        implCard = ElevatedCardWidget(self)
        il = QVBoxLayout(implCard)
        il.setContentsMargins(12, 10, 12, 12)
        il.setSpacing(6)
        il.addWidget(SubtitleLabel("🤖 用 AI 实现(读原理图 + 需求文档)", implCard))
        il.addWidget(CaptionLabel(
            "据「项目文档」预读的原理图/需求,调用配置的 AI 生成产品固件文件并写回工程。"
            "生成较慢(推理模型尤甚),在后台线程执行,完成会提示。", implCard))
        implRow = QHBoxLayout()
        self.goalEdit = LineEdit(implCard)
        self.goalEdit.setPlaceholderText("实现目标,如:实现 SHT40 温湿度采集 + USART3 调试日志")
        implRow.addWidget(self.goalEdit, 1)
        self.btnImplement = PrimaryPushButton("🤖 实现", implCard)
        self.btnDeploy = PushButton("🚀 实现并部署", implCard)
        self.btnGenProject = PrimaryPushButton("🏗️ 一键生成整项目", implCard)
        implRow.addWidget(self.btnImplement)
        implRow.addWidget(self.btnDeploy)
        implRow.addWidget(self.btnGenProject)
        il.addLayout(implRow)
        self.implLabel = BodyLabel("尚未实现。", implCard)
        self.implLabel.setWordWrap(True)
        il.addWidget(self.implLabel)
        layout.addWidget(implCard)

        split = QHBoxLayout()

        # 左:补丁列表
        leftCard = ElevatedCardWidget(self)
        ll = QVBoxLayout(leftCard)
        ll.setContentsMargins(12, 10, 12, 12)
        ll.addWidget(SubtitleLabel("补丁列表", leftCard))
        self.listWidget = ListWidget(leftCard)
        ll.addWidget(self.listWidget, 1)
        split.addWidget(leftCard, 2)

        # 右:diff 预览
        rightCard = ElevatedCardWidget(self)
        rl = QVBoxLayout(rightCard)
        rl.setContentsMargins(12, 10, 12, 12)
        rl.addWidget(SubtitleLabel("代码 diff(新增绿 / 删除红)", rightCard))
        self.diffView = DiffView(rightCard)
        self.diffView.show_message("选中左侧补丁查看差异。")
        rl.addWidget(self.diffView, 1)
        split.addWidget(rightCard, 3)

        layout.addLayout(split, 1)

        # 操作
        bar = QHBoxLayout()
        self.btnApply = PrimaryPushButton("✓ 应用选中补丁", self)
        self.btnRollback = PushButton("↺ 全部回滚(撤销已应用)", self)
        bar.addWidget(self.btnApply)
        bar.addWidget(self.btnRollback)
        bar.addStretch(1)
        layout.addLayout(bar)

        # 绑定
        self.hub.aiPatches.connect(self._on_patches)
        self.hub.applyResult.connect(self._on_apply_result)
        self.hub.rollbackResult.connect(self._on_rollback_result)
        self.hub.implementResult.connect(self._on_implement_result)
        self.listWidget.currentRowChanged.connect(self._on_select)
        self.btnApply.clicked.connect(self._on_apply)
        self.btnRollback.clicked.connect(self._on_rollback)
        self.btnImplement.clicked.connect(self._on_implement)
        self.btnDeploy.clicked.connect(self._on_deploy)
        self.btnGenProject.clicked.connect(self._on_gen_project)
        self.hub.progress.connect(self._on_progress)

    # ---------- 回调 ----------
    def _on_patches(self, patches: list, diagnosis: str, mock: bool) -> None:
        tag = '<span style="color:#C77F00">[mock]</span> ' if mock else ""
        self.diagLabel.setText("诊断:" + tag + (diagnosis or "—"))
        start = len(self._patches)
        self._patches.extend(patches or [])
        for i, p in enumerate(patches or [], start=start):
            label = f"#{i}  {p.get('file','?')}  ·  {p.get('anchor','?')}"
            self.listWidget.addItem(label)
        if self.listWidget.count() and self.listWidget.currentRow() < 0:
            self.listWidget.setCurrentRow(0)

    def _on_select(self, row: int) -> None:
        if 0 <= row < len(self._patches):
            self.diffView.show_patch(self._patches[row])

    def _on_apply(self) -> None:
        row = self.listWidget.currentRow()
        if not (0 <= row < len(self._patches)):
            InfoBar.warning("未选择", "请先在左侧选择一个补丁。", parent=self.window(), duration=3000)
            return
        self.hub.apply_patch(self._patches[row])

    def _on_apply_result(self, payload: dict) -> None:
        if payload.get("ok"):
            InfoBar.success("已应用", f"补丁已写回 {payload.get('file','?')}(已备份 .bak,可回滚)。",
                            parent=self.window(), duration=4000)
        else:
            InfoBar.warning("未命中", f"补丁未能应用(可能已应用或 old 不匹配):{payload.get('file','?')}",
                            parent=self.window(), duration=4000)

    def _on_rollback(self) -> None:
        self.hub.rollback()

    def _on_rollback_result(self, n: int) -> None:
        InfoBar.success("已回滚", f"已还原 {n} 处修改。", parent=self.window(), duration=4000)

    # ---------- AI 实现(读原理图+需求 → 生成固件)----------
    def _on_implement(self) -> None:
        goal = self.goalEdit.text().strip()
        self.implLabel.setText("⟳ 正在调用 AI 实现(读取原理图/需求文档,生成固件)… 可能较慢,请稍候。")
        self.btnImplement.setEnabled(False)
        self.hub.implement_now(goal)
        InfoBar.information("已提交", "AI 实现任务已在后台执行,完成后会在此提示。",
                            parent=self.window(), duration=3000)

    def _on_deploy(self) -> None:
        goal = self.goalEdit.text().strip()
        self.implLabel.setText("⟳ 正在生成工程代码 → 自动编译(自愈)→ 烧录… 全程无人,请稍候。")
        self.btnDeploy.setEnabled(False)
        self.hub.implement_and_deploy_now(goal)
        InfoBar.information("已提交", "生成+部署流水线已在后台执行(生成→编译→烧录)。",
                            parent=self.window(), duration=3000)

    def _on_gen_project(self) -> None:
        goal = self.goalEdit.text().strip()
        self.implLabel.setText("⟳ 一键生成整项目(架构→scaffold→模块→集成→编译自愈)… 全程后台,请稍候。")
        self.btnGenProject.setEnabled(False)
        self.hub.generate_project_now(goal)
        InfoBar.information("已提交", "一键生成整项目已在后台执行(五阶段),进度见此处。",
                            parent=self.window(), duration=3000)

    _PROJ_STAGE_LABEL = {
        "proj_design": "① 架构设计",
        "proj_scaffold": "② 生成工程骨架",
        "proj_integrate": "④ 主循环+状态机集成",
        "proj_build_heal": "⑤ 编译自愈",
    }

    def _on_progress(self, stage: str, payload: dict) -> None:
        """识别 proj_* 阶段进度,更新提示文案。"""
        if not stage or not stage.startswith("proj_"):
            return
        if stage.startswith("proj_module_"):
            name = stage.split("proj_module_", 1)[1]
            self.implLabel.setText(f"⟳ ③ 生成模块:{name} …")
            return
        label = self._PROJ_STAGE_LABEL.get(stage, stage)
        status = payload.get("status", "")
        if stage == "proj_build_heal" and status == "done":
            ok = payload.get("build_ok")
            heal = payload.get("heal_attempts", 0)
            self.implLabel.setText(
                f"⟳ {label}:{'✓编译通过' if ok else '✗编译失败(自愈 ' + str(heal) + ' 轮)'}")
        elif status:
            self.implLabel.setText(f"⟳ {label}:{status}")

    def _on_implement_result(self, payload: dict) -> None:
        self.btnImplement.setEnabled(True)
        self.btnDeploy.setEnabled(True)
        self.btnGenProject.setEnabled(True)
        err = payload.get("error")
        if err:
            self.implLabel.setText(f"❌ 实现失败:{err}")
            InfoBar.error("实现失败", str(err), parent=self.window(), duration=5000)
            return
        mock = payload.get("mock", False)
        files = payload.get("files", []) or []
        written = payload.get("written", 0)
        skipped = payload.get("skipped", 0)
        summary = payload.get("summary", "") or "(无摘要)"
        tb_hits = payload.get("tb_hits", 0)
        tag = " [mock]" if mock else ""
        tb_note = f"<br>　🔁 已复用工程 {tb_hits} 个已有签名(TokenBase)" if tb_hits else ""
        file_lines = "".join(f"<br>　· {f.get('path')} ({f.get('chars', 0)} 字符) {f.get('reason', '')}"
                             for f in files)
        # 部署模式:追加编译/烧录状态
        deploy_line = ""
        if payload.get("deploy"):
            build_ok = payload.get("build_ok")
            flashed = payload.get("flashed")
            bs = "✓编译通过" if build_ok else "✗编译失败"
            fs = {True: "✓已烧录", False: "✗烧录失败", None: "—未烧录"}.get(flashed, "—")
            deploy_line = f"<br><b>部署:{bs}　{fs}</b>　{payload.get('note', '')}"
        self.implLabel.setText(
            f"{tag}摘要:{summary}{tb_note}<br>"
            f"<b>写入 {written} 个文件(跳过 {skipped})</b>{file_lines or '<br>　(无文件)'}"
            f"{deploy_line}"
        )
        msg = (f"已生成 {len(files)} 个文件,写入 {written} 个。" if files
               else "AI 未产出文件。")
        if mock:
            msg = "[mock] " + msg + " 配好 ai.api_key 后为真实生成。"
        InfoBar.success("AI 实现完成", msg, parent=self.window(), duration=5000)
