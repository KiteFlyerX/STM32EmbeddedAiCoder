"""支持 `python -m embedded_ai_coder [...]` 启动。

无子命令(或 --gui) → 启动 Fluent GUI(app.main);
带子命令(run/collect/index) → 走 M1 CLI(cli.main)。
"""

from __future__ import annotations

import sys


def _looks_like_cli_subcommand(argv: list[str]) -> bool:
    return bool(argv) and argv[0] in {"run", "collect", "index"}


def main() -> int:
    argv = sys.argv[1:]
    # 显式 --gui 或无子命令 → GUI;否则 CLI
    if "--gui" in argv:
        argv = [a for a in argv if a != "--gui"]
        if argv:
            sys.argv[1:] = argv
        from .app import main as gui_main
        return gui_main()
    if _looks_like_cli_subcommand(argv):
        from .cli import main as cli_main
        return cli_main(argv)
    # 默认起 GUI
    from .app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
