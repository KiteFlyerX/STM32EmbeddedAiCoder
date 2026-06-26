"""GUI 可复用组件。"""

from .diff_view import DiffView, patch_to_html
from .log_view import LiveLogView, LogHighlighter

__all__ = ["DiffView", "patch_to_html", "LiveLogView", "LogHighlighter"]
