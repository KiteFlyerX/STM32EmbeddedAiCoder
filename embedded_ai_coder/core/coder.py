"""
代码回写:Coder(M1)。

把 AI 产出的补丁安全写回工程文件:
- 按函数名(anchor)定位:优先 tree-sitter 精确定位函数体;不可用则正则兜底。
- 按 old/new 做最小替换(支持按行模糊匹配:去除前后空白差异)。
- 回写前把原文件备份到 .bak(同目录),并登记到回滚栈。
- 支持回滚(按 patch 逆序还原)。
对应需求 F-04。基于 interfaces.Coder。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .interfaces import Coder

logger = logging.getLogger(__name__)


@dataclass
class _AppliedPatch:
    """记录一次已应用的补丁,用于回滚。"""
    file: Path
    bak_path: Path
    # 回滚用:把 new_text 还原为 old_text(同一文件内,仅一处时安全)
    old_text: str
    new_text: str


class CoderImpl(Coder):
    """代码回写实现。"""

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        self._applied: list[_AppliedPatch] = []

    # ---------- 对外主入口 ----------
    def apply(self, patch: dict) -> bool:
        """应用单个补丁。patch 形如 {file, anchor, old, new}。"""
        rel = patch.get("file", "")
        if not rel:
            logger.error("补丁缺 file 字段,跳过")
            return False
        target = (self.root / rel).resolve()
        if not target.exists():
            logger.error("目标文件不存在:%s", target)
            return False

        old_text = patch.get("old", "")
        new_text = patch.get("new", "")
        anchor = patch.get("anchor", "")

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("读取 %s 失败:%s", target, exc)
            return False

        # 1) 定位 + 替换
        new_content, ok = self._replace(content, old_text, new_text, anchor)
        if not ok:
            logger.warning("补丁未命中(old 不匹配),文件未改:%s", target)
            return False

        # 2) 备份原文件
        bak = self._backup(target)

        # 3) 回写
        try:
            target.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            logger.error("回写 %s 失败:%s", target, exc)
            return False

        self._applied.append(_AppliedPatch(target, bak, old_text, new_text))
        logger.info("已应用补丁:%s(anchor=%s)", target.name, anchor or "?")
        return True

    def apply_many(self, patches: list[dict]) -> tuple[int, int]:
        """批量应用。返回 (成功数, 失败数)。"""
        ok_n = fail_n = 0
        for p in patches:
            if self.apply(p):
                ok_n += 1
            else:
                fail_n += 1
        return ok_n, fail_n

    # ---------- 定位 / 替换 ----------
    def _replace(self, content: str, old_text: str, new_text: str,
                 anchor: str) -> tuple[str, bool]:
        """在 content 里把 old 替换为 new,返回 (新内容, 是否命中)。

        策略(依次尝试):
          a) 精确子串匹配;
          b) 模糊行匹配:忽略每行首尾空白后再比对;
          c) anchor 兜底:若 old 没命中但有 anchor(函数名),在函数体里找最接近的一行替换。
        """
        if not old_text and not new_text:
            return content, False

        # a) 精确匹配
        if old_text and old_text in content:
            return content.replace(old_text, new_text, 1), True

        # b) 模糊行匹配
        if old_text:
            idx = self._fuzzy_find_line(content, old_text)
            if idx is not None:
                return self._replace_block(content, idx, old_text, new_text), True

        # c) anchor 兜底:用函数名定位函数体,首行匹配 old 的核心 token
        if anchor:
            rng = self._locate_function(content, anchor)
            if rng is not None:
                start, end = rng
                body = content[start:end]
                # 用 old 的首个非空标识片段去匹配
                key = self._first_meaningful_token(old_text)
                if key and key in body:
                    new_body = body.replace(key, self._first_meaningful_token(new_text) or key, 1)
                    return content[:start] + new_body + content[end:], True
        return content, False

    def _fuzzy_find_line(self, content: str, old_text: str) -> int | None:
        """模糊匹配:old_text 第一行(去空白)在 content 里出现,返回该处字符偏移。"""
        lines = old_text.strip().splitlines()
        if not lines:
            return None
        key = " ".join(lines[0].split())
        for i, ln in enumerate(content.splitlines()):
            if key in " ".join(ln.split()):
                # 返回该行起始的字符偏移
                return self._line_start_offset(content, i)
        return None

    def _line_start_offset(self, content: str, line_no: int) -> int:
        """第 line_no(0-based)行的起始字符偏移。"""
        offset = 0
        for i, ln in enumerate(content.splitlines(keepends=True)):
            if i == line_no:
                return offset
            offset += len(ln)
        return offset

    def _replace_block(self, content: str, start_off: int,
                       old_text: str, new_text: str) -> str:
        """从 start_off 开始,匹配 old_text(模糊)并替换为 new_text。"""
        old_lines = [ln.strip() for ln in old_text.strip().splitlines() if ln.strip()]
        new_lines = new_text.rstrip("\n").splitlines()
        # 从 start_off 起,按行对照,逐行替换命中行
        content_lines = content.splitlines(keepends=True)
        # 计算 start_off 对应的行号
        line_no = content.count("\n", 0, start_off)
        replaced = 0
        out_lines = list(content_lines)
        oi = 0
        ni = line_no
        while oi < len(old_lines) and ni < len(out_lines):
            cur = " ".join(out_lines[ni].split())
            if old_lines[oi] and old_lines[oi] in cur:
                if replaced < len(new_lines):
                    indent = out_lines[ni][: len(out_lines[ni]) - len(out_lines[ni].lstrip())]
                    out_lines[ni] = indent + new_lines[replaced].lstrip() + "\n"
                    replaced += 1
                oi += 1
            ni += 1
        # 若 new 比命中的 old 行少,已自然覆盖;若多,剩余忽略(M1 简化)
        return "".join(out_lines)

    def _first_meaningful_token(self, text: str) -> str:
        """取文本里第一个有意义的片段(非空白非括号)。"""
        m = re.search(r"[A-Za-z_]\w*[^;{}]*", text)
        return m.group(0).strip() if m else ""

    # ---------- 函数定位(tree-sitter / 正则兜底)----------
    def _locate_function(self, content: str, func_name: str) -> tuple[int, int] | None:
        """返回函数体的 (起始字符偏移, 结束字符偏移)。None 表示未找到。"""
        rng = self._locate_function_treesitter(content, func_name)
        if rng is not None:
            return rng
        return self._locate_function_regex(content, func_name)

    def _locate_function_treesitter(self, content: str,
                                    func_name: str) -> tuple[int, int] | None:
        """用 tree-sitter-c 精确定位函数体起止(字节偏移 → 字符偏移近似)。"""
        try:
            import tree_sitter_c
            from tree_sitter import Language, Parser, Query, QueryCursor
        except Exception:
            return None
        try:
            lang = Language(tree_sitter_c.language())
            parser = Parser(lang)
            tree = parser.parse(content.encode("utf-8"))
            q = Query(lang, r"""
                (function_definition
                  declarator: [
                    (function_declarator declarator: (identifier) @name)
                    (pointer_declarator declarator:
                      (function_declarator declarator: (identifier) @name))
                  ]) @func
            """)
            for cap_name, nodes in QueryCursor(q).captures(tree.root_node).items():
                if cap_name != "name":
                    continue
                for n in nodes:
                    if n.text.decode("utf-8", "replace") == func_name:
                        # 向上找到 function_definition
                        parent = n
                        while parent is not None and parent.type != "function_definition":
                            parent = parent.parent
                        if parent is not None:
                            # 字节偏移;对纯 ASCII 工程等同字符偏移(C 文件注释外多 ASCII)
                            return parent.start_byte, parent.end_byte
        except Exception as exc:
            logger.debug("tree-sitter 定位失败,回退正则:%s", exc)
        return None

    def _locate_function_regex(self, content: str,
                               func_name: str) -> tuple[int, int] | None:
        """正则兜底:找 ``<返回类型> func_name(...) { ... }``。"""
        pat = re.compile(
            r"\b" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{",
            re.MULTILINE,
        )
        m = pat.search(content)
        if not m:
            return None
        start = m.start()
        # 从 '{' 起做花括号配平找函数尾
        brace_off = content.index("{", m.start())
        depth = 0
        for i in range(brace_off, len(content)):
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        return None

    # ---------- 备份 / 回滚 ----------
    def _backup(self, target: Path) -> Path:
        """备份原文件到 .bak(覆盖旧的 .bak)。"""
        bak = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, bak)
        logger.info("已备份 %s → %s", target.name, bak.name)
        return bak

    def rollback_all(self) -> int:
        """回滚所有已应用补丁(逆序)。返回回滚条数。"""
        n = 0
        while self._applied:
            ap = self._applied.pop()
            try:
                # 优先用 .bak 整体还原(最稳)
                if ap.bak_path.exists():
                    shutil.copy2(ap.bak_path, ap.file)
                    ap.bak_path.unlink(missing_ok=True)
                else:
                    # 逆替换:new → old
                    cur = ap.file.read_text(encoding="utf-8")
                    ap.file.write_text(cur.replace(ap.new_text, ap.old_text, 1),
                                       encoding="utf-8")
                n += 1
                logger.info("已回滚 %s", ap.file.name)
            except Exception as exc:
                logger.error("回滚 %s 失败:%s", ap.file, exc)
        return n

    @property
    def applied(self) -> list[_AppliedPatch]:
        return list(self._applied)
