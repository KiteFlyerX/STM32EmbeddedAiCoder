"""① 控制台:连接状态卡片 + 启停闭环 + 迭代进度 + 实时日志 / AI 任务占位。"""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ElevatedCardWidget,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)


class StatusCard(ElevatedCardWidget):
    """状态卡片:标题 + 当前值。"""

    def __init__(self, title: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setFixedHeight(98)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)
        layout.addWidget(BodyLabel(title, self))
        self.valueLabel = StrongBodyLabel(value, self)
        layout.addWidget(self.valueLabel)

    def setValue(self, text: str) -> None:
        self.valueLabel.setText(text)


class DashboardPage(QWidget):
    """控制台驾驶舱。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashboardPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 36)
        root.setSpacing(12)

        root.addWidget(TitleLabel("控制台", self))
        root.addWidget(
            BodyLabel("总览连接状态,启停自动化闭环,观察实时日志与 AI 任务。", self)
        )

        # 状态卡片
        self.cardSerial = StatusCard("串口", "未连接", self)
        self.cardProbe = StatusCard("调试器", "—", self)
        self.cardBoard = StatusCard("目标板", "—", self)
        self.cardIter = StatusCard("迭代轮次", "0 / 10", self)
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, card in enumerate(
            [self.cardSerial, self.cardProbe, self.cardBoard, self.cardIter]
        ):
            grid.addWidget(card, 0, i)
        root.addLayout(grid)

        # 操作栏(引擎接入前暂停/停止不可用)
        bar = QHBoxLayout()
        self.btnStart = PrimaryPushButton("启动闭环", self)
        self.btnPause = PushButton("暂停", self)
        self.btnStop = PushButton("停止", self)
        self.btnPause.setEnabled(False)
        self.btnStop.setEnabled(False)
        bar.addWidget(self.btnStart)
        bar.addWidget(self.btnPause)
        bar.addWidget(self.btnStop)
        bar.addStretch(1)
        root.addLayout(bar)

        # 迭代进度
        self.progress = ProgressBar(self)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # 实时日志占位
        logCard = ElevatedCardWidget(self)
        logLayout = QVBoxLayout(logCard)
        logLayout.addWidget(SubtitleLabel("实时日志", logCard))
        logLayout.addWidget(
            BodyLabel("采集启动后,此处展示串口 / RTT 实时输出(M1 实现)。", logCard)
        )
        root.addWidget(logCard, 1)

        # 当前 AI 任务占位
        aiCard = ElevatedCardWidget(self)
        aiLayout = QVBoxLayout(aiCard)
        aiLayout.addWidget(SubtitleLabel("当前 AI 任务", aiCard))
        aiLayout.addWidget(
            BodyLabel("闭环运行时,此处展示 AI 正在分析的问题与生成的修改(M1 实现)。", aiCard)
        )
        root.addWidget(aiCard)

        root.addStretch(1)
