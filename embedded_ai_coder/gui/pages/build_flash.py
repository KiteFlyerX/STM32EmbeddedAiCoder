"""⑤ 构建烧录:arm-gcc 构建输出 + pyOCD 烧录进度 + 后端切换(M1/M3)。"""

from __future__ import annotations

from .base import PlaceholderPage


class BuildFlashPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__(
            "构建烧录",
            "调用 arm-none-eabi-gcc 构建固件(编译错误可回喂 AI 自愈),经 pyOCD 等后端烧录并复位。",
            parent,
        )
