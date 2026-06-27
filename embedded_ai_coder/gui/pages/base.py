"""页面基类:统一的标题 + 说明 + 占位内容区,减少重复代码。

子类只需调用 super().__init__(title, subtitle),objectName 自动取子类名
(供 FluentWindow 导航唯一识别);如需自定义内容,覆写 _build_content()。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, CardWidget, SubtitleLabel, TitleLabel


class PlaceholderPage(QWidget):
    """通用占位页面基类。"""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName(self.__class__.__name__)

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(36, 32, 36, 36)
        self.vbox.setSpacing(8)

        self.titleLabel = TitleLabel(title, self)
        self.subtitleLabel = BodyLabel(subtitle, self)
        self.subtitleLabel.setWordWrap(True)

        self.vbox.addWidget(self.titleLabel)
        self.vbox.addWidget(self.subtitleLabel)
        self.vbox.addSpacing(16)

        self.contentLayout = QVBoxLayout()
        self._build_content(self.contentLayout)
        # 内容区 stretch=1:随窗口变大填满页面(日志视图/源码树/diff 等会跟着撑开)
        self.vbox.addLayout(self.contentLayout, 1)

    def _build_content(self, layout: QVBoxLayout) -> None:
        """默认占位卡片;子类可覆写以自定义内容区。"""
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(SubtitleLabel("🚧 开发中", card))
        card_layout.addWidget(
            BodyLabel("该页面将在对应里程碑实现,详见需求文档第十节。", card)
        )
        layout.addWidget(card)
