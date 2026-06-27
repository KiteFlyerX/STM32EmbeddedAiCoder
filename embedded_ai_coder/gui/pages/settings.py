"""⑦ 设置:采集 / 烧录 / 构建 / AI / 闭环 / 主题,与 config/*.yaml 双向同步。

所有改动可「保存到 local.yaml」(密钥只落 local.yaml,已 gitignore);主题即时切换。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    SettingCard,
    SpinBox,
    StrongBodyLabel,
    SwitchButton,
    Theme,
    TitleLabel,
    setTheme,
)

from ...config import save_local_overlay

_THEME_MAP = {"light": Theme.LIGHT, "dark": Theme.DARK}


class SettingsPage(QWidget):
    def __init__(self, hub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.config = hub.config
        self.setObjectName("SettingsPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 12)
        root.setSpacing(8)
        root.addWidget(TitleLabel("设置", self))
        root.addWidget(
            BodyLabel("配置采集、烧录、构建、AI 与闭环参数(对应 config/*.yaml)。", self)
        )
        root.addSpacing(12)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget(scroll)
        col = QVBoxLayout(inner)
        col.setSpacing(6)

        col.addWidget(self._section("采集"))
        col.addWidget(self._serialCard())
        col.addWidget(self._section("烧录 / 调试器"))
        col.addWidget(self._flasherCard())
        col.addWidget(self._debuggerCard())
        col.addWidget(self._section("AI"))
        col.addWidget(self._aiProviderCard())
        col.addWidget(self._aiModelCard())
        col.addWidget(self._aiKeyCard())
        col.addWidget(self._section("闭环"))
        col.addWidget(self._loopMaxCard())
        col.addWidget(self._loopTimeoutCard())
        col.addWidget(self._loopAutoFlashCard())
        col.addWidget(self._loopAutoBuildCard())
        col.addWidget(self._section("界面"))
        col.addWidget(self._themeCard())
        col.addStretch(1)

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # 保存栏
        bar = QHBoxLayout()
        self.btnSave = PrimaryPushButton("💾 保存到 local.yaml", self)
        bar.addWidget(self.btnSave)
        bar.addStretch(1)
        root.addLayout(bar)

        self.btnSave.clicked.connect(self._on_save)

    # ---------- 区段 ----------
    def _section(self, text: str) -> StrongBodyLabel:
        s = StrongBodyLabel(text, self)
        return s

    def _card(self, icon, title: str, content: str) -> SettingCard:
        return SettingCard(icon, title, content, self)

    def _serialCard(self) -> SettingCard:
        card = self._card(FIF.LINK, "串口端口 / 波特率", "Windows: COM3;Linux: /dev/ttyUSB0")
        h = QHBoxLayout()
        self.cbPort = LineEdit(self)
        self.cbPort.setFixedWidth(160)
        self.cbPort.setText((self.config.get("collector", {}) or {}).get("serial", {}).get("port", "COM3"))
        self.sbBaud = SpinBox(self)
        self.sbBaud.setRange(1200, 4000000)
        self.sbBaud.setValue(int((self.config.get("collector", {}) or {}).get("serial", {}).get("baudrate", 115200)))
        h.addWidget(self.cbPort)
        h.addWidget(self.sbBaud)
        h.addSpacing(16)
        for w in (self.cbPort, self.sbBaud):
            card.hBoxLayout.addWidget(w)
        card.hBoxLayout.addSpacing(16)
        return card

    def _flasherCard(self) -> SettingCard:
        card = self._card(FIF.SYNC, "烧录后端", "pyOCD / STM32CubeProgrammer / OpenOCD")
        self.cbBackend = ComboBox(self)
        self.cbBackend.addItems(["pyocd", "stm32cubeprogrammer", "openocd"])
        self.cbBackend.setCurrentText((self.config.get("flasher", {}) or {}).get("backend", "pyocd"))
        card.hBoxLayout.addWidget(self.cbBackend)
        card.hBoxLayout.addSpacing(16)
        return card

    def _debuggerCard(self) -> SettingCard:
        card = self._card(FIF.INFO, "调试器探针", "ST-Link / J-Link / CMSIS-DAP")
        self.cbProbe = ComboBox(self)
        self.cbProbe.addItems(["stlink", "jlink", "cmsis-dap"])
        self.cbProbe.setCurrentText((self.config.get("debugger", {}) or {}).get("probe", "stlink"))
        card.hBoxLayout.addWidget(self.cbProbe)
        card.hBoxLayout.addSpacing(16)
        return card

    def _aiProviderCard(self) -> SettingCard:
        card = self._card(FIF.MESSAGE, "AI 提供商", "claude / openai / local(经 OpenAI 兼容 base_url)")
        self.cbProvider = ComboBox(self)
        self.cbProvider.addItems(["", "claude", "openai", "local"])
        self.cbProvider.setCurrentText((self.config.get("ai", {}) or {}).get("provider", ""))
        self.edBaseUrl = LineEdit(self)
        self.edBaseUrl.setPlaceholderText("base_url,如 https://api.openai.com/v1")
        self.edBaseUrl.setText((self.config.get("ai", {}) or {}).get("base_url", ""))
        card.hBoxLayout.addWidget(self.cbProvider)
        card.hBoxLayout.addWidget(self.edBaseUrl)
        card.hBoxLayout.addSpacing(16)
        return card

    def _aiModelCard(self) -> SettingCard:
        card = self._card(FIF.MESSAGE, "AI 模型", "如 gpt-4o-mini / claude-sonnet-4-6")
        self.edModel = LineEdit(self)
        self.edModel.setText((self.config.get("ai", {}) or {}).get("model", ""))
        card.hBoxLayout.addWidget(self.edModel)
        card.hBoxLayout.addSpacing(16)
        return card

    def _aiKeyCard(self) -> SettingCard:
        card = self._card(FIF.CERTIFICATE, "API Key", "仅存 local.yaml(已 gitignore);留空走 mock 演示")
        self.edApiKey = PasswordLineEdit(self)
        self.edApiKey.setText((self.config.get("ai", {}) or {}).get("api_key", ""))
        card.hBoxLayout.addWidget(self.edApiKey)
        card.hBoxLayout.addSpacing(16)
        return card

    def _loopMaxCard(self) -> SettingCard:
        card = self._card(FIF.HOME, "最大迭代轮数", "自动闭环的最多尝试次数")
        self.sbMaxIter = SpinBox(self)
        self.sbMaxIter.setRange(1, 100)
        self.sbMaxIter.setValue(int((self.config.get("loop", {}) or {}).get("max_iterations", 10)))
        card.hBoxLayout.addWidget(self.sbMaxIter)
        card.hBoxLayout.addSpacing(16)
        return card

    def _loopTimeoutCard(self) -> SettingCard:
        card = self._card(FIF.HISTORY, "闭环超时(秒)", "整体闭环的最大运行时长")
        self.sbTimeout = SpinBox(self)
        self.sbTimeout.setRange(10, 7200)
        self.sbTimeout.setValue(int((self.config.get("loop", {}) or {}).get("timeout_sec", 600)))
        card.hBoxLayout.addWidget(self.sbTimeout)
        card.hBoxLayout.addSpacing(16)
        return card

    def _loopAutoFlashCard(self) -> SettingCard:
        card = self._card(FIF.SEND, "自动烧录", "开启后闭环自动烧录(默认关闭,烧录前确认)")
        self.swAutoFlash = SwitchButton(self)
        self.swAutoFlash.setChecked(bool((self.config.get("loop", {}) or {}).get("auto_flash", False)))
        card.hBoxLayout.addWidget(self.swAutoFlash)
        card.hBoxLayout.addSpacing(16)
        return card

    def _loopAutoBuildCard(self) -> SettingCard:
        card = self._card(FIF.SYNC, "自动编译 + 编译自愈",
                          "编译失败自动把错误回喂 AI 改码重编;开启后全程无需人工接管编译")
        self.swAutoBuild = SwitchButton(self)
        self.swAutoBuild.setChecked(bool((self.config.get("loop", {}) or {}).get("auto_build", True)))
        card.hBoxLayout.addWidget(BodyLabel("自愈轮数:", self))
        self.sbBuildHeal = SpinBox(self)
        self.sbBuildHeal.setRange(0, 10)
        self.sbBuildHeal.setValue(int((self.config.get("loop", {}) or {}).get("build_heal_retries", 2)))
        card.hBoxLayout.addWidget(self.sbBuildHeal)
        card.hBoxLayout.addWidget(self.swAutoBuild)
        card.hBoxLayout.addSpacing(16)
        return card

    def _themeCard(self) -> SettingCard:
        card = self._card(FIF.BASKETBALL, "主题", "亮色 / 暗色 / 跟随系统(即时切换)")
        self.cbTheme = ComboBox(self)
        self.cbTheme.addItems(["auto", "light", "dark"])
        self.cbTheme.setCurrentText((self.config.get("ui", {}) or {}).get("theme", "auto"))
        self.cbTheme.currentTextChanged.connect(self._on_theme_changed)
        card.hBoxLayout.addWidget(self.cbTheme)
        card.hBoxLayout.addSpacing(16)
        return card

    # ---------- 动作 ----------
    def _on_theme_changed(self, value: str) -> None:
        setTheme(_THEME_MAP.get(value, Theme.AUTO))

    def _on_save(self) -> None:
        overlay: dict[str, Any] = {
            "collector": {"serial": {"port": self.cbPort.text().strip(),
                                     "baudrate": self.sbBaud.value()}},
            "debugger": {"probe": self.cbProbe.currentText()},
            "flasher": {"backend": self.cbBackend.currentText()},
            "ai": {
                "provider": self.cbProvider.currentText(),
                "model": self.edModel.text().strip(),
                "base_url": self.edBaseUrl.text().strip(),
                "api_key": self.edApiKey.text(),
            },
            "loop": {
                "max_iterations": self.sbMaxIter.value(),
                "timeout_sec": self.sbTimeout.value(),
                "auto_flash": self.swAutoFlash.isChecked(),
                "auto_build": self.swAutoBuild.isChecked(),
                "build_heal_retries": self.sbBuildHeal.value(),
            },
            "ui": {"theme": self.cbTheme.currentText()},
        }
        try:
            save_local_overlay(overlay)
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("保存失败", str(exc), parent=self.window(), duration=4000)
            return
        # 同步内存 config(深合并)
        for k, v in overlay.items():
            cur = self.config.get(k)
            if isinstance(cur, dict) and isinstance(v, dict):
                cur.update(v)
            else:
                self.config[k] = v
        InfoBar.success("已保存", "设置已写入 local.yaml,下次启动与即时生效。", parent=self.window(), duration=3000)
