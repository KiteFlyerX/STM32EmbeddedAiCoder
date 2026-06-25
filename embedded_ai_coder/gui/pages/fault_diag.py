"""③ 故障诊断:Cortex-M HardFault 解码 + 栈回溯 + addr2line 源码定位(M1/M3)。"""

from __future__ import annotations

from .base import PlaceholderPage


class FaultDiagPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__(
            "故障诊断",
            "自动解码 HardFault(fault 寄存器 + 栈回溯),用 addr2line 映射到源码行,辅助 AI 定位根因。",
            parent,
        )
