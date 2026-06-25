"""应用入口:加载配置 → 创建 Fluent 主窗口 → 进入事件循环。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .config import load_config
from .main_window import MainWindow


def main() -> int:
    config = load_config()

    app = QApplication(sys.argv)
    app.setApplicationName("EmbeddedAiCoder")
    app.setApplicationDisplayName("EmbeddedAiCoder")

    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
