"""② 日志监控:串口 / RTT 实时日志流,error/fault 高亮 + 搜索过滤。

接 hub.logLine:与控制台共用同一份实时日志流,本页提供更大视图 + 搜索/暂停滚动/清空。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from qfluentwidgets import BodyLabel, LineEdit, PushButton, SwitchButton

from ..widgets import LiveLogView
from .base import PlaceholderPage


class LogMonitorPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        super().__init__(
            "日志监控",
            "实时展示串口 / RTT 日志;error / fault / assert 自动高亮,支持关键字搜索与暂停滚动。",
            parent,
        )

    def _build_content(self, layout) -> None:
        bar = QHBoxLayout()
        self.clearBtn = PushButton("清空", self)
        self.scrollSwitch = SwitchButton(self)
        self.scrollSwitch.setText("自动滚动")
        self.scrollSwitch.setChecked(True)
        self.filterEdit = LineEdit(self)
        self.filterEdit.setPlaceholderText("关键字过滤(不区分大小写),如 hardfault / i2c")
        bar.addWidget(self.clearBtn)
        bar.addWidget(self.scrollSwitch)
        bar.addWidget(BodyLabel("过滤:", self))
        bar.addWidget(self.filterEdit, 1)
        layout.addLayout(bar)

        self.liveLog = LiveLogView(self)
        layout.addWidget(self.liveLog, 1)

        # 绑定
        self.hub.logLine.connect(self.liveLog.append_line)
        self.clearBtn.clicked.connect(self.liveLog.clear_log)
        self.scrollSwitch.checkedChanged.connect(self.liveLog.set_autoscroll)
        self.filterEdit.textChanged.connect(self.liveLog.set_filter)
