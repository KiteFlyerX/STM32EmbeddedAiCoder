"""配置加载:config/default.yaml(底) + 可选 config/local.yaml(覆盖)。

local.yaml 用于存放个人配置与密钥,默认被 .gitignore 忽略。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "default.yaml"
LOCAL_CONFIG = CONFIG_DIR / "local.yaml"


def load_config() -> dict[str, Any]:
    """加载并合并配置。default 为基础,local 覆盖同名键。"""
    data: dict[str, Any] = {}
    for path in (DEFAULT_CONFIG, LOCAL_CONFIG):
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            data = _deep_merge(data, loaded)
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并:override 优先。"""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def save_local_overlay(overlay: dict[str, Any]) -> dict[str, Any]:
    """把 overlay 深合并写入 config/local.yaml(不存在则创建),返回合并后的完整 local 配置。

    供设置页 / 工程页持久化用户改动;密钥等敏感字段只落 local.yaml(已 gitignore)。
    返回值便于调用方刷新内存中的 config。
    """
    existing: dict[str, Any] = {}
    if LOCAL_CONFIG.exists():
        try:
            with LOCAL_CONFIG.open("r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}
    merged = _deep_merge(existing, overlay)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with LOCAL_CONFIG.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    return merged
