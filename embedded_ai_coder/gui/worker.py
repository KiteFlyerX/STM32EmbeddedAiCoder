"""
GUI ↔ 引擎桥接:后台工作器 LoopWorker(QObject)。

把阻塞式的 ``Orchestrator.run()`` 放到 QThread 上执行,每步经 Qt 信号上报:
- on_step(stage, payload) → progress 信号;
- collector 的 on_line(line) → logLine 信号(实时日志流);
- 每轮 IterationResult → iterationDone 信号;
- 手动动作(构建/烧录/应用/回滚)→ 各自结果信号;
- 烧录前确认 → confirmRequested(跨线程问答,阻塞 worker 直到 GUI 回答)。

控制语义:
- run(goal, max_iter, demo):在 worker 线程内建 orchestrator + 开采集 + 跑闭环;
- pause/resume/stop:只操作 threading.Event / 标志,**直接跨线程调用**(不必经事件循环,
  因为 run() 期间事件循环被阻塞,这些调用只动 Event/bool,线程安全);
- 演示模式(demo=True):采集器切到文件回放,AI 走 mock,flasher dry-run —— 无硬件/key 可跑。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Signal, Slot

from ..core.orchestrator import IterationResult, make_orchestrator

logger = logging.getLogger(__name__)

# 演示日志默认路径(包根下的 examples/demo_log.txt)
_DEMO_LOG_DEFAULT = Path(__file__).resolve().parents[2] / "examples" / "demo_log.txt"


def _serialize(res: IterationResult) -> dict[str, Any]:
    """把 IterationResult 转成 JSON 友好的 dict(GUI 用)。"""
    return {
        "iteration": res.iteration,
        "log_lines": list(res.log_lines),
        "fault_fragment": res.fault_fragment,
        "symbols": list(res.symbols),
        "tokenbase_echo": list(res.tokenbase_echo),
        "docs_echo": list(res.docs_echo),
        "diagnosis": res.diagnosis,
        "patches": list(res.patches),
        "patches_applied": res.patches_applied,
        "patches_failed": res.patches_failed,
        "build_ok": res.build_ok,
        "flashed": res.flashed,
        "verified": res.verified,
        "note": res.note,
    }


class LoopWorker(QObject):
    """在后台线程跑闭环的工作器。"""

    # ---- worker → hub(经 hub relay 给各页面)----
    progress = Signal(str, dict)          # 对应 orchestrator on_step(stage, payload)
    logLine = Signal(str)                 # 实时日志行(collector 线程发射,跨线程安全)
    stateChanged = Signal(str)            # idle|running|paused|stopping|done|error
    iterationDone = Signal(dict)          # 单轮结果(序列化)
    aiPatches = Signal(list, str, bool)   # (patches, diagnosis, mock) —— 供 AI 编码页
    error = Signal(str)
    finishedRun = Signal(int)             # 本轮跑了几轮

    # ---- 手动动作结果 ----
    buildResult = Signal(dict)            # {ok, log_tail}
    flashResult = Signal(dict)            # {ok, reason}
    applyResult = Signal(dict)            # {applied, failed, file, ok}
    rollbackResult = Signal(int)          # 回滚条数
    implementResult = Signal(dict)        # AI 实现结果 {summary, files, written, skipped, mock, error}

    # ---- 跨线程确认(GUI 弹框,worker 阻塞等回答)----
    confirmRequested = Signal(str, object)  # (message, answer_queue)

    def __init__(self, config_getter: Callable[[], dict], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config_getter = config_getter        # 返回当前有效 config(hub 持有,可被设置页更新)
        self._orch = None
        self._pause_event = threading.Event()
        self._pause_event.set()                    # set=运行,clear=暂停
        self._running = False
        self._demo_last = False                    # 记住最近一次 demo 态(供手动动作)

    # ---------- 主入口(worker 线程执行,经 _runRequested 信号排队触发)----------
    @Slot(str, int, bool)
    def run(self, goal: str, max_iterations: int, demo: bool) -> None:
        if self._running:
            logger.warning("闭环已在运行,忽略重复启动")
            return
        self._running = True
        self._demo_last = demo
        self._pause_event.set()
        config = dict(self._config_getter())
        try:
            self.stateChanged.emit("running")
            # 演示模式无固件可烧,直接跳过烧录确认(与 CLI --dry-run 一致);
            # 真实模式才弹框确认(confirm_fn 跨线程问答)。
            confirm = (lambda msg: False) if demo else self._confirm
            orch = make_orchestrator(
                config,
                dry_run=demo,
                on_step=lambda s, p: self.progress.emit(s, p),
                on_line=lambda line: self.logLine.emit(line),
                pause_event=self._pause_event,
                confirm_fn=confirm,
            )
            self._orch = orch
            # 演示模式:把采集器换成文件回放
            if demo:
                from ..core.collector import SerialCollector
                demo_log = (config.get("collector", {}) or {}).get("demo_log") or str(_DEMO_LOG_DEFAULT)
                orch.collector = SerialCollector(
                    port="(replay)", baudrate=0, from_file=demo_log,
                    on_line=lambda line: self.logLine.emit(line),
                )
            else:
                # 真实串口:确保 on_line 已接上(make_orchestrator 已接,这里幂等)
                orch.collector.on_line = lambda line: self.logLine.emit(line)

            n = max_iterations or int((config.get("loop", {}) or {}).get("max_iterations", 10))
            orch.collector.open()
            count = 0
            try:
                for res in orch.run(goal=goal or "修复串口日志中的 fault", max_iterations=n):
                    count += 1
                    payload = _serialize(res)
                    self.iterationDone.emit(payload)
                    # 把 AI 补丁单独发一份给 AI 编码页(mock 标记从 meta 推不出,用 demo 近似)
                    if res.patches:
                        self.aiPatches.emit(list(res.patches), res.diagnosis, demo)
            finally:
                orch.collector.close()
            self.finishedRun.emit(count)
            self.stateChanged.emit("done")
        except Exception as exc:  # noqa: BLE001
            logger.exception("闭环运行异常")
            self.error.emit(f"{type(exc).__name__}: {exc}")
            self.stateChanged.emit("error")
        finally:
            self._running = False

    # ---------- 控制(直接跨线程调用,只动 Event/标志)----------
    def pause(self) -> None:
        self._pause_event.clear()
        self.stateChanged.emit("paused")

    def resume(self) -> None:
        self._pause_event.set()
        self.stateChanged.emit("running")

    def stop(self) -> None:
        if self._orch is not None:
            try:
                self._orch.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pause_event.set()  # 解除暂停,使其能观察到 stop
        if self._running:
            self.stateChanged.emit("stopping")

    # ---------- 手动动作(worker 线程,经 *_Requested 信号排队触发)----------
    @Slot()
    def do_build(self) -> None:
        orch = self._ensure_orch()
        if orch is None:
            return
        ok, log = orch.builder.build()
        self.buildResult.emit({"ok": ok, "log_tail": log[-2000:] if log else ""})

    @Slot()
    def do_flash(self) -> None:
        orch = self._ensure_orch()
        if orch is None:
            return
        fw = getattr(orch.flasher, "firmware", "") or ""
        if not fw:
            self.flashResult.emit({"ok": False, "reason": "未配置 flasher.firmware"})
            return
        ok = orch.flasher.flash(fw)
        self.flashResult.emit({"ok": ok, "reason": "" if ok else "烧录未成功(见日志)"})

    @Slot(dict)
    def do_apply(self, patch: dict) -> None:
        orch = self._ensure_orch()
        if orch is None:
            return
        ok = orch.coder.apply(patch)
        self.applyResult.emit({
            "ok": ok, "file": patch.get("file", ""),
            "applied": 1 if ok else 0, "failed": 0 if ok else 1,
        })

    @Slot()
    def do_rollback(self) -> None:
        orch = self._ensure_orch()
        if orch is None:
            return
        n = orch.coder.rollback_all()
        self.rollbackResult.emit(n)

    @Slot(str)
    def do_implement(self, goal: str) -> None:
        """据预读的原理图+需求文档,用 AI 实现产品固件并写回工程(worker 线程)。"""
        orch = self._ensure_orch()
        if orch is None:
            return
        try:
            out = orch.ai_client.implement_from_docs(goal=goal or "")
            files = out.get("files", [])
            written, skipped = (0, 0)
            if files:
                written, skipped = orch.coder.write_files(files)
            meta = out.get("meta", {}) or {}
            self.implementResult.emit({
                "summary": out.get("summary", ""),
                "files": [{"path": f["path"], "reason": f.get("reason", ""),
                           "chars": len(f.get("content", ""))} for f in files],
                "written": written, "skipped": skipped,
                "mock": meta.get("mock", False),
                "error": meta.get("error", ""),
                "tb_symbols": meta.get("tokenbase_symbols", []),
                "tb_hits": sum(1 for e in (meta.get("tokenbase_echo", []) or [])
                               if "未在 tokenbase" not in e and "exit_ok=True" in e),
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("implement 失败")
            self.implementResult.emit({
                "summary": "", "files": [], "written": 0, "skipped": 0,
                "mock": False, "error": f"{type(exc).__name__}: {exc}",
            })

    # ---------- 内部 ----------
    def _ensure_orch(self):
        """手动动作前确保 orchestrator 已建(复用上次的;无则按当前 config 建一个)。"""
        if self._orch is not None:
            return self._orch
        try:
            config = dict(self._config_getter())
            orch = make_orchestrator(
                config, dry_run=self._demo_last,
                on_step=lambda s, p: self.progress.emit(s, p),
                on_line=lambda line: self.logLine.emit(line),
                pause_event=self._pause_event, confirm_fn=self._confirm,
            )
            self._orch = orch
            return orch
        except Exception as exc:  # noqa: BLE001
            logger.exception("构建 orchestrator 失败")
            self.error.emit(f"构建引擎失败:{exc}")
            return None

    def _confirm(self, message: str) -> bool:
        """烧录前跨线程确认:发到 GUI 线程弹框,阻塞等回答。"""
        answer: "queue.Queue[bool]" = queue.Queue()
        self.confirmRequested.emit(message, answer)
        try:
            return bool(answer.get(timeout=300))
        except queue.Empty:
            return False
