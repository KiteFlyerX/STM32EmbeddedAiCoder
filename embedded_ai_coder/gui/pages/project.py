"""⑥ 工程:工程路径 / 芯片型号 / 构建方式 / 源码树浏览。"""

from __future__ import annotations

from .base import PlaceholderPage


class ProjectPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        super().__init__(
            "工程",
            "配置与浏览 STM32 工程:根目录、芯片型号、构建方式、源码树,供 AI 提取上下文。",
            parent,
        )
