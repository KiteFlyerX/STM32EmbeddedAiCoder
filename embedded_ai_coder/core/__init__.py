"""后台引擎核心(接口骨架)。

各模块抽象接口见 interfaces.py;具体实现按里程碑逐步填充:
- M1:SerialCollector + PyOcdFlasher + AiClient + 闭环
- M2:GUI 接入引擎
- M3:RttCollector + FaultDecoder + 多烧录后端
"""

from .interfaces import (
    AiClient,
    Builder,
    Coder,
    FaultDecoder,
    Flasher,
    LogCollector,
    Orchestrator,
    PyOcdFlasher,
    RttCollector,
    SerialCollector,
)

__all__ = [
    "LogCollector",
    "SerialCollector",
    "RttCollector",
    "Flasher",
    "PyOcdFlasher",
    "AiClient",
    "Coder",
    "Builder",
    "FaultDecoder",
    "Orchestrator",
]
