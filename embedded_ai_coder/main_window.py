"""主窗口:FluentWindow + 左侧 8 项导航 + 主题切换 + 引擎中枢。

装载 8 个页面(均注入 EngineHub),负责:
- 跨线程烧录确认(worker 阻塞等回答);
- 退出时优雅停止后台线程(closeEvent)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QMessageBox

from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    NavigationItemPosition,
    Theme,
    setTheme,
)

from .gui.hub import EngineHub
from .gui.pages.ai_coder import AiCoderPage
from .gui.pages.build_flash import BuildFlashPage
from .gui.pages.dashboard import DashboardPage
from .gui.pages.fault_diag import FaultDiagPage
from .gui.pages.history import HistoryPage
from .gui.pages.log_monitor import LogMonitorPage
from .gui.pages.project import ProjectPage
from .gui.pages.settings import SettingsPage

_THEME_MAP = {"light": Theme.LIGHT, "dark": Theme.DARK}


class MainWindow(FluentWindow):
    """EmbeddedAiCoder 主窗口。"""

    def __init__(self, config: dict[str, Any]):
        super().__init__()
        self.config = config
        self.hub = EngineHub(config, self)
        self.init_window()
        self.init_navigation()
        self.init_signals()

    def init_window(self) -> None:
        self.resize(1200, 800)
        self.setMinimumSize(QSize(960, 640))
        self.setWindowTitle("EmbeddedAiCoder — STM32 编码自动化")

        theme = self.config.get("ui", {}).get("theme", "auto")
        setTheme(_THEME_MAP.get(theme, Theme.AUTO))

    def init_navigation(self) -> None:
        hub = self.hub
        # 主导航(顶部)
        self.dashboard = DashboardPage(hub, self)
        self.log_monitor = LogMonitorPage(hub, self)
        self.fault_diag = FaultDiagPage(hub, self)
        self.ai_coder = AiCoderPage(hub, self)
        self.build_flash = BuildFlashPage(hub, self)
        self.project = ProjectPage(hub, self)

        self.addSubInterface(self.dashboard, FIF.HOME, "控制台")
        self.addSubInterface(self.log_monitor, FIF.DOCUMENT, "日志监控")
        self.addSubInterface(self.fault_diag, FIF.INFO, "故障诊断")
        self.addSubInterface(self.ai_coder, FIF.MESSAGE, "AI 编码")
        self.addSubInterface(self.build_flash, FIF.PLAY, "构建烧录")
        self.addSubInterface(self.project, FIF.FOLDER, "工程")

        # 次导航(底部)
        self.history = HistoryPage(hub, self)
        self.settings = SettingsPage(hub, self)
        self.addSubInterface(
            self.history, FIF.CALENDAR, "历史", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(
            self.settings, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM
        )

        self.navigationInterface.setExpandWidth(200)

    def init_signals(self) -> None:
        # 跨线程烧录确认:worker 发 (message, queue),GUI 弹框后回填答案
        self.hub.confirmRequested.connect(self._on_confirm)

    def _on_confirm(self, message: str, answer) -> None:
        """answer 是一个 queue.Queue;弹框后 put 布尔,解除 worker 阻塞。"""
        try:
            btn = QMessageBox.question(
                self, "烧录确认", message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            answer.put(btn == QMessageBox.StandardButton.Yes)
        except Exception:  # noqa: BLE001
            answer.put(False)

    def closeEvent(self, event) -> None:
        # 优雅停止后台线程,避免「QThread destroyed while still running」
        self.hub.shutdown()
        super().closeEvent(event)
