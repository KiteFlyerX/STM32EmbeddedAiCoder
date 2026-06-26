"""
日志采集:SerialCollector(M1)。

基于 pyserial 流式读取 UART,后台线程逐行产出;支持断线重连。
对应 interfaces.SerialCollector(F-01)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from .interfaces import LogCollector

logger = logging.getLogger(__name__)


class SerialCollector(LogCollector):
    """UART 串口采集器。

    - open() 在后台线程持续读取,逐行推入内部队列;
    - iter_lines() 从队列取行,阻塞式产出(可被 stop 退出);
    - 串口不可用时优雅降级(日志提示),不抛异常中断整个闭环。

    除真实串口外,支持 ``from_file=`` 模式:把一个文本文件按行回放,
    用于离线演示 / 单测(不依赖硬件)。
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        *,
        reconnect_interval: float = 2.0,
        from_file: Optional[str | Path] = None,
        on_line: Optional[Callable[[str], None]] = None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.reconnect_interval = reconnect_interval
        self.from_file = Path(from_file) if from_file else None

        # 每行产出时同步回调(GUI 用来做实时日志流式展示);回调异常被吞,不影响采集
        self.on_line = on_line

        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._serial = None  # pyserial 实例(lazy import)
        self._log_path: Optional[Path] = None  # 落盘文件
        self._replay_consumed = False  # 文件回放是否已到 EOF(用于多轮重新回放)

    # ---------- 生命周期 ----------
    def open(self) -> None:
        """打开采集通道,启动后台读取线程。"""
        self._stop_event.clear()
        # 清空可能残留的队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._thread = threading.Thread(
            target=self._run, name="serial-collector", daemon=True
        )
        self._thread.start()
        logger.info("SerialCollector 已启动(port=%s, baud=%s)", self.port, self.baudrate)

    def close(self) -> None:
        """关闭采集通道与后台线程。"""
        self._stop_event.set()
        # 塞一个哨兵让 iter_lines 立刻退出
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        logger.info("SerialCollector 已关闭")

    # ---------- 后台读取 ----------
    def _run(self) -> None:
        """后台线程主体:真实串口 / 文件回放 / 不可用降级。"""
        if self.from_file is not None:
            self._replay_file()
            return
        try:
            self._loop_serial()
        except Exception as exc:  # 硬件相关,优雅降级
            logger.warning("SerialCollector 降级:%s", exc)
            self._queue.put(None)

    def _replay_file(self) -> None:
        """从文件逐行回放(演示/单测用)。"""
        assert self.from_file is not None
        try:
            with self.from_file.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if self._stop_event.is_set():
                        break
                    line = line.rstrip("\r\n")
                    self._emit(line)
                    time.sleep(0.02)  # 模拟串口节流,演示更直观
        except Exception as exc:
            logger.error("回放文件失败 %s:%s", self.from_file, exc)
        finally:
            self._replay_consumed = True
            self._queue.put(None)  # 结束哨兵

    def _loop_serial(self) -> None:
        """真实串口循环:断线自动重连。"""
        ser = self._open_serial()
        if ser is None:
            return
        try:
            while not self._stop_event.is_set():
                line = ser.readline()
                if not line:
                    continue
                try:
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                except Exception:
                    continue
                self._emit(text)
        except Exception as exc:
            logger.warning("串口读取异常:%s,尝试重连", exc)
            if not self._stop_event.is_set():
                time.sleep(self.reconnect_interval)
                # 递归重连(受 stop_event 保护,不会无限)
                self._loop_serial()

    def _open_serial(self):
        """打开 pyserial,失败返回 None。"""
        try:
            import serial  # lazy import,避免无硬件环境直接崩
        except ImportError:
            logger.error("未安装 pyserial,串口采集不可用")
            return None
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
            logger.info("已连接串口 %s @ %d", self.port, self.baudrate)
            return self._serial
        except Exception as exc:
            logger.warning("打开串口 %s 失败:%s", self.port, exc)
            return None

    def _emit(self, line: str) -> None:
        """产出一行:推队列 + 可选落盘 + 可选 on_line 回调。"""
        self._queue.put(line)
        if self.on_line is not None:
            try:
                self.on_line(line)
            except Exception:
                pass
        if self._log_path is not None:
            try:
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    # ---------- 对外迭代 ----------
    def iter_lines(self) -> Iterator[str]:
        """阻塞式逐行产出日志;收到 None 哨兵则结束。

        文件回放模式下,若上一轮已到 EOF,本轮自动从头重新回放(多轮闭环可用)。
        """
        if self.from_file is not None and self._replay_consumed:
            # 重置:清队列 + 重启回放线程
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._stop_event.clear()
            self._replay_consumed = False
            self._thread = threading.Thread(
                target=self._replay_file, name="serial-collector", daemon=True
            )
            self._thread.start()
        while True:
            if self._stop_event.is_set():
                return
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:  # 哨兵
                return
            yield item

    # ---------- 辅助 ----------
    def enable_log_to_file(self, path: str | Path) -> None:
        """启用落盘(每行追加)。"""
        self._log_path = Path(path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)


def make_collector(config: dict, on_line: Optional[Callable[[str], None]] = None) -> SerialCollector:
    """根据 config 构造采集器。on_line 用于 GUI 实时日志流(CLI 不传)。"""
    collector_cfg = config.get("collector", {}) or {}
    serial_cfg = collector_cfg.get("serial", {}) or {}
    port = serial_cfg.get("port", "COM3")
    baudrate = int(serial_cfg.get("baudrate", 115200))
    return SerialCollector(port=port, baudrate=baudrate, on_line=on_line)
