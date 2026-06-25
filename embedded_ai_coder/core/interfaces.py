"""后台核心接口定义(M1 占位骨架)。

设计原则:
- 采集 / 烧录 / 构建 / AI 均抽象为接口,便于切换实现与单元测试。
- 闭环由 Orchestrator 编排;GUI 仅负责呈现与人工介入,不直接驱动硬件。
- 所有方法暂为 NotImplementedError,留待对应里程碑实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


# ---------- 1. 日志采集 ----------
class LogCollector(ABC):
    """日志采集器抽象接口。"""

    @abstractmethod
    def open(self) -> None:
        """打开采集通道。"""

    @abstractmethod
    def close(self) -> None:
        """关闭采集通道。"""

    @abstractmethod
    def iter_lines(self) -> Iterator[str]:
        """阻塞式逐行产出日志。"""


class SerialCollector(LogCollector):
    """UART 串口采集(M1):基于 pyserial。"""

    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate

    def open(self) -> None:
        raise NotImplementedError("M1 待实现:pyserial 连接")

    def close(self) -> None:
        raise NotImplementedError

    def iter_lines(self) -> Iterator[str]:
        raise NotImplementedError


class RttCollector(LogCollector):
    """SEGGER RTT 经 SWD 采集(M3):基于 pyOCD,高速且不占用 UART。"""

    def open(self) -> None:
        raise NotImplementedError("M3 待实现:pyOCD RTT")

    def close(self) -> None:
        raise NotImplementedError

    def iter_lines(self) -> Iterator[str]:
        raise NotImplementedError


# ---------- 7. 烧录与复位 ----------
class Flasher(ABC):
    """烧录/复位抽象接口(后端可切换)。"""

    @abstractmethod
    def flash(self, firmware: str, reset: bool = True) -> bool:
        """烧录固件,返回是否成功。"""


class PyOcdFlasher(Flasher):
    """pyOCD 烧录(默认后端,Apache-2.0)。"""

    def __init__(self, probe: str = "stlink", target: str = ""):
        self.probe = probe
        self.target = target

    def flash(self, firmware: str, reset: bool = True) -> bool:
        raise NotImplementedError("M1 待实现:pyOCD flash")


# ---------- 4. AI 诊断与编码 ----------
class AiClient(ABC):
    """AI 客户端:输入日志/源码上下文,返回诊断 + 代码补丁。"""

    @abstractmethod
    def diagnose_and_patch(
        self, log_context: str, source_context: str, goal: str = ""
    ) -> dict:
        """返回结构化结果:{diagnosis, patches:[{file, anchor, old, new}]}"""


# ---------- 5. 代码回写 ----------
class Coder(ABC):
    """把 AI 产出的补丁安全写回工程文件(带回滚)。"""

    @abstractmethod
    def apply(self, patch: dict) -> bool:
        ...


# ---------- 6. 构建 ----------
class Builder(ABC):
    """编译固件,返回(成功, 输出日志)。"""

    @abstractmethod
    def build(self) -> tuple[bool, str]:
        ...


# ---------- 2. 故障解码 ----------
class FaultDecoder(ABC):
    """Cortex-M HardFault 解码:fault 寄存器 + 栈回溯 → 可读诊断 + 源码行。"""

    @abstractmethod
    def decode(self, fault_dump: str) -> dict:
        """返回:{reason, registers, stack:[{addr, symbol, file, line}]}"""


# ---------- 8. 闭环调度 ----------
class Orchestrator(ABC):
    """编排闭环:采集 → 解码/过滤 → AI → 回写 → 构建 → 烧录 → 复采。

    GUI 通过本接口控制运行;后台线程执行,通过回调/信号上报进度。
    """

    @abstractmethod
    def run(self, goal: str, max_iterations: int = 10) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
