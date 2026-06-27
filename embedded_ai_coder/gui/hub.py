"""
GUI ↔ 引擎桥接:中枢 EngineHub(QObject)。

职责:
- 拥有 QThread + LoopWorker(worker.moveToThread),生命周期随 MainWindow;
- 对外暴露稳定的信号集(progress/logLine/stateChanged/iterationDone/aiPatches/
  buildResult/flashResult/applyResult/rollbackResult/error/confirmRequested),各页面只连 hub;
- 提供控制方法 start_loop/pause/resume/stop 与手动动作 build_now/flash_now/apply_patch/rollback;
- pause/resume/stop 直接跨线程调用 worker(只动 threading.Event/标志,线程安全);
  run 与手动动作经内部信号排队到 worker 线程执行(真正干活,不能阻塞 GUI)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from .worker import LoopWorker

logger = logging.getLogger(__name__)


class EngineHub(QObject):
    """引擎中枢:GUI 各页面通过它驱动后台闭环。"""

    # ---- worker → 页面(forward)----
    progress = Signal(str, dict)
    logLine = Signal(str)
    stateChanged = Signal(str)
    iterationDone = Signal(dict)
    aiPatches = Signal(list, str, bool)      # patches, diagnosis, mock
    error = Signal(str)
    finishedRun = Signal(int)
    buildResult = Signal(dict)
    flashResult = Signal(dict)
    applyResult = Signal(dict)
    rollbackResult = Signal(int)
    implementResult = Signal(dict)        # AI 实现结果
    confirmRequested = Signal(str, object)   # 跨线程确认(message, answer_queue)

    # ---- 页面 → worker(排队到 worker 线程)----
    _runRequested = Signal(str, int, bool)
    _buildRequested = Signal()
    _flashRequested = Signal()
    _applyRequested = Signal(dict)
    _rollbackRequested = Signal()
    _implementRequested = Signal(str)

    def __init__(self, config: dict[str, Any], parent: Optional[QObject] = None):
        super().__init__(parent)
        self.config = config  # 可变 dict;设置/工程页更新它,worker 启动时读取

        self.thread = QThread(self)
        self.thread.setObjectName("embedded-ai-coder-engine")
        self.worker = LoopWorker(config_getter=lambda: self.config)
        self.worker.moveToThread(self.thread)

        # 控制信号 → worker 槽(跨线程自动排队)
        self._runRequested.connect(self.worker.run)
        self._buildRequested.connect(self.worker.do_build)
        self._flashRequested.connect(self.worker.do_flash)
        self._applyRequested.connect(self.worker.do_apply)
        self._rollbackRequested.connect(self.worker.do_rollback)
        self._implementRequested.connect(self.worker.do_implement)

        # worker 信号 → hub 信号(转发,跨线程回到 GUI 线程)
        self.worker.progress.connect(self.progress)
        self.worker.logLine.connect(self.logLine)
        self.worker.stateChanged.connect(self.stateChanged)
        self.worker.iterationDone.connect(self.iterationDone)
        self.worker.aiPatches.connect(self.aiPatches)
        self.worker.error.connect(self.error)
        self.worker.finishedRun.connect(self.finishedRun)
        self.worker.buildResult.connect(self.buildResult)
        self.worker.flashResult.connect(self.flashResult)
        self.worker.applyResult.connect(self.applyResult)
        self.worker.rollbackResult.connect(self.rollbackResult)
        self.worker.implementResult.connect(self.implementResult)
        self.worker.confirmRequested.connect(self.confirmRequested)

        self.thread.start()

    # ---------- 闭环控制 ----------
    def start_loop(self, goal: str, max_iterations: int, demo: bool) -> None:
        self._runRequested.emit(goal, max_iterations, demo)

    def pause(self) -> None:
        # 直接跨线程:只操作 Event / 发状态信号,线程安全
        self.worker.pause()

    def resume(self) -> None:
        self.worker.resume()

    def stop(self) -> None:
        self.worker.stop()

    # ---------- 手动动作 ----------
    def build_now(self) -> None:
        self._buildRequested.emit()

    def flash_now(self) -> None:
        self._flashRequested.emit()

    def apply_patch(self, patch: dict) -> None:
        self._applyRequested.emit(patch)

    def rollback(self) -> None:
        self._rollbackRequested.emit()

    def implement_now(self, goal: str) -> None:
        """据原理图+需求文档,用 AI 实现产品固件(排队到 worker 线程)。"""
        self._implementRequested.emit(goal)

    # ---------- 生命周期 ----------
    def shutdown(self, timeout_ms: int = 10000) -> None:
        """优雅停止 worker 并退出线程(MainWindow.closeEvent 调用)。"""
        try:
            self.worker.stop()
        except Exception:  # noqa: BLE001
            pass
        self.thread.quit()
        self.thread.wait(timeout_ms)
