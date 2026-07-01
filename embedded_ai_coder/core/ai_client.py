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


# ---------- F-25 五阶段一键生成:各阶段系统提示词 ----------
ARCH_SYSTEM_PROMPT = (
    "你是一名资深 STM32 嵌入式系统架构师。输入:产品目标 + 【引脚表(结构化,用户已校验,优先遵循)】"
    "+ 需求文档(可选)+ 原理图理解(可选,由视觉模型给出)。请给出整产品固件的架构设计。\n\n"
    "输出严格 JSON(不要 markdown 包裹,不要前言):\n"
    '  {"summary":"本次架构一两句话",\n'
    '   "hal_modules":["gpio","rcc","cortex","pwr","dma","i2c","uart",...],\n'
    '   "pin_assign":[{"signal":"I2C1_SCL","port":"PB6","function":"I2C1_SCL","module":"eeprom","note":""}],\n'
    '   "modules":[{"name":"eeprom","purpose":"铁电存储读写",'
    '"peripherals":[{"type":"i2c","instance":"1"}],"deps":[],'
    '"api":["uint8_t eeprom_read(uint16_t addr, uint8_t *buf, uint16_t len)"],'
    '"files":[{"path":"Drivers/BSP/eeprom.c","role":"impl"},'
    '{"path":"Drivers/BSP/eeprom.h","role":"hdr"}]}],\n'
    '   "state_machine":{"initial":"IDLE","events":["EVT_CARD","EVT_TIMEOUT"],'
    '"states":[{"name":"IDLE","entry":"led_idle()","exit":"",'
    '"transitions":[{"to":"RUN","event":"EVT_CARD","guard":""}]}]},\n'
    '   "tasks":[{"module":"eeprom","order":1}]}\n'
    "规则:\n"
    "- pin_assign 必须与输入引脚表一致,不得臆造引脚/端口。\n"
    "- 模块单一职责、可独立编译;每个模块给出对外 api 函数签名(供阶段④集成调用)。\n"
    "- 状态机用事件驱动,列出全部状态/事件/迁移。\n"
    "- tasks 给出阶段③逐模块生成的顺序(被依赖的靠前)。"
)

MODULE_SYSTEM_PROMPT = (
    "你是一名资深 STM32 HAL 驱动工程师。输入:单个模块规格(name/purpose/peripherals/api/files)"
    "+ 全局 pin_assign + 引脚表 + 该模块涉及的 HAL 句柄上下文。请实现该模块完整 .c/.h"
    "(STM32 HAL 风格,可直接放入工程编译)。\n\n"
    "输出严格 JSON:\n"
    '  {"summary":"该模块实现一两句",\n'
    '   "files":[{"path":"<与 module.files[].path 一致>","content":"完整源码","reason":"职责"}]}\n'
    "规则:\n"
    "- 路径必须匹配 module.files[].path;content 是完整源码,保留缩进换行,不要 ``` 包裹。\n"
    "- 用 HAL API;外设底层初始化(GPIO/时钟/复用/NVIC)写在 stm32g0xx_hal_msp.c 的对应 "
    "HAL_xxx_MspInit 里(可只给本模块需补充的 MspInit 片段并注明),驱动文件只调 HAL_xxx_Init。\n"
    "- 复用引脚表里的 port/function,不要臆造引脚。"
)

