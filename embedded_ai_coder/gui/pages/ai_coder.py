"""④ AI 编码:AI 任务过程 + 代码 diff 预览 + 应用/回滚 + 对话历史(M2)。"""

from __future__ import annotations

from .base import PlaceholderPage


class AiCoderPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__(
            "AI 编码",
            "查看 AI 诊断思路与生成的 C 代码修改,逐项 diff 预览后应用或回滚,保留多轮对话历史。",
            parent,
        )
