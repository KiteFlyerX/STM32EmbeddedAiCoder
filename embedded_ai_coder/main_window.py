"""主窗口:FluentWindow + 左侧 8 项导航 + 主题切换。

装载 8 个页面:控制台 / 日志监控 / 故障诊断 / AI 编码 / 构建烧录 / 工程 / 设置 / 历史。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize

from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    NavigationItemPosition,
    Theme,
    setTheme,
)

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
        self.init_window()
        self.init_navigation()

    def init_window(self) -> None:
        self.resize(1180, 760)
        self.setMinimumSize(QSize(960, 640))
        self.setWindowTitle("EmbeddedAiCoder — STM32 编码自动化")

        theme = self.config.get("ui", {}).get("theme", "auto")
        setTheme(_THEME_MAP.get(theme, Theme.AUTO))

    def init_navigation(self) -> None:
        # 主导航(顶部)
        self.dashboard = DashboardPage(self)
        self.log_monitor = LogMonitorPage(self)
        self.fault_diag = FaultDiagPage(self)
        self.ai_coder = AiCoderPage(self)
        self.build_flash = BuildFlashPage(self)
        self.project = ProjectPage(self)

        self.addSubInterface(self.dashboard, FIF.HOME, "控制台")
        self.addSubInterface(self.log_monitor, FIF.DOCUMENT, "日志监控")
        self.addSubInterface(self.fault_diag, FIF.INFO, "故障诊断")
        self.addSubInterface(self.ai_coder, FIF.MESSAGE, "AI 编码")
        self.addSubInterface(self.build_flash, FIF.PLAY, "构建烧录")
        self.addSubInterface(self.project, FIF.FOLDER, "工程")

        # 次导航(底部)
        self.history = HistoryPage(self)
        self.settings = SettingsPage(self.config, self)
        self.addSubInterface(
            self.history, FIF.CALENDAR, "历史", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(
            self.settings, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM
        )

        self.navigationInterface.setExpandWidth(200)