INTEGRATE_SYSTEM_PROMPT = (
    "你是一名资深 STM32 主循环集成工程师。输入:状态机定义 + 各模块对外 API + 引脚表。"
    "请生成主循环与状态机调度代码,把各模块串成可运行的产品固件。\n\n"
    "输出严格 JSON:\n"
    '  {"summary":"集成方案一两句","files":[\n'
    '    {"path":"Core/Src/main.c","content":"完整 main.c",'
    '"reason":"HAL_Init/SystemClock_Config/各 MX_*_Init/状态机 init + while(1) 调 fsm_tick()"},\n'
    '    {"path":"Core/Src/state_machine.c","content":"状态机实现","reason":"switch-case 或函数指针表"},\n'
    '    {"path":"Core/Inc/state_machine.h","content":"状态机接口","reason":"事件/状态枚举与 fsm_tick 声明"}]}\n'
    "规则:\n"
    "- main.c 整文件输出(覆盖 scaffold 占位骨架);保留 SystemClock_Config/Error_Handler。\n"
    "- 事件由各模块中断/轮询产生并注入事件队列;状态机 fsm_tick() 在主循环调用。\n"
    "- 调用各模块 api 时用其真实签名;不要臆造函数名。"
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
    vision_model: str = ""         # F-25:视觉模型(看原理图 PDF);空=不尝试视觉,纯走引脚表
    vision_base_url: str = ""      # 视觉端点,空=复用 base_url
    vision_api_key: str = ""       # 视觉 key,空=复用 api_key


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
        vision_model=ai.get("vision_model", ""),
        vision_base_url=ai.get("vision_base_url", ""),
        vision_api_key=ai.get("vision_api_key", ""),
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

    # ---------- F-25 五阶段一键生成 ----------
    def design_architecture(self, *, goal: str, pinmap_text: str = "",
                            modules_hint: str = "", docs_text: str = "",
                            use_vision: bool = True) -> dict:
        """阶段①:据目标+引脚表(+需求/原理图视觉)出架构设计 JSON。
        返回 {summary, hal_modules, pin_assign, modules[], state_machine, tasks, meta}。
        无 key 走 mock。视觉(vision_model)失败自动退回纯引脚表。"""
        vision_block = ""
        if use_vision and self.cfg.vision_model:
            try:
                _docs_text, img_urls, _echo = self._gather_docs()
                if img_urls:
                    vision_block = "\n# 原理图理解(视觉模型识别)\n" + self._call_vision(
                        "请识别这张 STM32 原理图的 MCU 型号、引脚分配、外设连接、关键元件,"
                        "用结构化文本输出(供架构设计参考)。", img_urls)
            except Exception as exc:  # noqa: BLE001
                logger.warning("视觉理解失败,退回纯引脚表:%s", exc)
                vision_block = ""
        if self._should_mock():
            result = self._mock_arch(goal, pinmap_text)
        else:
            user_msg = self._build_arch_prompt(goal, modules_hint, pinmap_text,
                                                docs_text, vision_block)
            messages = [{"role": "system", "content": ARCH_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg}]
            try:
                content = self._post_chat(messages, budget=max(self.cfg.max_tokens, 8192))
                result = self._parse_arch_json(content)
            except Exception as exc:  # noqa: BLE001
                logger.error("架构设计失败:%s", exc)
                result = {"summary": f"(架构设计失败:{exc})", "modules": [],
                          "state_machine": {}, "meta": {"error": str(exc)}}
        result.setdefault("meta", {})
        result["meta"]["pinmap_chars"] = len(pinmap_text or "")
        result["meta"]["vision_used"] = bool(vision_block)
        return result

    def generate_module(self, module_spec: dict, arch: dict,
                        pinmap_text: str = "") -> dict:
        """阶段③:为单个模块生成完整 .c/.h。返回 {summary, files[], meta}。"""
        if self._should_mock():
            return self._mock_module(module_spec)
        periph = json.dumps(module_spec.get("peripherals", []), ensure_ascii=False)
        api_txt = " ".join(module_spec.get("api", []) or [])
        syms = self._extract_symbols_impl(
            f"{module_spec.get('name','')} {periph} {api_txt}", limit=8)
        lens_block, _echo = self._gather_tokenbase(syms)
        pin_assign = json.dumps(arch.get("pin_assign", []), ensure_ascii=False)
        user_msg = self._build_module_prompt(module_spec, pin_assign, pinmap_text, lens_block)
        messages = [{"role": "system", "content": MODULE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}]
        try:
            content = self._post_chat(messages, budget=max(self.cfg.max_tokens, 6144))
            result = self._parse_implement_json(content)   # 复用 {summary,files[]}
        except Exception as exc:  # noqa: BLE001
            logger.error("模块 %s 生成失败:%s", module_spec.get("name"), exc)
            result = {"summary": f"(失败:{exc})", "files": [], "meta": {"error": str(exc)}}
        return result

    def integrate_main(self, arch: dict, module_apis: list[dict]) -> dict:
        """阶段④:生成 main.c + state_machine.c/.h。返回 {summary, files[], meta}。"""
        if self._should_mock():
            return self._mock_integrate(arch, module_apis)
        user_msg = self._build_integrate_prompt(arch, module_apis)
        messages = [{"role": "system", "content": INTEGRATE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}]
        try:
            content = self._post_chat(messages, budget=max(self.cfg.max_tokens, 6144))
            result = self._parse_implement_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.error("集成失败:%s", exc)
            result = {"summary": f"(失败:{exc})", "files": [], "meta": {"error": str(exc)}}
        return result

    def _call_vision(self, prompt_text: str, images_data_urls: list[str]) -> str:
        """用 vision_model 看原理图。临时换 cfg 的 model/base_url/api_key,复用 _post_chat
        (其内置 image_url 被拒→退化纯文本 回退)。无 vision_model/无图返回 ''。"""
        if not self.cfg.vision_model or not images_data_urls:
            return ""
        saved = (self.cfg.model, self.cfg.base_url, self.cfg.api_key)
        try:
            self.cfg.model = self.cfg.vision_model
            if self.cfg.vision_base_url:
                self.cfg.base_url = self.cfg.vision_base_url
            if self.cfg.vision_api_key:
                self.cfg.api_key = self.cfg.vision_api_key
            content_list: object = [{"type": "text", "text": prompt_text}]
            for u in images_data_urls:
                content_list.append({"type": "image_url", "image_url": {"url": u}})  # type: ignore[union-attr]
            messages = [{"role": "user", "content": content_list}]
            return self._post_chat(messages, want_json=False)
        finally:
            self.cfg.model, self.cfg.base_url, self.cfg.api_key = saved

    # ---------- 五阶段 prompt 构造 ----------
    def _build_arch_prompt(self, goal: str, modules_hint: str, pinmap_text: str,
                           docs_text: str, vision_block: str) -> str:
        parts = ["# 产品目标", goal.strip() or "据下方引脚表与需求实现产品固件"]
        if modules_hint.strip():
            parts.append("\n# 模块提示(用户给定,可调整/补充)")
            parts.append(modules_hint.strip())
        parts.append("\n# 引脚表(用户已校验,优先遵循)")
        parts.append(pinmap_text.strip() or "(未提供引脚表)")
        if vision_block.strip():
            parts.append(vision_block.strip())
        if docs_text.strip():
            parts.append("\n# 需求文档")
            parts.append(docs_text.strip())
        parts.append("\n请输出架构设计(严格 JSON:"
                     "summary/hal_modules/pin_assign/modules/state_machine/tasks)。")
        return "\n".join(parts)

    def _build_module_prompt(self, module_spec: dict, pin_assign: str,
                             pinmap_text: str, lens_block: str) -> str:
        parts = ["# 模块规格(本任务只实现这一个模块)",
                 json.dumps(module_spec, ensure_ascii=False, indent=2)]
        parts.append("\n# 全局引脚分配(pin_assign)")
        parts.append(pin_assign or "[]")
        if pinmap_text.strip():
            parts.append("\n# 引脚表(用户校验)")
            parts.append(pinmap_text.strip())
        if lens_block.strip():
            parts.append("\n# 工程已有符号上下文(复用其句柄/类型)")
            parts.append(lens_block)
        parts.append("\n请输出该模块 files[](严格 JSON,路径与 module.files[].path 一致)。")
        return "\n".join(parts)

    def _build_integrate_prompt(self, arch: dict, module_apis: list[dict]) -> str:
        parts = ["# 状态机定义",
                 json.dumps(arch.get("state_machine", {}), ensure_ascii=False, indent=2)]
        parts.append("\n# 各模块对外 API(供 main/状态机调用)")
        parts.append(json.dumps(module_apis, ensure_ascii=False, indent=2))
        parts.append("\n# 全局引脚分配")
        parts.append(json.dumps(arch.get("pin_assign", []), ensure_ascii=False, indent=2))
        parts.append("\n请输出 main.c + state_machine.c/.h(严格 JSON files[])。")
        return "\n".join(parts)

    def _parse_arch_json(self, content: str) -> dict:
        """解析架构 JSON(容错:剥离 ```、抓首个 {...})。"""
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
            logger.error("架构 JSON 解析失败:%s", exc)
            return {"summary": f"(架构 JSON 解析失败:{exc})", "modules": [],
                    "state_machine": {}, "meta": {"raw_truncated": content[:800]}}
        return obj

    # ---------- 五阶段 mock ----------
    def _mock_arch(self, goal: str, pinmap_text: str) -> dict:
        return {
            "summary": "[mock] 两模块示意架构(配好 ai.api_key 后由模型生成)",
            "hal_modules": ["gpio", "rcc", "cortex", "pwr", "dma", "i2c", "tim"],
            "pin_assign": [
                {"signal": "I2C1_SCL", "port": "PB6", "function": "I2C1_SCL", "module": "sht40", "note": ""},
                {"signal": "I2C1_SDA", "port": "PB7", "function": "I2C1_SDA", "module": "sht40", "note": ""},
                {"signal": "MOTOR_PWM", "port": "PA8", "function": "TIM1_CH1", "module": "motor", "note": ""},
            ],
            "modules": [
                {"name": "sht40", "purpose": "温湿度采集", "peripherals": [{"type": "i2c", "instance": "1"}],
                 "deps": [], "api": ["int sht40_read(float *t, float *rh);"],
                 "files": [{"path": "Drivers/BSP/sht40.c", "role": "impl"},
                           {"path": "Drivers/BSP/sht40.h", "role": "hdr"}]},
                {"name": "motor", "purpose": "直流电机 PWM", "peripherals": [{"type": "tim", "instance": "1"}],
                 "deps": [], "api": ["void motor_set(uint8_t duty);"],
                 "files": [{"path": "Drivers/BSP/motor.c", "role": "impl"},
                           {"path": "Drivers/BSP/motor.h", "role": "hdr"}]},
            ],
            "state_machine": {"initial": "IDLE", "events": ["EVT_TICK"],
                              "states": [{"name": "IDLE", "entry": "", "exit": "",
                                          "transitions": [{"to": "IDLE", "event": "EVT_TICK", "guard": ""}]}]},
            "tasks": [{"module": "sht40", "order": 1}, {"module": "motor", "order": 2}],
            "meta": {"mock": True},
        }

    def _mock_module(self, module_spec: dict) -> dict:
        name = module_spec.get("name", "mod")
        out_files = []
        for f in (module_spec.get("files") or []):
            p = (f.get("path") or "").strip()
            if not p:
                continue
            if p.endswith(".h"):
                content = (f"/* mock 占位 */\n#ifndef {name.upper()}_H\n#define {name.upper()}_H\n"
                           f'#include "main.h"\n#endif\n')
            else:
                content = f'/* mock 占位:配置 ai.api_key 后由模型生成 */\n#include "{name}.h"\n'
            out_files.append({"path": p, "content": content, "reason": "mock 占位"})
        return {"summary": f"[mock] {name} 占位实现", "files": out_files, "meta": {"mock": True}}

    def _mock_integrate(self, arch: dict, module_apis: list[dict]) -> dict:
        return {
            "summary": "[mock] 集成占位(配好 key 后由模型生成)",
            "files": [
                {"path": "Core/Src/main.c",
                 "content": '/* mock main 占位 */\n#include "main.h"\n'
                            'int main(void){HAL_Init();while(1){}}\n', "reason": "mock"},
                {"path": "Core/Src/state_machine.c",
                 "content": '/* mock fsm 占位 */\n#include "state_machine.h"\nvoid fsm_tick(void){}\n',
                 "reason": "mock"},
                {"path": "Core/Inc/state_machine.h",
                 "content": "#ifndef STATE_MACHINE_H\n#define STATE_MACHINE_H\nvoid fsm_tick(void);\n#endif\n",
                 "reason": "mock"},
            ],
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
