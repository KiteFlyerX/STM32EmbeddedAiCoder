"""⑧ 历史:每轮迭代(日志/fault/diff/结果)回放(P2)。"""

from __future__ import annotations

from .base import PlaceholderPage


class HistoryPage(PlaceholderPage):
    def __init__(self, hub, parent=None):
        self.hub = hub
        super().__init__(
            "历史",
            "回溯每一次迭代的原始日志、fault 解码、AI 输出与代码 diff,支持复现(P2)。",
            parent,
        )
