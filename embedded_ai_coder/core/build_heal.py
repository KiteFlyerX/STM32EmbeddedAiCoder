"""编译自愈共享函数(F-25 抽取)。

把「构建 + 编译失败回喂 AI 改码重编」从 OrchestratorImpl._build_with_heal 抽成纯函数,
供 M1 闭环与 F-25 五阶段一键生成编排**共用**(消除重复)。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def build_with_heal(
    builder: Any,
    ai_client: Any,
    coder: Any,
    goal: str,
    *,
    max_retries: int = 2,
    fragment: str = "",
    on_step: Optional[Callable[[str, dict], None]] = None,
) -> tuple[bool, str, int]:
    """构建 + 编译自愈(失败回喂 AI 改码重编)。

    - builder.build() -> (ok, log);失败时把编译日志回喂 ai_client.diagnose_and_patch,
      用 coder.apply_many 落补丁后重编,最多 max_retries 轮。
    - fragment:diagnose_and_patch 的 log_context(M1 闭环传 fault 片段;五阶段传 "")。
    - on_step(stage, payload):每轮自愈上报(可选)。
    返回 (build_ok, build_log, attempts)。
    """
    emit = on_step or (lambda stage, payload: None)
    build_ok, build_log = builder.build()
    attempts = 0
    while not build_ok and attempts < max_retries:
        attempts += 1
        emit("build_heal", {"attempt": attempts, "max": max_retries,
                            "log_tail": (build_log or "")[-300:]})
        heal_out = ai_client.diagnose_and_patch(
            log_context=fragment, source_context="", goal=goal,
            build_errors=build_log or "")
        patches = heal_out.get("patches", []) or []
        if not patches:
            logger.warning("编译自愈:第 %d 轮 AI 未给补丁,放弃", attempts)
            break
        ok_n, _fail = coder.apply_many(patches)
        if ok_n == 0:
            logger.warning("编译自愈:第 %d 轮补丁未命中,放弃", attempts)
            break
        build_ok, build_log = builder.build()
    return build_ok, build_log or "", attempts
