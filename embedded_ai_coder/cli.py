"""
命令行入口:embedded-ai-coder run|collect|index(M1)。

- run    :跑闭环(采集 → 过滤 → AI(含 tokenbase 上下文)→ 回写 → 构建 → 烧录 → 复采)
- collect:只采集看日志
- index  :对工程目录跑 tokenbase index(建上下文库)

入口在 __main__.py 分发:GUI(无参)走 app.main;带子命令走 cli.main。
``embedded-ai-coder`` console_script 指向 app.main(GUI 默认);为兼容,
也可直接 ``python -m embedded_ai_coder run --dry-run``。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config


def _setup_logging(verbose: bool) -> None:
    """配置日志:控制台 + 滚动日志文件(共用 log_setup)。"""
    from .log_setup import setup_logging
    setup_logging(verbose=verbose, console=True)


def _make_step_printer():
    """构造 on_step 回调:把每步事件打印成可读文本。"""
    def _print(stage: str, payload: dict) -> None:
        if stage == "iteration_start":
            print(f"\n========== 第 {payload.get('iteration')} 轮 ==========")
        elif stage == "collected":
            print(f"[1/7 采集]   共 {payload.get('lines', 0)} 行日志")
        elif stage == "filtered":
            hit = payload.get("hit")
            print(f"[2/7 过滤]   fault 片段命中={'是' if hit else '否'}"
                  f"(长度 {payload.get('fragment_len', 0)})")
        elif stage == "ai_done":
            mock = payload.get("mock")
            print(f"[3/7 AI]    {'(mock)' if mock else ''} 诊断:")
            print(f"           诊断:{payload.get('diagnosis', '')}")
            print(f"           补丁数:{payload.get('patches', 0)}")
            syms = payload.get("symbols", [])
            if syms:
                print(f"           tokenbase 抽取符号:{syms}")
            for echo in payload.get("tokenbase_echo", []):
                print(f"             $ {echo}")
            docs_echo = payload.get("docs_echo", [])
            if docs_echo:
                print("           预读文档:")
                for echo in docs_echo:
                    print(f"             · {echo}")
        elif stage == "coded":
            print(f"[4/7 回写]   应用 {payload.get('applied', 0)} 处,"
                  f"失败 {payload.get('failed', 0)} 处;"
                  f"文件:{payload.get('diff_files', [])}")
        elif stage == "build_heal":
            print(f"[5/7 自愈]   编译失败,回喂 AI 自愈(第 {payload.get('attempt')}/"
                  f"{payload.get('max')} 轮)")
        elif stage == "built":
            heal = payload.get("heal_attempts", 0)
            extra = f"(经 {heal} 轮编译自愈)" if heal else ""
            print(f"[5/7 构建]   ok={payload.get('ok')} {extra}")
        elif stage == "flash_skipped":
            print(f"[6/7 烧录]   跳过:{payload.get('reason', '')}")
        elif stage == "flashed":
            print(f"[6/7 烧录]   ok={payload.get('ok')}")
        elif stage == "verified":
            print(f"[7/7 验证]   verified={payload.get('verified')}")
        elif stage == "stop":
            print(f"\n[停止] {payload.get('reason', '')}(轮次 {payload.get('iteration', '?')})")
        elif stage == "done":
            print(f"\n[完成] {payload.get('reason', '')}(轮次 {payload.get('iteration', '?')})")
        elif stage == "finish":
            print(f"\n========== 闭环结束,共 {payload.get('iterations', 0)} 轮 ==========")
        elif stage == "note":
            print(f"           备注:{payload.get('note', '')}")
    return _print


# ---------- 子命令 ----------
def cmd_run(args: argparse.Namespace) -> int:
    """跑闭环。"""
    from .core.orchestrator import make_orchestrator

    config = load_config()
    # CLI 覆盖:goal / max-iter / dry-run / demo log 回放
    if args.goal:
        config.setdefault("goal", args.goal)
    if args.demo_log:
        # 把采集器切到「文件回放」模式(不依赖硬件)
        config.setdefault("collector", {})
        config["collector"].setdefault("serial", {})
    if args.project:
        config.setdefault("project", {})
        config["project"]["root"] = str(Path(args.project).resolve())
        # AiClient 的 tokenbase_dir 也指向工程目录
        config.setdefault("ai", {})
        config["ai"]["tokenbase_dir"] = str(Path(args.project).resolve())
    max_iter = args.max_iter or (config.get("loop", {}) or {}).get("max_iterations", 10)

    on_step = _make_step_printer()
    confirm_fn = (lambda msg: False) if args.dry_run else None  # dry-run 自动取消烧录

    orch = make_orchestrator(
        config, dry_run=args.dry_run,
        on_step=on_step, confirm_fn=confirm_fn,
    )

    # demo 模式:用文件回放代替真实串口
    if args.demo_log:
        from .core.collector import SerialCollector
        orch.collector = SerialCollector(
            port="(replay)", baudrate=0, from_file=args.demo_log
        )

    print(f"目标:{args.goal or '(默认:修复 fault)'}  最大轮数:{max_iter}  "
          f"dry-run={args.dry_run}")
    print(f"工程目录:{(config.get('project', {}) or {}).get('root', '(未配置)')}")

    # 闭环前环境自检:缺关键工具(arm-gcc/pyOCD)写日志,不阻断(演示模式可继续)
    from .preflight import log_warnings
    log_warnings()

    orch.collector.open()
    try:
        results = orch.run(goal=args.goal or "修复串口日志中的 fault", max_iterations=max_iter)
    finally:
        orch.collector.close()

    # 汇总
    print("\n---------- 汇总 ----------")
    for r in results:
        print(f"  轮 {r.iteration}: fault={'有' if r.fault_fragment else '无'}, "
              f"补丁={len(r.patches)}, 应用={r.patches_applied}, "
              f"烧录={r.flashed}, verified={r.verified}")
    if args.json:
        print(json.dumps(
            [{"iteration": r.iteration, "diagnosis": r.diagnosis,
              "patches": r.patches, "note": r.note,
              "tokenbase_symbols": r.symbols} for r in results],
            ensure_ascii=False, indent=2,
        ))
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    """只采集看日志。"""
    from .core.collector import SerialCollector

    config = load_config()
    serial_cfg = (config.get("collector") or {}).get("serial", {}) or {}
    if args.demo_log:
        coll = SerialCollector(port="(replay)", baudrate=0, from_file=args.demo_log)
    else:
        coll = SerialCollector(
            port=args.port or serial_cfg.get("port", "COM3"),
            baudrate=serial_cfg.get("baudrate", 115200),
        )
    print(f"采集:{coll.port} @ {coll.baudrate}(Ctrl+C 停止)")
    coll.open()
    try:
        for line in coll.iter_lines():
            print(line)
    except KeyboardInterrupt:
        print("\n(停止采集)")
    finally:
        coll.close()
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """对工程目录跑 tokenbase index(建上下文库)。"""
    from .core import tokenbase_bridge

    ok, out = tokenbase_bridge.index(args.directory, force=args.force)
    print(out)
    return 0 if ok else 2


def cmd_check_env(args: argparse.Namespace) -> int:
    """环境自检:检测构建/烧录/定位工具链是否就绪,并给 Windows 安装提示。"""
    from .preflight import check_env, format_report, log_warnings
    checks = check_env()
    print(format_report(checks))
    log_warnings(checks)
    missing = [c.name for c in checks if c.critical and not c.found]
    return 2 if missing else 0


def cmd_implement(args: argparse.Namespace) -> int:
    """据预读的原理图 + 需求文档,调用配置的 AI 实现产品固件并写回工程。"""
    from .core.ai_client import make_ai_client
    from .core.coder import CoderImpl

    config = load_config()
    if args.project:
        root_abs = str(Path(args.project).resolve())
        config.setdefault("project", {})["root"] = root_abs
        config.setdefault("ai", {})["tokenbase_dir"] = root_abs
    root = (config.get("project", {}) or {}).get("root", "")
    if not root:
        print("错误:未配置 project.root(用 --project 指定或在 config 配置)。")
        return 2

    print(f"工程目录:{root}")
    print(f"实现目标:{args.goal or '(据需求文档实现全部功能)'}")
    print(f"模型:{(config.get('ai', {}) or {}).get('model', '(未配置)')}")

    ai = make_ai_client(config)
    coder = CoderImpl(root)
    out = ai.implement_from_docs(goal=args.goal or "", scope=args.scope or "")
    summary = out.get("summary", "")
    files = out.get("files", [])
    meta = out.get("meta", {})

    print("\n---------- AI 实现摘要 ----------")
    print(summary or "(无摘要)")
    syms = meta.get("tokenbase_symbols", [])
    if syms:
        print(f"  tokenbase 抽取符号:{syms}")
    for echo in meta.get("tokenbase_echo", []):
        print(f"    $ {echo}")
    for echo in meta.get("docs_echo", []):
        print(f"  预读:{echo}")
    print(f"  生成文件数:{len(files)}  mock={meta.get('mock', False)}"
          f"  图片随附={meta.get('images_sent', 0)}")
    if meta.get("error"):
        print(f"  错误:{meta['error']}")

    if not files:
        print("\n(AI 未产出可写文件)")
        return 1

    print("\n---------- 写入工程 ----------")
    written, skipped = coder.write_files(files)
    print(f"写入 {written} 个,跳过 {skipped} 个:")
    for f in files:
        print(f"  · {f['path']}  ({len(f['content'])} 字符)  {f.get('reason', '')}")
    print("\n提示:如需撤销,回滚将删除新建文件 / 还原已覆盖文件(.bak)。")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="embedded-ai-coder",
        description="EmbeddedAiCoder — STM32 编码自动化(M1 CLI)",
    )
    sub = p.add_subparsers(dest="command")

    pr = sub.add_parser("run", help="跑闭环")
    pr.add_argument("--goal", default="", help="本轮目标(自然语言)")
    pr.add_argument("--max-iter", type=int, default=0, help="最大轮数(覆盖 config)")
    pr.add_argument("--dry-run", action="store_true",
                    help="mock 模式:不调模型/不烧录,用预制 patch 跑通演示")
    pr.add_argument("--project", default="", help="工程根目录(覆盖 config.project.root)")
    pr.add_argument("--demo-log", default="", help="用指定日志文件回放(代替真实串口)")
    pr.add_argument("--json", action="store_true", help="额外输出每轮 JSON")
    pr.add_argument("-v", "--verbose", action="store_true")
    pr.set_defaults(func=cmd_run)

    pc = sub.add_parser("collect", help="只采集看日志")
    pc.add_argument("--port", default="", help="串口(覆盖 config)")
    pc.add_argument("--demo-log", default="", help="回放指定日志文件")
    pc.add_argument("-v", "--verbose", action="store_true")
    pc.set_defaults(func=cmd_collect)

    pi = sub.add_parser("index", help="对工程目录建 tokenbase 索引")
    pi.add_argument("directory", help="工程根目录")
    pi.add_argument("--force", action="store_true", help="强制全量重解析")
    pi.add_argument("-v", "--verbose", action="store_true")
    pi.set_defaults(func=cmd_index)

    pim = sub.add_parser("implement", help="据原理图+需求文档,用 AI 实现产品固件")
    pim.add_argument("--goal", default="", help="实现目标(自然语言)")
    pim.add_argument("--scope", default="", help="范围/约束(可选)")
    pim.add_argument("--project", default="", help="工程根目录(覆盖 config.project.root)")
    pim.add_argument("--json", action="store_true", help="额外输出结果 JSON")
    pim.add_argument("-v", "--verbose", action="store_true")
    pim.set_defaults(func=cmd_implement)

    pce = sub.add_parser("check-env", help="环境自检(构建/烧录工具链是否就绪)")
    pce.add_argument("-v", "--verbose", action="store_true")
    pce.set_defaults(func=cmd_check_env)
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
