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
from .docs_context import DocsConfig, DocsContextReader, make_docs_config
from .interfaces import AiClient

logger = logging.getLogger(__name__)


def _has_image(messages: list[dict]) -> bool:
    """messages 里是否含 image_url 内容(多模态)。"""
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def _strip_images(messages: list[dict]) -> list[dict]:
    """把多模态 content 退化为纯文本(只保留 text 部分,拼接)。"""
    out: list[dict] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            texts = [p.get("text", "") for p in c
                     if isinstance(p, dict) and p.get("type") == "text"]
            out.append({**m, "content": "\n".join(t for t in texts if t)})
        else:
            out.append(m)
    return out

# 系统提示词:告诉模型它是嵌入式代码医生,且必须返回严格 JSON
SYSTEM_PROMPT = (
    "你是一名资深 STM32 嵌入式 C 工程师与故障诊断专家。"
    "用户会给你一段串口日志(含 fault/崩溃)、来自代码数据库的符号签名上下文,"
    "以及可能的【项目文档预读】——包括需求文档正文与原理图(原理图以图片形式随消息附送)。"
    "请结合原理图的引脚/外设/网络连接与需求文档的产品定义定位根因,并给出最小化的 C 代码补丁。\n\n"
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


# 实现模式系统提示词:据原理图+需求文档+工程已有签名,生成/实现产品固件文件(整文件输出)
IMPLEMENT_SYSTEM_PROMPT = (
    "你是一名资深 STM32 嵌入式 C 工程师。用户会给你【项目文档预读】"
    "(需求文档正文 + 原理图元信息/图片)、【实现目标】,以及【来自代码数据库的工程已有签名上下文】"
    "(现有函数 signature Lens / HAL 句柄 / 类型 / 宏的定位)。"
    "请据此实现产品的固件功能:复用/扩展现有代码、用对已有句柄名与类型,"
    "为每个需要的源文件给出完整内容(STM32 HAL 风格,可直接放入工程编译)。\n\n"
    "输出要求:\n"
    "- 必须返回严格 JSON,不要 markdown 代码块包裹,不要解释性前言。\n"
    "- schema:\n"
    '  {"summary":"本次实现了什么(一两句)",\n'
    '   "files":[{"path":"相对工程根的路径(如 src/sht40.c)",'
    '"content":"该文件完整 C 源码","reason":"该文件职责"}]}\n'
    "- content 是该文件的完整源码(含头文件声明与实现),保留缩进与换行,不要用 ``` 包裹。\n"
    "- 路径用正斜杠;按需求文档实现全部功能模块。\n"
    "- 优先复用/扩展现有文件;确需新建才给新文件。"
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
    max_tokens: int = 8192         # 生成上限(推理模型需较大,否则 content 被截断为空)
    docs: Optional[DocsConfig] = None   # 项目文档预读配置(原理图/需求文档)


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
        max_tokens=int(ai.get("max_tokens", 8192)),
        docs=make_docs_config(config),
    )


class AiClientImpl(AiClient):
    """AiClient 实现:tokenbase 上下文 + OpenAI 兼容 HTTP + mock。"""

    def __init__(self, cfg: AiConfig, docs_reader: Optional[DocsContextReader] = None):
        self.cfg = cfg
        # 预读器:读一次缓存复用;无 docs 配置则不预读
        self._docs = docs_reader or (
            DocsContextReader(cfg.docs) if cfg.docs is not None else None
        )
        self._http = None  # lazy

    # ---------- 对外主入口 ----------
    def diagnose_and_patch(
        self,
        log_context: str,
        source_context: str,
        goal: str = "",
        build_errors: str = "",
    ) -> dict:
        """主入口。返回 {diagnosis, patches, meta}。

        meta 里有 tokenbase 查询的回显(query 命令 + Lens),便于审计演示。
        build_errors 非空时(编译自愈用),优先让 AI 修复编译错误。
        """
        # 1) 从日志片段里抽符号 → tokenbase query 取 Lens
        symbols = self._extract_symbols(log_context)
        lens_block, query_echo = self._gather_tokenbase(symbols)

        # 1b) 预读项目文档(原理图/需求文档):读一次缓存复用
        docs_text, docs_images, docs_echo = self._gather_docs()

        # 2) 决定 mock 还是真调
        if self._should_mock():
            result = self._mock_result(log_context, lens_block,
                                       build_errors=build_errors)
        else:
            result = self._call_model(log_context, lens_block, source_context, goal,
                                      docs_text, docs_images, build_errors)

        result.setdefault("meta", {})
        result["meta"]["tokenbase_symbols"] = symbols
        result["meta"]["tokenbase_echo"] = query_echo
        result["meta"]["docs_echo"] = docs_echo
        return result

    # ---------- 文档预读接入 ----------
    def _gather_docs(self) -> tuple[str, list[str], list[str]]:
        """预读原理图/需求文档。返回 (prompt 文本块, 图片 data_url 列表, 回显行)。"""
        if self._docs is None:
            return "", [], []
        items, echo = self._docs.read_all()
        text_block = self._docs.render_text_block(items)
        images = self._docs.render_images(items)
        return text_block, images, echo

    # ---------- 据文档实现功能(F-24 扩展)----------
    def implement_from_docs(self, goal: str, scope: str = "") -> dict:
        """据预读的原理图 + 需求文档,让 AI 实现/生成产品固件文件。

        同时用 STM32_TokenBase 查询工程已有符号的 signature Lens,让生成的代码
        与现有源码(函数/HAL 句柄/类型/宏)衔接,而非盲写。
        返回 {summary, files:[{path,content,reason}], meta}。meta 含 docs_echo /
        images_sent / tokenbase_symbols / tokenbase_echo。无 key/dry_run 时走 mock。
        """
        docs_text, docs_images, docs_echo = self._gather_docs()
        # 从目标 + 需求文档抽候选符号 → tokenbase 查已有签名
        tb_symbols = self._extract_symbols_impl(
            f"{goal}\n{scope}\n{docs_text}", limit=max(self.cfg.max_symbols, 16))
        lens_block, tb_echo = self._gather_tokenbase(tb_symbols)
        if self._should_mock():
            result = self._mock_implement(goal)
        else:
            user_msg = self._build_implement_prompt(goal, scope, docs_text, lens_block)
            messages = [
                {"role": "system", "content": IMPLEMENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]
            try:
                # 整文件生成 token 消耗大;起始用配置预算,不足时 _post_chat 自动翻倍
                content = self._post_chat(messages, budget=self.cfg.max_tokens)
                result = self._parse_implement_json(content)
            except Exception as exc:
                logger.error("实现生成失败:%s", exc)
                result = {"summary": f"(生成失败:{exc})", "files": [],
                          "meta": {"error": str(exc)}}
        result.setdefault("meta", {})
        result["meta"]["docs_echo"] = docs_echo
        result["meta"]["images_sent"] = len(docs_images)
        result["meta"]["tokenbase_symbols"] = tb_symbols
        result["meta"]["tokenbase_echo"] = tb_echo
        return result

    def _extract_symbols_impl(self, text: str, limit: int = 16) -> list[str]:
        """从实现目标/需求文本里抽「代码味」候选符号(函数/句柄/宏/类型),供 tokenbase query。

        判定:含下划线 / camelCase / 全大写宏 / 含数字(引脚句柄如 hi2c1、PB10、SHT40)。
        未命中的会在 Lens 块里标注,不影响。
        """
        stop = {"the", "and", "for", "with", "that", "this", "from", "into", "using",
                "void", "int", "char", "float", "double", "struct", "enum", "return",
                "static", "const", "extern", "true", "false", "null", "size_t"}
        syms: list[str] = []
        seen: set[str] = set()
        for m in re.finditer(r"[A-Za-z_]\w{2,}", text or ""):
            tok = m.group(0)
            low = tok.lower()
            if low in stop or low in seen:
                continue
            is_code = ("_" in tok
                       or any(c.isupper() for c in tok[1:])    # camelCase / 含大写
                       or (tok.isupper() and len(tok) > 2)     # 全大写宏
                       or any(c.isdigit() for c in tok))        # 含数字(引脚/句柄)
            if not is_code:
                continue
            seen.add(low)
            syms.append(tok)
            if len(syms) >= limit:
                break
        return syms

    def _build_implement_prompt(self, goal: str, scope: str, docs_text: str,
                                lens_block: str = "") -> str:
        parts = ["# 实现目标",
                 goal.strip() or "根据下方需求文档与原理图,实现产品的全部固件功能。"]
        if scope.strip():
            parts.append("\n# 范围 / 约束")
            parts.append(scope.strip())
        parts.append("")
        parts.append(docs_text or "(无项目文档上下文)")
        if lens_block.strip():
            parts.append("")
            parts.append(lens_block)
            parts.append("\n实现时请复用/扩展上面已有的函数、HAL 句柄(如 hi2c1/huart3)、类型与宏,"
                         "保证新生成代码与现有源码衔接、可直接编译。")
        parts.append("\n请输出实现方案(严格 JSON:summary + files[])。")
        return "\n".join(parts)

    def _parse_implement_json(self, content: str) -> dict:
        """解析实现结果 JSON(容错:剥离 ```、抓首个 {...})。"""
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("实现 JSON 解析失败:%s", exc)
            return {"summary": f"(JSON 解析失败:{exc})", "files": [],
                    "meta": {"raw_truncated": content[:800]}}
        files = []
        for f in (obj.get("files") or []):
            path = (f.get("path") or "").replace("\\", "/").lstrip("/")
            content_text = f.get("content") or ""
            if path and content_text.strip():
                files.append({"path": path, "content": content_text,
                              "reason": f.get("reason", "")})
        return {"summary": obj.get("summary", ""), "files": files}

    def _mock_implement(self, goal: str) -> dict:
        """mock:无 key 时返回示意实现,保证流程可演示。"""
        return {
            "summary": "[mock] 未配置 ai.api_key,返回示意骨架(配好 key 后由模型生成)",
            "files": [{
                "path": "src/sht40.c",
                "content": ('/* mock 占位:配置 ai.api_key 后由模型生成 */\n'
                            '#include "sht40.h"\n\n'
                            'int sht40_read(float *temp, float *rh) { (void)temp; (void)rh; return 0; }\n'),
                "reason": "示意:SHT40 温湿度读取骨架",
            }],
            "meta": {"mock": True},
        }

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
                    source_context: str, goal: str,
                    docs_text: str = "", docs_images: list[str] | None = None,
                    build_errors: str = "") -> dict:
        """调 OpenAI 兼容 API。有原理图图片时走多模态(content 列表含 image_url)。"""
        docs_images = docs_images or []
        user_msg = self._build_user_prompt(log_context, lens_block, source_context, goal,
                                           docs_text, build_errors)
        messages = self._build_messages(user_msg, docs_images)
        try:
            content = self._post_chat(messages)
            return self._parse_model_json(content)
        except Exception as exc:
            logger.error("模型调用失败,回退 mock:%s", exc)
            return self._mock_result(log_context, lens_block,
                                     error=f"模型调用失败:{exc}",
                                     build_errors=build_errors)

    def _build_messages(self, user_msg: str, docs_images: list[str]) -> list[dict]:
        """构造 messages:有原理图图片 + vision 开启时,user content 用 [text,image_url...]。"""
        if docs_images and self.cfg.docs and self.cfg.docs.vision:
            user_content: object = [{"type": "text", "text": user_msg}]
            for data_url in docs_images:
                user_content.append(
                    {"type": "image_url", "image_url": {"url": data_url}})
        else:
            user_content = user_msg
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def _post_chat(self, messages: list[dict], *, want_json: bool = True,
                   budget: int | None = None) -> str:
        """发 chat/completions 并处理推理模型与端点差异。返回 content 文本。

        三级自动回退:
        - response_format=json_object 被端点拒(400)→ 去掉重试(按文本解析 JSON)。
        - 图片(image_url)被端点拒(如 glm-5.1 仅接受 text)→ 退化为纯文本重试。
        - 推理模型 content 空 + finish=length → 加倍 max_tokens 重试(glm-5.1 思考在
          reasoning_content、答案在 content,预算不足时 content 会被截断为空)。

        budget:起始 max_tokens(整文件生成可传更大值);默认用 cfg.max_tokens。
        """
        import httpx  # lazy

        base = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        budget = budget or self.cfg.max_tokens
        rf = {"type": "json_object"} if want_json else None
        msgs = messages
        for attempt in range(5):
            payload = {
                "model": self.cfg.model or "gpt-4o-mini",
                "messages": msgs,
                "temperature": 0.2,
                "max_tokens": budget,
            }
            if rf:
                payload["response_format"] = rf
            resp = httpx.post(url, json=payload, headers=headers, timeout=180.0)
            if resp.status_code == 400:
                body = resp.text[:300]
                if rf:  # 1) 端点拒 response_format
                    logger.warning("端点拒绝 response_format,去掉后重试")
                    rf = None
                    continue
                if _has_image(msgs):  # 2) 端点不支持图片
                    logger.warning("端点不支持图片内容,退化为纯文本重试")
                    msgs = _strip_images(msgs)
                    continue
                raise RuntimeError(f"API 400:{body}")
            resp.raise_for_status()
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            content = (msg.get("content") or "").strip()
            finish = choice.get("finish_reason")
            if content:
                return content
            # content 空:推理模型可能被 max_tokens 截断 → 加预算重试
            if finish == "length" and attempt < 4 and budget < 32768:
                budget = min(budget * 2, 32768)
                logger.warning("推理模型 content 为空(finish=length),max_tokens→%d 重试", budget)
                continue
            # 仍空:回退 reasoning_content(总比没有强)
            reasoning = (msg.get("reasoning_content") or "").strip()
            if reasoning:
                logger.warning("模型 content 为空,回退 reasoning_content(%d 字符)", len(reasoning))
                return reasoning
            raise RuntimeError(f"模型返回空内容(finish={finish})")
        raise RuntimeError("模型多次重试仍无内容")

    def _build_user_prompt(self, log_context: str, lens_block: str,
                           source_context: str, goal: str,
                           docs_text: str = "", build_errors: str = "") -> str:
        parts: list[str] = []
        if goal:
            parts.append(f"# 用户目标\n{goal}")
        # 编译自愈:把上一轮编译错误喂回,优先修复
        if build_errors.strip():
            parts.append("\n# 上一轮编译错误(优先修复以通过编译)")
            parts.append("```")
            parts.append(build_errors.strip()[-2000:])
            parts.append("```")
        parts.append("# 串口日志(fault 现场)")
        parts.append("```")
        parts.append(log_context.strip())
        parts.append("```")
        parts.append("")
        parts.append(lens_block)
        if docs_text.strip():
            parts.append("")
            parts.append(docs_text)
        if source_context.strip():
            parts.append("\n# 额外源码片段(可选)")
            parts.append("```c")
            parts.append(source_context.strip())
            parts.append("```")
        tail = ("请优先修复上面的编译错误,给出通过编译的最小补丁(严格 JSON)。"
                if build_errors.strip()
                else "请给出根因 + 最小化 C 补丁(严格 JSON)。")
        parts.append("\n" + tail)
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
                     error: str = "", build_errors: str = "") -> dict:
        """mock 模式:基于演示工程的已知 bug 返回一个预制 patch。

        该 patch 针对 examples/demo_project 的越界 bug:
        ``for (i = 0; i <= SENSOR_BUF_SIZE; i++)`` → ``i < SENSOR_BUF_SIZE``。
        build_errors 非空时(编译自愈演示),diagnosis 标注为编译修复。
        """
        if build_errors.strip():
            diagnosis = "[mock] 编译自愈:据编译错误返回修复补丁(演示)。"
        else:
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
