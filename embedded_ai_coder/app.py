"""应用入口:配置日志 → 加载配置 → 创建 Fluent 主窗口 → 进入事件循环。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .config import load_config
from .log_setup import setup_logging
from .main_window import MainWindow


def main() -> int:
    # GUI 无控制台,日志主要落文件(logs/embedded_ai_coder.log)
    log_path = setup_logging(console=False)
    config = load_config()

    app = QApplication(sys.argv)
    app.setApplicationName("EmbeddedAiCoder")
    app.setApplicationDisplayName("EmbeddedAiCoder")

    window = MainWindow(config)
    window.show()
    import logging
    logging.getLogger(__name__).info(
        "GUI 启动;日志文件: %s", log_path)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
