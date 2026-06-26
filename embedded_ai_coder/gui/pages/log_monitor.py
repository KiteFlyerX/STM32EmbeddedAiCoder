"""② 日志监控:串口 / RTT 实时日志流,error/fault 高亮 + 搜索过滤(M1 实现)。"""

from __future__ import annotations

from .base import PlaceholderPage


class LogMonitorPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        super().__init__(
            "日志监控",
            "实时展示串口 / RTT 日志;error / fault / assert 自动高亮,支持关键字搜索与通道切换。",
            parent,
        )
