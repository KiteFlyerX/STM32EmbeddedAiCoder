"""
闭环调度:Orchestrator(M1)。

串联:采集 → 过滤 → AiClient(取 tokenbase 上下文+诊断+补丁)→ Coder 回写
       → 构建(可选)→ Flasher 烧录 → 复采验证。

特性:
- 最大轮数 / 超时 / 熔断(连续失败或无 patch 则停);
- 人工确认(loop.auto_flash=false 时,烧录前停下问 y/N);
- 全程打印每步结果,CLI 与未来 GUI 共用本引擎。
对应需求 F-06。基于 interfaces.Orchestrator。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .ai_client import AiClientImpl
from .builder import Builder
from .coder import CoderImpl
from .collector import SerialCollector
from .filter import LogFilter
from .flasher import PyOcdFlasher
from .interfaces import Orchestrator

logger = logging.getLogger(__name__)

# 熔断阈值
MAX_CONSECUTIVE_FAILS = 3


@dataclass
class IterationResult:
    """一轮闭环的结果(供 GUI / CLI 呈现)。"""
    iteration: int
    log_lines: list[str] = field(default_factory=list)
    fault_fragment: str = ""
    symbols: list[str] = field(default_factory=list)
    tokenbase_echo: list[str] = field(default_factory=list)
    docs_echo: list[str] = field(default_factory=list)
    diagnosis: str = ""
    patches: list[dict] = field(default_factory=list)
    patches_applied: int = 0
    patches_failed: int = 0
    build_ok: Optional[bool] = None
    flashed: Optional[bool] = None
    verified: Optional[bool] = None  # 复采后 fault 是否消失
    note: str = ""


class OrchestratorImpl(Orchestrator):
    """M1 闭环编排器。"""

    def __init__(
        self,
        collector: SerialCollector,
        log_filter: LogFilter,
        ai_client: AiClientImpl,
        coder: CoderImpl,
        builder: Builder,
        flasher: PyOcdFlasher,
        *,
        max_iterations: int = 10,
        timeout_sec: float = 600.0,
        auto_flash: bool = False,
        auto_build: bool = True,
        build_heal_retries: int = 2,
        confirm_fn: Optional[Callable[[str], bool]] = None,
        collect_lines_per_iter: int = 200,
        on_step: Optional[Callable[[str, dict], None]] = None,
        pause_event: Optional[threading.Event] = None,
    ):
        self.collector = collector
        self.log_filter = log_filter
        self.ai_client = ai_client
        self.coder = coder
        self.builder = builder
        self.flasher = flasher
        self.max_iterations = max_iterations
        self.timeout_sec = timeout_sec
        self.auto_flash = auto_flash
        self.auto_build = auto_build                  # 编译失败是否回喂 AI 自愈
        self.build_heal_retries = build_heal_retries  # 自愈最多尝试轮数
        # confirm_fn(message)->bool;默认 input() 交互(M1 CLI 用)
        self.confirm_fn = confirm_fn or self._default_confirm
        self.collect_lines_per_iter = collect_lines_per_iter
        # on_step(stage, payload):每步回调,CLI 打印 / GUI 上报都用它
        self.on_step = on_step or (lambda stage, payload: None)
        # pause_event:set=运行,clear=暂停(GUI 用);None(CLI)=永不暂停
        self._pause_event = pause_event
        self._stop = False

    # ---------- 主入口 ----------
    def run(self, goal: str, max_iterations: int = 10) -> list[IterationResult]:
        """跑闭环,返回每轮结果。"""
        n = max_iterations or self.max_iterations
        start = time.monotonic()
        results: list[IterationResult] = []
        consecutive_fail = 0

        self._emit("start", {"goal": goal, "max_iterations": n})

        for i in range(1, n + 1):
            if self._stop:
                self._emit("stop", {"reason": "用户停止", "iteration": i})
                break
            # 暂停(GUI):在迭代之间阻塞,但仍以 0.25s 粒度响应 stop
            if self._pause_event is not None and not self._pause_event.is_set():
                self._emit("paused", {"iteration": i})
                while not self._pause_event.is_set():
                    if self._stop:
                        break
                    self._pause_event.wait(timeout=0.25)
                if self._stop:
                    self._emit("stop", {"reason": "用户停止(暂停中)", "iteration": i})
                    break
                self._emit("resumed", {"iteration": i})
            if time.monotonic() - start > self.timeout_sec:
                self._emit("stop", {"reason": "超时", "iteration": i})
                break

            self._emit("iteration_start", {"iteration": i})
            res = self._run_one_iteration(i, goal)

            # 熔断:连续无 patch 或验证失败
            if not res.patches or res.verified is False:
                consecutive_fail += 1
            else:
                consecutive_fail = 0
            results.append(res)

            # 验证通过则提前结束
            if res.verified is True:
                self._emit("done", {"reason": "验证通过", "iteration": i})
                break
            if consecutive_fail >= MAX_CONSECUTIVE_FAILS:
                self._emit("stop", {"reason": f"连续 {consecutive_fail} 轮无进展,熔断",
                                    "iteration": i})
                break

        self._emit("finish", {"iterations": len(results)})
        return results

    def stop(self) -> None:
        self._stop = True

    # ---------- 单轮 ----------
    def _run_one_iteration(self, iteration: int, goal: str) -> IterationResult:
        res = IterationResult(iteration=iteration)

        # 1) 采集
        lines = self._collect()
        res.log_lines = lines
        self._emit("collected", {"lines": len(lines)})
        if not lines:
            res.note = "本轮无日志"
            self._emit("note", {"note": res.note})
            return res

        # 2) 过滤
        fragment = self.log_filter.extract(lines)
        res.fault_fragment = fragment
        self._emit("filtered", {"fragment_len": len(fragment),
                                "hit": bool(fragment)})
        if not fragment:
            res.verified = True  # 没有故障文本 → 视为已修复
            res.note = "未过滤出 fault,视为已修复"
            self._emit("verified", {"verified": True, "note": res.note})
            return res

        # 3) AI 诊断 + 补丁(内部已取 tokenbase 上下文)
        ai_out = self.ai_client.diagnose_and_patch(
            log_context=fragment, source_context="", goal=goal
        )
        res.diagnosis = ai_out.get("diagnosis", "")
        res.patches = ai_out.get("patches", [])
        meta = ai_out.get("meta", {})
        res.symbols = meta.get("tokenbase_symbols", [])
        res.tokenbase_echo = meta.get("tokenbase_echo", [])
        res.docs_echo = meta.get("docs_echo", [])
        self._emit("ai_done", {
            "diagnosis": res.diagnosis, "patches": len(res.patches),
            "symbols": res.symbols, "tokenbase_echo": res.tokenbase_echo,
            "docs_echo": res.docs_echo,
            "mock": meta.get("mock", False),
        })

        if not res.patches:
            res.note = "AI 未给出补丁"
            self._emit("note", {"note": res.note})
            return res

        # 4) 回写
        ok_n, fail_n = self.coder.apply_many(res.patches)
        res.patches_applied = ok_n
        res.patches_failed = fail_n
        self._emit("coded", {"applied": ok_n, "failed": fail_n,
                             "diff_files": [p["file"] for p in res.patches]})

        if ok_n == 0:
            res.note = "补丁均未命中文件"
            return res

        # 5) 构建(可选)+ 编译自愈(F-08):失败则把编译错误回喂 AI 改码重编,达上限止
        build_ok, build_log = self.builder.build()
        res.build_ok = build_ok
        heal_attempts = 0
        max_heal = self.build_heal_retries if self.auto_build else 0
        while not build_ok and heal_attempts < max_heal:
            heal_attempts += 1
            self._emit("build_heal", {"attempt": heal_attempts, "max": max_heal,
                                      "log_tail": build_log[-300:]})
            heal_out = self.ai_client.diagnose_and_patch(
                log_context=fragment, source_context="", goal=goal,
                build_errors=build_log)
            heal_patches = heal_out.get("patches", [])
            if not heal_patches:
                res.note = f"编译自愈:第 {heal_attempts} 轮 AI 未给补丁,放弃"
                break
            ok_n, _fail_n = self.coder.apply_many(heal_patches)
            if ok_n == 0:
                res.note = f"编译自愈:第 {heal_attempts} 轮补丁未命中,放弃"
                break
            build_ok, build_log = self.builder.build()
            res.build_ok = build_ok
        self._emit("built", {"ok": build_ok, "log_tail": build_log[-400:],
                             "heal_attempts": heal_attempts})
        if not build_ok:
            res.note = (f"构建失败(已编译自愈 {heal_attempts} 轮)" if heal_attempts
                        else "构建失败(auto_build 关闭,未自愈)")
            return res

        # 6) 烧录(人工确认 / dry-run)
        fw = getattr(self.flasher, "firmware", "") or ""
        if not self.auto_flash:
            msg = f"准备烧录(firmware={fw or '未配置'}),是否继续?"
            if not self.confirm_fn(msg):
                res.note = "用户取消烧录"
                res.flashed = False
                self._emit("flash_skipped", {"reason": "用户取消"})
                return res
        if not fw:
            res.flashed = False
            res.note = "未配置 flasher.firmware,跳过烧录(M1 dry-run 常见)"
            self._emit("flash_skipped", {"reason": "无固件路径"})
            return res
        flashed = self.flasher.flash(fw)
        res.flashed = flashed
        self._emit("flashed", {"ok": flashed})
        if not flashed:
            res.note = "烧录未成功(dry-run 或硬件不可用)"
            return res

        # 7) 复采验证(下一轮再采集判断);M1 此处只置"待复采"
        res.verified = None
        res.note = "已烧录,等待下一轮复采验证"
        return res

    # ---------- 采集 ----------
    def _collect(self) -> list[str]:
        """采一轮日志(最多 collect_lines_per_iter 行,或采集到哨兵/超时)。"""
        lines: list[str] = []
        deadline = time.monotonic() + 5.0
        for ln in self.collector.iter_lines():
            lines.append(ln)
            if len(lines) >= self.collect_lines_per_iter:
                break
            if time.monotonic() > deadline:
                break
        return lines

    # ---------- 辅助 ----------
    def _emit(self, stage: str, payload: dict) -> None:
        payload.setdefault("ts", time.time())
        self.on_step(stage, payload)

    @staticmethod
    def _default_confirm(message: str) -> bool:
        """默认交互式确认。非交互环境(GUI/CI)应覆盖 confirm_fn。"""
        try:
            ans = input(f"{message} [y/N]: ").strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")


def make_orchestrator(config: dict, *, dry_run: bool = False,
                      log_file: Optional[str | Path] = None,
                      on_step: Optional[Callable[[str, dict], None]] = None,
                      confirm_fn: Optional[Callable[[str], bool]] = None,
                      on_line: Optional[Callable[[str], None]] = None,
                      pause_event: Optional[threading.Event] = None,
                      ) -> OrchestratorImpl:
    """工厂:用一组 *_make_* 工厂 + config 组装 Orchestrator。"""
    from .ai_client import make_ai_client
    from .builder import make_builder
    from .coder import CoderImpl
    from .collector import make_collector
    from .filter import make_filter
    from .flasher import make_flasher

    project = config.get("project", {}) or {}
    root = project.get("root", ".")

    collector = make_collector(config, on_line=on_line)
    if log_file:
        collector.enable_log_to_file(log_file)
    log_filter = make_filter(config)
    ai_client = make_ai_client(config)
    ai_client.cfg.dry_run = dry_run or ai_client.cfg.dry_run
    coder = CoderImpl(root)
    builder = make_builder(config, dry_run=dry_run)
    flasher = make_flasher(config, dry_run=dry_run)

    loop = config.get("loop", {}) or {}
    return OrchestratorImpl(
        collector=collector, log_filter=log_filter, ai_client=ai_client,
        coder=coder, builder=builder, flasher=flasher,
        max_iterations=int(loop.get("max_iterations", 10)),
        timeout_sec=float(loop.get("timeout_sec", 600.0)),
        auto_flash=bool(loop.get("auto_flash", False)),
        auto_build=bool(loop.get("auto_build", True)),
        build_heal_retries=int(loop.get("build_heal_retries", 2)),
        confirm_fn=confirm_fn, on_step=on_step,
        pause_event=pause_event,
    )
