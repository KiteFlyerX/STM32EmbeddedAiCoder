"""⑤ 构建烧录:立即构建 / 烧录,输出控制台,显示固件与后端。

接 hub.build_now / flash_now:复用闭环同一套 builder / flasher(经 worker 线程)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    ElevatedCardWidget,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from .base import PlaceholderPage


class BuildFlashPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        super().__init__(
            "构建烧录",
            "调用 arm-none-eabi-gcc 构建固件,经 pyOCD 等后端烧录并复位(编译错误可回喂 AI 自愈)。",
            parent,
        )

    def _build_content(self, layout) -> None:
        cfg = self.hub.config
        fw = (cfg.get("flasher", {}) or {}).get("firmware", "") or "(未配置)"
        backend = (cfg.get("flasher", {}) or {}).get("backend", "pyocd")
        probe = (cfg.get("debugger", {}) or {}).get("probe", "stlink")
        build_cmd = (cfg.get("project", {}) or {}).get("build_command", "") or "(未配置)"

        info = QHBoxLayout()
        info.addWidget(BodyLabel(f"构建命令:<code>{build_cmd}</code>", self))
        info.addSpacing(16)
        info.addWidget(BodyLabel(f"烧录后端:<b>{backend}</b> · 探针 <b>{probe}</b>", self))
        info.addSpacing(16)
        info.addWidget(BodyLabel(f"固件:<code>{fw}</code>", self))
        info.addStretch(1)
        layout.addLayout(info)

        bar = QHBoxLayout()
        self.btnBuild = PrimaryPushButton("🔨 立即构建", self)
        self.btnFlash = PushButton("⚡ 立即烧录", self)
        self.btnClear = PushButton("清空输出", self)
        bar.addWidget(self.btnBuild)
        bar.addWidget(self.btnFlash)
        bar.addWidget(self.btnClear)
        bar.addStretch(1)
        layout.addLayout(bar)

        outCard = ElevatedCardWidget(self)
        ol = QVBoxLayout(outCard)
        ol.setContentsMargins(12, 10, 12, 12)
        ol.addWidget(SubtitleLabel("构建 / 烧录输出", outCard))
        self.output = QPlainTextEdit(outCard)
        self.output.setReadOnly(True)
        font = self.output.font()
        font.setFamily("Consolas, Courier New, monospace")
        font.setPointSize(9)
        self.output.setFont(font)
        ol.addWidget(self.output, 1)
        layout.addWidget(outCard, 1)

        # 绑定
        self.btnBuild.clicked.connect(self.hub.build_now)
        self.btnFlash.clicked.connect(self.hub.flash_now)
        self.btnClear.clicked.connect(self.output.clear)
        self.hub.buildResult.connect(self._on_build)
        self.hub.flashResult.connect(self._on_flash)

    def _on_build(self, payload: dict) -> None:
        ok = payload.get("ok")
        tail = payload.get("log_tail", "")
        mark = "✅ 构建成功" if ok else "❌ 构建失败"
        self.output.appendPlainText(f"===== {mark} =====\n{tail}\n")
        if ok:
            InfoBar.success("构建完成", "固件构建成功。", parent=self.window(), duration=3000)
        else:
            InfoBar.error("构建失败", "见输出控制台(可让 AI 自愈)。", parent=self.window(), duration=4000)

    def _on_flash(self, payload: dict) -> None:
        ok = payload.get("ok")
        reason = payload.get("reason", "")
        mark = "✅ 烧录成功" if ok else "❌ 烧录未成功"
        self.output.appendPlainText(f"===== {mark} {reason} =====\n")
        if ok:
            InfoBar.success("烧录完成", "固件已烧录并复位。", parent=self.window(), duration=3000)
        else:
            InfoBar.warning("烧录跳过", reason or "见输出控制台。", parent=self.window(), duration=4000)
