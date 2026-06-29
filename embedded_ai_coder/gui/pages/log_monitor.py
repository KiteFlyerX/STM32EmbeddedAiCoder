"""② 日志监控:串口 / RTT 实时日志流,error/fault 高亮 + 搜索过滤。

接 hub.logLine:与控制台共用同一份实时日志流,本页提供更大视图 + 搜索/暂停滚动/清空。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from qfluentwidgets import (BodyLabel, LineEdit, PrimaryPushButton, PushButton,
                            StrongBodyLabel, SwitchButton)

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
        # 监听控制:不跑 AI 闭环也能持续读串口
        monBar = QHBoxLayout()
        self.btnStartMon = PrimaryPushButton("▶ 开始监听", self)
        self.btnStopMon = PushButton("⏹ 停止监听", self)
        self.btnStopMon.setEnabled(False)
        self.demoSwitch = SwitchButton(self)
        self.demoSwitch.setText("演示回放")
        self.monStatus = StrongBodyLabel("● 未监听", self)
        monBar.addWidget(self.btnStartMon)
        monBar.addWidget(self.btnStopMon)
        monBar.addWidget(BodyLabel("演示回放:", self))
        monBar.addWidget(self.demoSwitch)
        monBar.addStretch(1)
        monBar.addWidget(self.monStatus)
        layout.addLayout(monBar)

        # 过滤/清空/滚动
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
        self.hub.monitorStateChanged.connect(self._on_monitor_state)
        self.clearBtn.clicked.connect(self.liveLog.clear_log)
        self.scrollSwitch.checkedChanged.connect(self.liveLog.set_autoscroll)
        self.filterEdit.textChanged.connect(self.liveLog.set_filter)
        self.btnStartMon.clicked.connect(
            lambda: self.hub.start_monitor(self.demoSwitch.isChecked()))
        self.btnStopMon.clicked.connect(self.hub.stop_monitor)

    def _on_monitor_state(self, state: str) -> None:
        monitoring = state == "monitoring"
        self.btnStartMon.setEnabled(not monitoring)
        self.btnStopMon.setEnabled(monitoring)
        if state == "monitoring":
            port = "(演示回放)" if self.demoSwitch.isChecked() else self._port_text()
            self.monStatus.setText(f"● 监听中:{port}")
        elif state == "error":
            self.monStatus.setText("● 监听失败(见提示/日志)")
        else:
            self.monStatus.setText("● 未监听")

    def _port_text(self) -> str:
        ser = (self.hub.config.get("collector", {}) or {}).get("serial", {}) or {}
        return f"{ser.get('port','?')}@{ser.get('baudrate','?')}"
