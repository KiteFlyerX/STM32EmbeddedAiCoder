"""
五阶段一键生成编排:ProjectOrchestrator(F-25)。

据「原理图 + 需求 + 用户指定 SDK + 芯片型号」一键生成整个可编译产品工程:
  ① 设计  ai.design_architecture(goal, pinmap, 需求, [原理图视觉]) → 架构 JSON
  ② 骨架  Scaffolder.apply(chip, sdk, hal_modules) → 可编译工程(无 AI)
  ③ 模块  逐个 ai.generate_module(module, arch, pinmap) → 各驱动/应用 .c/.h
  ④ 集成  ai.integrate_main(arch, module_apis) → main.c + state_machine.c/.h
  ⑤ 自愈  build_with_heal(builder, ai, coder) → make 编译,失败回喂 AI 改码重编

每阶段经 on_step(stage="proj_*", payload) 上报 GUI 进度。复用 AiClientImpl /
CoderImpl / Scaffolder / build_with_heal,不重写。异常时 coder.rollback_all() 还原。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from .ai_client import AiClientImpl
from .builder import Builder
from .coder import CoderImpl
from .scaffolder import Scaffolder

logger = logging.getLogger(__name__)


class ProjectOrchestrator:
    """五阶段一键生成整工程编排器。"""

    def __init__(
        self,
        ai_client: AiClientImpl,
        coder: CoderImpl,
        scaffolder: Scaffolder,
        builder: Builder,
        *,
        max_heal_retries: int = 2,
        on_step: Optional[Callable[[str, dict], None]] = None,
        config: Optional[dict] = None,
    ):
        self.ai_client = ai_client
        self.coder = coder
        self.scaffolder = scaffolder
        self.builder = builder
        self.max_heal_retries = max_heal_retries
        self.on_step = on_step or (lambda stage, payload: None)
        self.config = config or {}

    # ---------- 主入口 ----------
    def implement_project(
        self,
        *,
        goal: str,
        chip: str,
        pinmap_text: Optional[str] = None,
        modules_hint: str = "",
        project_root: str = "",
        sdk_root: str = "",
    ) -> dict:
        """一键五阶段生成。返回汇总 dict。"""
        try:
            return self._run(goal, chip, pinmap_text, modules_hint, project_root, sdk_root)
        except Exception as exc:  # noqa: BLE001
            logger.exception("一键生成整项目异常")
            rolled = 0
            try:
                rolled = self.coder.rollback_all()
            except Exception:  # noqa: BLE001
                pass
            self._emit("proj_error", {"error": str(exc), "rolled_back": rolled})
            return {"summary": f"(异常:{exc})", "files": [], "build_ok": False,
                    "note": f"异常,已回滚 {rolled} 项", "error": str(exc),
                    "failed_modules": [], "heal_attempts": 0, "mock": False}

    def _run(self, goal, chip, pinmap_text, modules_hint, project_root, sdk_root) -> dict:
        self._emit("proj_start", {"goal": goal, "chip": chip,
                                  "project_root": project_root, "sdk_root": sdk_root})
        if not chip:
            return self._result(summary="未配置芯片型号(project.chip)",
                                note="停止:scaffold 需要芯片型号")

        # pinmap 优先用传入;否则从 docs 预读器取
        if pinmap_text is None:
            pinmap_text = self._read_pinmap()

        # ① 架构设计
        self._emit("proj_design", {"status": "start"})
        docs_text = self._gather_docs_text()
        arch = self.ai_client.design_architecture(
            goal=goal, pinmap_text=pinmap_text or "", modules_hint=modules_hint,
            docs_text=docs_text, use_vision=True)
        modules = arch.get("modules") or []
        self._emit("proj_design", {
            "status": "done",
            "modules": [m.get("name") for m in modules],
            "hal_modules": arch.get("hal_modules", []),
            "mock": bool(arch.get("meta", {}).get("mock")),
        })
        if not modules:
            return self._result(summary=arch.get("summary", "架构设计未给出模块"),
                                arch=arch, note="停止:架构设计未产出模块")

        # ② scaffold 可编译骨架
        self._emit("proj_scaffold", {"status": "start", "chip": chip})
        plan = self.scaffolder.plan(chip, hal_modules=arch.get("hal_modules"))
        sc_out = self.scaffolder.apply(plan, self.coder)
        self._emit("proj_scaffold", {"status": "done", "written": sc_out["written"],
                                     "copied": sc_out["copied"], "notes": sc_out["notes"]})
        self._ensure_make_command()   # scaffold 后置 build_command=make 并重建 builder

        # ③ 逐模块生成(按 tasks 顺序)
        all_files: list[str] = []
        module_apis: list[dict] = []
        failed: list[str] = []
        for m in self._ordered_modules(modules, arch.get("tasks") or []):
            name = m.get("name") or "?"
            self._emit(f"proj_module_{name}", {"status": "start"})
            out = self.ai_client.generate_module(m, arch, pinmap_text or "")
            files = out.get("files") or []
            if files:
                written, _skipped = self.coder.write_files(files)
                paths = [f["path"] for f in files]
                all_files.extend(paths)
                module_apis.append({"name": name, "api": m.get("api", []), "files": paths})
                self._emit(f"proj_module_{name}",
                           {"status": "done", "files": len(files), "written": written})
            else:
                failed.append(name)
                self._emit(f"proj_module_{name}", {
                    "status": "failed",
                    "error": (out.get("meta") or {}).get("error", "未产出文件")})

        # ④ 主循环 + 状态机集成
        self._emit("proj_integrate", {"status": "start"})
        intg = self.ai_client.integrate_main(arch, module_apis)
        ifiles = intg.get("files") or []
        if ifiles:
            written, _skipped = self.coder.write_files(ifiles)
            all_files.extend(f["path"] for f in ifiles)
        self._emit("proj_integrate", {"status": "done", "files": len(ifiles),
                                      "written": written if ifiles else 0})

        # ⑤ 编译自愈
        self._emit("proj_build_heal", {"status": "start"})
        from .build_heal import build_with_heal
        build_ok, build_log, heal = build_with_heal(
            self.builder, self.ai_client, self.coder, goal,
            max_retries=self.max_heal_retries, fragment="", on_step=self._emit)
        self._emit("proj_build_heal", {"status": "done", "build_ok": build_ok,
                                       "heal_attempts": heal,
                                       "log_tail": (build_log or "")[-400:]})

        note = (f"生成 {len(all_files)} 个文件;模块失败 {failed or '无'};"
                f"编译 {'通过' if build_ok else f'失败(自愈 {heal} 轮)'}")
        return self._result(summary=arch.get("summary", ""), arch=arch,
                            files=all_files, build_ok=build_ok, build_log=build_log,
                            heal=heal, failed_modules=failed, note=note)

    # ---------- 辅助 ----------
    def _emit(self, stage: str, payload: dict) -> None:
        payload.setdefault("ts", time.time())
        self.on_step(stage, payload)

    def _read_pinmap(self) -> str:
        docs = getattr(self.ai_client, "_docs", None)
        if docs is None:
            return ""
        try:
            return docs._read_pinmap_text()
        except Exception:  # noqa: BLE001
            return ""

    def _gather_docs_text(self) -> str:
        """取需求/原理图文本块(design_architecture 的 docs_text)。"""
        docs = getattr(self.ai_client, "_docs", None)
        if docs is None:
            return ""
        try:
            items, _echo = docs.read_all()
            return docs.render_text_block(items)
        except Exception:  # noqa: BLE001
            return ""

    def _ensure_make_command(self) -> None:
        """scaffold 后:把 build_command 置为 make(若空)并重建 builder,以复用编译自愈。"""
        proj = self.config.setdefault("project", {})
        if not (proj.get("build_command") or "").strip():
            proj["build_command"] = "make"
            from .builder import make_builder
            self.builder = make_builder(self.config)
            logger.info("scaffold 后置 build_command=make 并重建 builder")

    @staticmethod
    def _ordered_modules(modules: list[dict], tasks: list[dict]) -> list[dict]:
        """按 tasks[].order 排序;无 tasks 则原序。"""
        order_map = {t.get("module"): t.get("order", 99) for t in tasks if t.get("module")}
        return sorted(modules, key=lambda m: order_map.get(m.get("name"), 99))

    def _result(self, *, summary, arch=None, files=None, build_ok=False,
                build_log="", heal=0, failed_modules=None, note="", error="") -> dict:
        meta = (arch or {}).get("meta", {}) if arch else {}
        return {
            "summary": summary, "files": files or [], "build_ok": build_ok,
            "build_log": (build_log or "")[-1000:], "heal_attempts": heal,
            "failed_modules": failed_modules or [], "note": note, "error": error,
            "mock": bool(meta.get("mock")),
        }


def make_project_orchestrator(
    config: dict, *, on_step: Optional[Callable[[str, dict], None]] = None,
) -> ProjectOrchestrator:
    """工厂:用 config 组装 ProjectOrchestrator(复用各 make_* 工厂)。"""
    from .ai_client import make_ai_client
    from .builder import make_builder
    from .coder import CoderImpl
    from .scaffolder import make_scaffolder

    proj = config.get("project", {}) or {}
    root = proj.get("root", ".")
    loop = config.get("loop", {}) or {}
    return ProjectOrchestrator(
        ai_client=make_ai_client(config),
        coder=CoderImpl(root),
        scaffolder=make_scaffolder(config),
        builder=make_builder(config),
        max_heal_retries=int(loop.get("build_heal_retries", 2)),
        on_step=on_step,
        config=config,
    )
