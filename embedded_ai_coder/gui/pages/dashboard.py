"""① 控制台:连接状态卡片 + 启停闭环 + 迭代进度 + 实时日志 / AI 任务。

接 EngineHub:启动闭环(演示/真实)、暂停/恢复/停止,实时日志流,
AI 任务(诊断 + 补丁数 + mock 标记)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ElevatedCardWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
)

from ..widgets import LiveLogView

_MAX_DEFAULT = 10


class StatusCard(ElevatedCardWidget):
    """状态卡片:标题 + 当前值。"""

    def __init__(self, title: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setFixedHeight(96)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)
        layout.addWidget(BodyLabel(title, self))
        self.valueLabel = StrongBodyLabel(value, self)
        self.valueLabel.setWordWrap(True)
        layout.addWidget(self.valueLabel)

    def setValue(self, text: str) -> None:
        self.valueLabel.setText(text)


class DashboardPage(QWidget):
    """控制台驾驶舱。"""

    def __init__(self, hub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.setObjectName("DashboardPage")
        self._state = "idle"
        self._max_iter = int((hub.config.get("loop", {}) or {}).get("max_iterations", _MAX_DEFAULT))

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 36)
        root.setSpacing(12)

        root.addWidget(TitleLabel("控制台", self))
        root.addWidget(BodyLabel("总览连接状态,启停自动化闭环,观察实时日志与 AI 任务。", self))

        # 操作栏:目标 + 演示开关 + 最大轮数 + 启停
        bar = QHBoxLayout()
        bar.addWidget(BodyLabel("目标:", self))
        self.goalEdit = LineEdit(self)
        self.goalEdit.setPlaceholderText("本轮目标,如:修复 HardFault / 实现 UART DMA 接收")
        self.goalEdit.setText(hub.config.get("goal", "") or "")
        bar.addWidget(self.goalEdit, 3)

        bar.addWidget(BodyLabel("演示模式:", self))
        self.demoSwitch = SwitchButton(self)
        self.demoSwitch.setChecked(True)  # 默认演示:无硬件/key 也能跑
        bar.addWidget(self.demoSwitch)

        bar.addWidget(BodyLabel("最大轮数:", self))
        self.maxSpin = SpinBox(self)
        self.maxSpin.setRange(1, 100)
        self.maxSpin.setValue(self._max_iter)
        bar.addWidget(self.maxSpin)

        self.btnStart = PrimaryPushButton("▶ 启动闭环", self)
        self.btnPause = PushButton("⏸ 暂停", self)
        self.btnStop = PushButton("⏹ 停止", self)
        self.btnPause.setEnabled(False)
        self.btnStop.setEnabled(False)
        bar.addWidget(self.btnStart)
        bar.addWidget(self.btnPause)
        bar.addWidget(self.btnStop)
        root.addLayout(bar)

        # 状态卡片
        self.cardSerial = StatusCard("串口", "未连接", self)
        self.cardProbe = StatusCard("调试器", self._probe_text(), self)
        self.cardBoard = StatusCard("目标板", self._board_text(), self)
        self.cardIter = StatusCard("迭代轮次", f"0 / {self._max_iter}", self)
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, card in enumerate([self.cardSerial, self.cardProbe, self.cardBoard, self.cardIter]):
            grid.addWidget(card, 0, i)
        root.addLayout(grid)

        # 迭代进度
        self.progress = ProgressBar(self)
        self.progress.setRange(0, self._max_iter)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # 实时日志
        logCard = ElevatedCardWidget(self)
        logLayout = QVBoxLayout(logCard)
        logLayout.setContentsMargins(16, 12, 16, 16)
        logLayout.addWidget(SubtitleLabel("实时日志(AI 工作过程)", logCard))
        self.liveLog = LiveLogView(logCard)
        logLayout.addWidget(self.liveLog, 1)
        root.addWidget(logCard, 3)

        # 当前 AI 任务
        aiCard = ElevatedCardWidget(self)
        aiLayout = QVBoxLayout(aiCard)
        aiLayout.setContentsMargins(16, 12, 16, 16)
        aiLayout.addWidget(SubtitleLabel("当前 AI 任务", aiCard))
        self.aiLabel = BodyLabel("尚未启动闭环。点击「启动闭环」开始(演示模式下用预制日志与 mock 补丁跑通)。", aiCard)
        self.aiLabel.setWordWrap(True)
        self.aiLabel.setTextFormat(Qt.TextFormat.RichText)
        aiLayout.addWidget(self.aiLabel)
        aiCard.setFixedHeight(110)
        root.addWidget(aiCard)

        # 信号绑定
        self.btnStart.clicked.connect(self._on_start)
        self.btnPause.clicked.connect(self._on_pause)
        self.btnStop.clicked.connect(self._on_stop)
        self.maxSpin.valueChanged.connect(lambda v: self._on_max_changed(v))

        hub.progress.connect(self._on_progress)
        hub.stateChanged.connect(self._on_state)
        hub.error.connect(self._on_error)

    # ---------- 探测文本 ----------
    def _probe_text(self) -> str:
        probe = (self.hub.config.get("debugger", {}) or {}).get("probe", "stlink")
        return {"stlink": "ST-Link", "jlink": "J-Link", "cmsis-dap": "CMSIS-DAP"}.get(probe, probe)

    def _board_text(self) -> str:
        chip = (self.hub.config.get("project", {}) or {}).get("chip", "")
        return chip or "—"

    # ---------- 控制 ----------
    def _on_start(self) -> None:
        goal = self.goalEdit.text().strip() or "修复串口日志中的 fault"
        self._max_iter = self.maxSpin.value()
        self.hub.config.setdefault("loop", {})["max_iterations"] = self._max_iter
        self.hub.config["goal"] = goal
        self.cardIter.setValue(f"0 / {self._max_iter}")
        self.progress.setRange(0, self._max_iter)
        self.progress.setValue(0)
        self.liveLog.clear_log()
        self.cardSerial.setValue("采集中…")
        self.hub.start_loop(goal, self._max_iter, self.demoSwitch.isChecked())

    def _on_pause(self) -> None:
        if self._state == "running":
            self.hub.pause()
        elif self._state == "paused":
            self.hub.resume()

    def _on_stop(self) -> None:
        self.hub.stop()

    def _on_max_changed(self, value: int) -> None:
        self._max_iter = value
        if self._state == "idle":
            self.cardIter.setValue(f"0 / {value}")
            self.progress.setRange(0, value)

    # ---------- 信号回调(均已在 GUI 线程)----------
    def _on_state(self, state: str) -> None:
        self._state = state
        running = state in ("running", "paused", "stopping")
        self.btnStart.setEnabled(state in ("idle", "done", "error"))
        self.btnStop.setEnabled(running)
        self.btnPause.setEnabled(state in ("running", "paused"))
        self.btnPause.setText("▶ 继续" if state == "paused" else "⏸ 暂停")
        if state in ("idle", "done", "error"):
            self.cardSerial.setValue("已断开" if state != "idle" else "未连接")
        if state == "done":
            self._flash_info("闭环结束", "所有迭代已完成。", InfoBarPosition.TOP_RIGHT)

    def _fmt_stage(self, stage: str, payload: dict) -> str | None:
        """把一个 progress 事件格式化成实时日志一行;返回 None 表示不记录。"""
        from datetime import datetime
        t = datetime.now().strftime("%H:%M:%S")

        def ln(s: str) -> str:
            return f"[{t}] {s}"

        # ---- M1 闭环 ----
        if stage == "iteration_start":
            return ln(f"▶ 轮次 {payload.get('iteration', 0)} 开始")
        if stage == "collected":
            return ln(f"   采集 {payload.get('lines', 0)} 行串口日志")
        if stage == "filtered":
            return ln(f"   过滤 fault:{'命中' if payload.get('hit') else '未命中'}"
                      f"({payload.get('fragment_len', 0)} 字符)")
        if stage == "ai_done":
            diag = (payload.get("diagnosis") or "—").strip().splitlines()[0][:80]
            mock = " [mock]" if payload.get("mock") else ""
            return ln(f"   AI 诊断:{diag}{mock}(补丁 {payload.get('patches', 0)})")
        if stage == "coded":
            return ln(f"   回写:应用 {payload.get('applied', 0)} / 失败 {payload.get('failed', 0)}")
        if stage == "built":
            return ln("   编译 ✓ 通过" if payload.get("ok") else "   编译 ✗ 失败")
        if stage == "build_heal":
            return ln(f"   编译失败,自愈第 {payload.get('attempt')}/{payload.get('max')} 轮")
        if stage == "flashed":
            return ln("   烧录 ✓" if payload.get("ok") else "   烧录 ✗/跳过")
        if stage == "verified":
            ok = payload.get("verified")
            return ln(f"   验证:{'✓ fault 消失' if ok is True else '✗ 仍存在' if ok is False else '待复采'}")
        if stage == "stop":
            return ln(f"■ 停止:{payload.get('reason', '')}")
        if stage == "finish":
            return ln(f"■ 结束(共 {payload.get('iterations', '')} 轮)")
        # ---- F-25 一键生成整项目 ----
        if stage == "proj_start":
            return ln(f"🏗 一键生成整项目:{payload.get('goal', '')}")
        if stage == "proj_design":
            mods = payload.get("modules") or []
            return ln(f"   ① 架构设计:{payload.get('status', '')}"
                      + (f" → 模块 {', '.join(mods)}" if mods else ""))
        if stage == "proj_scaffold":
            return ln(f"   ② 生成工程骨架:{payload.get('status', '')} "
                      f"写入 {payload.get('written', 0)} 文件")
        if stage.startswith("proj_module_"):
            name = stage.split("proj_module_", 1)[1]
            st = payload.get("status", "")
            extra = (f" {payload.get('files', '')} 文件" if st == "done"
                     else f" {payload.get('error', '')}" if st == "failed" else "")
            return ln(f"   ③ 生成模块 {name}:{st}{extra}")
        if stage == "proj_integrate":
            return ln(f"   ④ 主循环+状态机集成:{payload.get('status', '')} "
                      f"写入 {payload.get('written', 0)} 文件")
        if stage == "proj_build_heal":
            if payload.get("status") == "done":
                ok = payload.get("build_ok")
                heal = payload.get("heal_attempts", 0)
                return ln(f"   ⑤ 编译:{'✓通过' if ok else '✗失败(自愈 ' + str(heal) + ' 轮)'}")
            return ln("   ⑤ 编译自愈中…")
        if stage == "proj_error":
            return ln(f"   ✗ 异常:{payload.get('error', '')}  已回滚 {payload.get('rolled_back', 0)} 项")
        # ---- implement_and_deploy ----
        if stage == "impl_start":
            return ln("🚀 实现并部署:开始")
        if stage == "impl_written":
            return ln(f"   生成 {payload.get('files', 0)} 文件,写入 {payload.get('written', 0)}")
        return None

    def _on_progress(self, stage: str, payload: dict) -> None:
        txt = self._fmt_stage(stage, payload)
        if txt:
            self.liveLog.append_line(txt)
        if stage == "iteration_start":
            i = payload.get("iteration", 0)
            self.cardIter.setValue(f"{i} / {self._max_iter}")
            self.progress.setValue(i - 1)
        elif stage == "collected":
            self.cardSerial.setValue(f"采集中 · {payload.get('lines', 0)} 行")
            self.progress.setValue(self.progress.value() if self.progress.value() else 0)
        elif stage == "ai_done":
            diag = payload.get("diagnosis", "") or "—"
            n = payload.get("patches", 0)
            mock = payload.get("mock", False)
            tag = '<span style="color:#C77F00">[mock]</span> ' if mock else ""
            syms = payload.get("symbols", []) or []
            sym_txt = ("抽取符号:" + ", ".join(syms)) if syms else ""
            self.aiLabel.setText(
                f"{tag}{diag}<br><b>补丁数:{n}</b>　{sym_txt}"
            )
        elif stage == "coded":
            ap = payload.get("applied", 0)
            fl = payload.get("failed", 0)
            self.aiLabel.setText(self.aiLabel.text().split("<br>")[0]
                                 + f"<br><b>已回写:应用 {ap} 处,失败 {fl} 处</b>")
        elif stage == "build_heal":
            self.aiLabel.setText(
                f"⟳ 编译失败,回喂 AI 自愈(第 {payload.get('attempt')}/{payload.get('max')} 轮)…")
        elif stage == "built":
            if payload.get("ok"):
                heal = payload.get("heal_attempts", 0)
                self.aiLabel.setText(f"✓ 编译通过" + (f"(经 {heal} 轮自愈)" if heal else ""))
            else:
                self.aiLabel.setText("✗ 编译失败(自愈未通过,见日志)")
        elif stage == "verified":
            ok = payload.get("verified")
            if ok is True:
                self._flash_info("验证通过", payload.get("note", "fault 已消失"), InfoBarPosition.TOP_RIGHT)

    def _on_error(self, message: str) -> None:
        self._flash_info("引擎错误", message, InfoBarPosition.BOTTOM_RIGHT, is_error=True)

    def _flash_info(self, title, content, position, is_error=False) -> None:
        method = InfoBar.error if is_error else InfoBar.success
        try:
            method(title, content, parent=self.window(), duration=4000, position=position)
        except Exception:  # noqa: BLE001
            pass
