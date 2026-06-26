"""
AI 客户端:AiClient(M1 核心)。

职责:
1. 改码前用 tokenbase query 取相关符号的 signature Lens + provenance,拼进 prompt
   (省 token 的实战接入,F-07)。
2. 调大模型 —— OpenAI 兼容 HTTP API(httpx)。provider/model/base_url/api_key 来自
   config 的 ai 段;Claude(经 openai 兼容代理)/ OpenAI / 本地(ollama/llama.cpp)都
   能用 base_url 切换。
3. 返回结构化结果:{diagnosis, patches:[{file, anchor, old, new}]},用 JSON 模式保证。
4. 无 key 时走 mock 模式:返回预制 patch,保证闭环端到端可演示(--dry-run)。

对应需求 F-03。基于 interfaces.AiClient。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import tokenbase_bridge
from .interfaces import AiClient

logger = logging.getLogger(__name__)

# 系统提示词:告诉模型它是嵌入式代码医生,且必须返回严格 JSON
SYSTEM_PROMPT = (
    "你是一名资深 STM32 嵌入式 C 工程师与故障诊断专家。"
    "用户会给你一段串口日志(含 fault/崩溃)+ 来自代码数据库的符号签名上下文。"
    "请定位根因,并给出最小化的 C 代码补丁。\n\n"
    "输出要求:\n"
    "- 必须返回严格 JSON,不要 markdown 包裹,不要解释性前言。\n"
    "- schema:\n"
    '  {"diagnosis":"根因一句话","patches":[{"file":"相对工程根的路径",'
    '  "anchor":"函数名(用该函数定位)","old":"要被替换的原始代码块",'
    '  "new":"替换后的新代码块","reason":"为何这么改"}]}\n'
    "- old/new 用原始 C 代码(保留缩进),new 是修正后的完整代码块。\n"
    "- anchor 用 patch 所在函数的名字,便于按函数定位回写。\n"
    "- 只改必要的行,不要重写整个文件。"
)


@dataclass
class AiConfig:
    """AI 段配置。"""
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    dry_run: bool = False          # 强制 mock(无 key 或 --dry-run)
    tokenbase_dir: str = ""        # 工程目录(用于 tokenbase query)
    max_symbols: int = 8           # 单轮最多 query 的符号数(控 token)


def make_ai_config(config: dict) -> AiConfig:
    """从全局 config 抽取 ai 段。"""
    ai = (config.get("ai") or {}).copy()
    project_root = (config.get("project") or {}).get("root", "")
    return AiConfig(
        provider=ai.get("provider", ""),
        model=ai.get("model", ""),
        api_key=ai.get("api_key", ""),
        base_url=ai.get("base_url", ""),
        tokenbase_dir=ai.get("tokenbase_dir", project_root),
        max_symbols=int(ai.get("max_symbols", 8)),
    )


class AiClientImpl(AiClient):
    """AiClient 实现:tokenbase 上下文 + OpenAI 兼容 HTTP + mock。"""

    def __init__(self, cfg: AiConfig):
        self.cfg = cfg
        self._http = None  # lazy

    # ---------- 对外主入口 ----------
    def diagnose_and_patch(
        self,
        log_context: str,
        source_context: str,
        goal: str = "",
    ) -> dict:
        """主入口。返回 {diagnosis, patches, meta}。

        meta 里有 tokenbase 查询的回显(query 命令 + Lens),便于审计演示。
        """
        # 1) 从日志片段里抽符号 → tokenbase query 取 Lens
        symbols = self._extract_symbols(log_context)
        lens_block, query_echo = self._gather_tokenbase(symbols)

        # 2) 决定 mock 还是真调
        if self._should_mock():
            result = self._mock_result(log_context, lens_block)
        else:
            result = self._call_model(log_context, lens_block, source_context, goal)

        result.setdefault("meta", {})
        result["meta"]["tokenbase_symbols"] = symbols
        result["meta"]["tokenbase_echo"] = query_echo
        return result

    # ---------- tokenbase 接入 ----------
    def _extract_symbols(self, log_context: str) -> list[str]:
        """从日志里抽候选符号名(``symbol + offset`` 形态)。"""
        syms: list[str] = []
        for m in re.finditer(r"([A-Za-z_]\w*)\s*\+\s*\d+", log_context):
            name = m.group(1)
            if name.lower() not in ("main", "start"):
                syms.append(name)
        # 去重保序 + 截断
        seen: set[str] = set()
        uniq = []
        for s in syms:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq[: self.cfg.max_symbols]

    def _gather_tokenbase(self, symbols: list[str]) -> tuple[str, list[str]]:
        """对每个符号跑 tokenbase query,返回 (prompt 用的 Lens 块, 回显命令列表)。"""
        if not symbols or not self.cfg.tokenbase_dir:
            return "(无 tokenbase 上下文:未提供工程目录或日志里未抽到符号)", []
        results = tokenbase_bridge.query_many(symbols, self.cfg.tokenbase_dir)
        echo = [" ".join(r.command) + f"  [exit_ok={r.ok}]" for r in results]
        block = tokenbase_bridge.render_lens_block(results)
        return block, echo

    # ---------- 模型调用 ----------
    def _should_mock(self) -> bool:
        """无 key 或显式 dry_run 时走 mock。"""
        if self.cfg.dry_run:
            return True
        if not self.cfg.api_key:
            logger.warning("未配置 ai.api_key,AiClient 进入 mock 模式(返回预制 patch)")
            return True
        return False

    def _call_model(self, log_context: str, lens_block: str,
                    source_context: str, goal: str) -> dict:
        """调 OpenAI 兼容 API。"""
        import httpx  # lazy

        user_msg = self._build_user_prompt(log_context, lens_block, source_context, goal)
        base = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        payload = {
            "model": self.cfg.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return self._parse_model_json(content)
        except Exception as exc:
            logger.error("模型调用失败,回退 mock:%s", exc)
            return self._mock_result(log_context, lens_block,
                                     error=f"模型调用失败:{exc}")

    def _build_user_prompt(self, log_context: str, lens_block: str,
                           source_context: str, goal: str) -> str:
        parts: list[str] = []
        if goal:
            parts.append(f"# 用户目标\n{goal}")
        parts.append("# 串口日志(fault 现场)")
        parts.append("```")
        parts.append(log_context.strip())
        parts.append("```")
        parts.append("")
        parts.append(lens_block)
        if source_context.strip():
            parts.append("\n# 额外源码片段(可选)")
            parts.append("```c")
            parts.append(source_context.strip())
            parts.append("```")
        parts.append("\n请给出根因 + 最小化 C 补丁(严格 JSON)。")
        return "\n".join(parts)

    def _parse_model_json(self, content: str) -> dict:
        """解析模型返回的 JSON(容错:剥离可能的 ```json 包裹)。"""
        text = content.strip()
        # 剥离 markdown 代码块
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # 尝试抓第一个 { ... }
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except json.JSONDecodeError as exc:
                    logger.error("模型 JSON 解析失败:%s", exc)
                    return {"diagnosis": "(模型返回解析失败)", "patches": [],
                            "meta": {"raw": content}}
            else:
                return {"diagnosis": "(模型返回非 JSON)", "patches": [],
                        "meta": {"raw": content}}
        # 规范化字段
        patches = obj.get("patches", []) or []
        norm_patches = []
        for p in patches:
            norm_patches.append({
                "file": p.get("file", ""),
                "anchor": p.get("anchor", ""),
                "old": p.get("old", ""),
                "new": p.get("new", ""),
                "reason": p.get("reason", ""),
            })
        return {"diagnosis": obj.get("diagnosis", ""), "patches": norm_patches,
                "meta": {"raw_truncated": content[:500]}}

    # ---------- mock ----------
    def _mock_result(self, log_context: str, lens_block: str,
                     error: str = "") -> dict:
        """mock 模式:基于演示工程的已知 bug 返回一个预制 patch。

        该 patch 针对 examples/demo_project 的越界 bug:
        ``for (i = 0; i <= SENSOR_BUF_SIZE; i++)`` → ``i < SENSOR_BUF_SIZE``。
        """
        diagnosis = (
            "[mock] 根因:sensor_samples_read 循环边界为 i <= SENSOR_BUF_SIZE,"
            "导致对 8 长度缓冲越界写,触发 HardFault(PRECISERR)。"
        )
        patches = [{
            "file": "src/demo_sensor.c",
            "anchor": "sensor_samples_read",
            "old": "for (uint8_t i = 0; i <= SENSOR_BUF_SIZE; i++) {",
            "new": "for (uint8_t i = 0; i < SENSOR_BUF_SIZE; i++) {",
            "reason": "修正循环边界:<= 改为 <,并按 count 上限裁剪(见 diagnosis)。",
        }]
        meta = {"mock": True}
        if error:
            meta["error"] = error
        return {"diagnosis": diagnosis, "patches": patches, "meta": meta}


def make_ai_client(config: dict) -> AiClientImpl:
    """工厂:从 config 构造 AiClient。"""
    return AiClientImpl(make_ai_config(config))
