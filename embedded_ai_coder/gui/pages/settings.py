"""⑦ 设置:采集 / 烧录 / 构建 / AI / 闭环参数,与 config/*.yaml 对应。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    SettingCard,
    SpinBox,
    StrongBodyLabel,
    TitleLabel,
)


class SettingsPage(QWidget):
    def __init__(self, config: dict[str, Any], parent=None):
        super().__init__(parent)
        self.config = config
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

        col.addWidget(self._section("采集 / 烧录"))
        col.addWidget(self._flasherCard())
        col.addWidget(self._debuggerCard())
        col.addWidget(self._section("AI"))
        col.addWidget(self._aiCard())
        col.addWidget(self._section("闭环"))
        col.addWidget(self._loopCard())
        col.addStretch(1)

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def _section(self, text: str) -> StrongBodyLabel:
        return StrongBodyLabel(text, self)

    def _flasherCard(self) -> SettingCard:
        card = SettingCard(
            FIF.SYNC, "烧录后端", "pyOCD / STM32CubeProgrammer / OpenOCD", self
        )
        combo = ComboBox(self)
        combo.addItems(["pyocd", "stm32cubeprogrammer", "openocd"])
        combo.setCurrentText(self.config.get("flasher", {}).get("backend", "pyocd"))
        card.hBoxLayout.addWidget(combo)
        card.hBoxLayout.addSpacing(16)
        return card

    def _debuggerCard(self) -> SettingCard:
        card = SettingCard(FIF.INFO, "调试器", "ST-Link / J-Link / CMSIS-DAP", self)
        combo = ComboBox(self)
        combo.addItems(["stlink", "jlink", "cmsis-dap"])
        combo.setCurrentText(self.config.get("debugger", {}).get("probe", "stlink"))
        card.hBoxLayout.addWidget(combo)
        card.hBoxLayout.addSpacing(16)
        return card

    def _aiCard(self) -> SettingCard:
        card = SettingCard(
            FIF.MESSAGE, "AI 提供商", "claude / openai / local (待填 G-02)", self
        )
        edit = LineEdit(self)
        edit.setPlaceholderText("provider")
        edit.setText(self.config.get("ai", {}).get("provider", "") or "")
        card.hBoxLayout.addWidget(edit)
        card.hBoxLayout.addSpacing(16)
        return card

    def _loopCard(self) -> SettingCard:
        card = SettingCard(FIF.HOME, "最大迭代轮数", "自动闭环的最多尝试次数", self)
        spin = SpinBox(self)
        spin.setRange(1, 100)
        spin.setValue(int(self.config.get("loop", {}).get("max_iterations", 10)))
        card.hBoxLayout.addWidget(spin)
        card.hBoxLayout.addSpacing(16)
        return card
