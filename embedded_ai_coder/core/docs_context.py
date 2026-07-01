"""
项目文档预读器:DocsContextReader(M2 增强)。

职责:在闭环启动前,把工程下的「原理图」与「需求文档」**读一次、缓存复用**,
渲染成 AI prompt 的上下文 —— 让 AI 诊断/改码时始终带着硬件设计与产品需求背景。

抽取策略(用户决策:原理图走多模态视觉 / 需求文档纯标准库,不增 pip 依赖):
- 文本类(.md/.txt/.c/.h/.json/...):read_text 直读。
- .docx:标准库 zipfile 读 word/document.xml,去标签抽正文(零依赖)。
- .pdf:纯标准库无法抽正文 → 仅记元信息备注。
- 图片原理图(.png/.jpg/...):base64 成 data_url,随 prompt 多模态发给视觉模型;
  极简 PNG/JPEG 头解析给出宽高作元信息提示;其余格式只给路径。
- .pdf 原理图:可选 import fitz(pymupdf,不在 requirements)渲染首页为图片;
  缺失则优雅降级(记备注跳过)。

预算:max_chars_per_doc / max_chars_total 截断控 token;max_images / max_image_mb 控图片成本。
缓存:按 (path, mtime, size) 命中则复用 —— 即「预读一次,跨迭代复用」。

对应需求 F-24。仿 core/tokenbase_bridge.py 的「独立模块 + 渲染上下文块」模式。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import base64
import logging
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 文本类后缀:直接 read_text
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".log",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".s", ".asm", ".ld",
    ".json", ".yaml", ".yml", ".csv", ".tsv", ".ini", ".cfg", ".conf",
    ".ioc", ".xml", ".html", ".htm",
}
# 图片类后缀:原理图视觉识别(多模态)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
_TRUNCATED_MARK = "(已截断)"


# ===================== 配置与数据结构 =====================
@dataclass
class DocsConfig:
    """docs 段配置。"""
    schematics: list[str] = field(default_factory=list)     # 原理图路径
    requirements: list[str] = field(default_factory=list)   # 需求文档路径
    pinmap: str = ""               # F-25:结构化引脚表 .md(主路径 + 视觉兜底校验)
    vision: bool = True            # 原理图走多模态视觉(需模型支持 image_url)
    max_images: int = 4            # 单轮最多随附原理图图片数(控成本)
    max_image_mb: float = 5.0      # 单张图片大小上限(MB),超过跳过
    max_chars_per_doc: int = 8000  # 单文档字符上限
    max_chars_total: int = 16000   # 所有文档合计字符上限


@dataclass
class DocItem:
    """一份预读文档的抽取结果。"""
    path: str
    kind: str          # "schematic" | "requirement"
    role: str          # "text" | "image" | "meta_only"
    text: str = ""     # 文本块 / 元信息提示
    image_data_url: str = ""   # 仅 role=="image":data:image/...;base64,...
    note: str = ""     # 备注(如抽取失败/截断/降级原因)


def make_docs_config(config: dict) -> DocsConfig:
    """从全局 config 抽取 docs 段。缺省值见 DocsConfig。"""
    docs = (config.get("docs") or {}).copy()
    return DocsConfig(
        schematics=list(docs.get("schematics") or []),
        requirements=list(docs.get("requirements") or []),
        pinmap=docs.get("pinmap", "") or "",
        vision=bool(docs.get("vision", True)),
        max_images=int(docs.get("max_images", 4)),
        max_image_mb=float(docs.get("max_image_mb", 5.0)),
        max_chars_per_doc=int(docs.get("max_chars_per_doc", 8000)),
        max_chars_total=int(docs.get("max_chars_total", 16000)),
    )


# ===================== 纯函数抽取器(模块级,便于单测)=====================
def extract_text_file(path: str | Path) -> tuple[str, str]:
    """文本类:直接读。返回 (text, note)。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "", f"读取失败:{exc}"
    return text, ""


def extract_docx(path: str | Path) -> tuple[str, str]:
    """ .docx:标准库 zipfile 读 word/document.xml,去标签抽正文(零依赖)。

    段落 ``<w:p>`` 之间换行;文本在 ``<w:t>`` 内。tab ``<w:tab/>``→制表符。
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            target = "word/document.xml" if "word/document.xml" in names else \
                next((n for n in names if n.lower().endswith("document.xml")), None)
            if target is None:
                return "", "docx 内未找到 document.xml"
            xml = z.read(target).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return "", f"docx 解析失败:{exc}"
    # 段落 / 制表符 → 文本标记
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t")
    # 去掉所有标签
    text = re.sub(r"<[^>]+>", "", xml)
    # 反转义常见 XML 实体
    text = (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
    # 规整连续空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, ""


def extract_pdf_meta(path: str | Path) -> tuple[str, str]:
    """ .pdf(需求文档):纯标准库无法抽正文 → 返回元信息备注。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        size = -1
    note = "PDF,纯标准库无法抽取正文,已记录文件信息"
    text = f"[PDF 文档]{p.name}  大小≈{size/1024:.1f}KB(正文未抽取)"
    return text, note


def _png_size(data: bytes) -> tuple[int, int] | None:
    """极简解析 PNG 宽高(IHDR 在前 24 字节内)。失败返回 None。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return w, h
    except Exception:  # noqa: BLE001
        pass
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """极简解析 JPEG 宽高(扫 SOF0/SOF2 标记)。失败返回 None。"""
    try:
        i = 2  # 跳过 SOI(FFD8)
        n = len(data)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            i += 2
            # SOF0..SOF15(不含 SOF4/DHT)携带宽高
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 3:i + 7])
                return w, h
            else:
                seg_len = struct.unpack(">H", data[i:i + 2])[0]
                i += seg_len
    except Exception:  # noqa: BLE001
        pass
    return None


def image_size(path: str | Path) -> tuple[int, int] | None:
    """尽量给出图片宽高(PNG/JPEG 头解析);其余格式返回 None。无 PIL 依赖。"""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        # PNG 头解析只需要前 24 字节,head 已够
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return _png_size(head)
        # JPEG 需要扫标记,重读较大头部块
        if head[:2] == b"\xff\xd8":
            with open(path, "rb") as f:
                data = f.read(min(Path(path).stat().st_size, 128 * 1024))
            return _jpeg_size(data)
    except OSError:
        return None
    return None


def _ext(path: str | Path) -> str:
    return Path(path).suffix.lower()


def load_image_as_data_url(path: str | Path, max_mb: float) -> tuple[str, str, str]:
    """图片 → data_url。返回 (data_url, meta_hint, note)。

    超过 max_mb 则跳过(data_url 为空,note 说明)。
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        return "", "", f"图片不可读:{exc}"
    if size > max_mb * 1024 * 1024:
        return "", "", f"图片过大({size/1024/1024:.1f}MB > {max_mb}MB),跳过"
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return "", "", f"图片读取失败:{exc}"
    ext = _ext(p).lstrip(".") or "png"
    # jpeg → jpg mime 简化
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    data_url = f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    wh = image_size(p)
    dim = f"{wh[0]}x{wh[1]}" if wh else "尺寸未知"
    meta_hint = f"原理图图片 {p.name}  {dim}  ≈{size/1024:.0f}KB"
    return data_url, meta_hint, ""


def render_pdf_pages_as_images(path: str | Path, max_pages: int = 1
                               ) -> tuple[list[str], str, str]:
    """ .pdf 原理图:可选 pymupdf(fitz)渲染首页为 PNG data_url。

    fitz 不在 requirements;不可用则降级(返回空列表 + 备注)。
    返回 (data_urls, meta_hint, note)。
    """
    try:
        import fitz  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return [], "", "PDF 原理图:未安装可选依赖 pymupdf(fitz),已跳过视觉识别"
    try:
        doc = fitz.open(path)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        return [], "", f"PDF 打开失败:{exc}"
    urls: list[str] = []
    n = min(max_pages, doc.page_count)
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=150)  # type: ignore[index]
        png = pix.tobytes("png")
        urls.append(f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}")
    doc.close()
    return urls, f"PDF 原理图 {Path(path).name}(已渲染前 {len(urls)} 页)", ""


# ===================== 预读器 =====================
class DocsContextReader:
    """读一次、缓存复用的项目文档预读器。

    缓存键:(path, mtime, size);文件未变则复用上次结果 —— 即「预读一次」。
    """

    def __init__(self, cfg: DocsConfig):
        self.cfg = cfg
        self._cache: dict[str, tuple[float, int, DocItem]] = {}

    # ---------- 对外主入口 ----------
    def read_all(self, *, force: bool = False) -> tuple[list[DocItem], list[str]]:
        """读取全部 schematics + requirements。返回 (items, echo)。"""
        items: list[DocItem] = []
        echo: list[str] = []

        for kind, paths in (("requirement", self.cfg.requirements),
                            ("schematic", self.cfg.schematics)):
            for p in paths:
                p = (p or "").strip()
                if not p:
                    continue
                item = self._read_one(p, kind, force=force)
                items.append(item)
                echo.append(self._echo_line(item))

        if not items:
            echo.append("(未配置 docs.schematics / docs.requirements,无预读文档)")
        # 引脚表(F-25 主路径文本,独立于 schematics/requirements)
        if (self.cfg.pinmap or "").strip():
            pm = self._read_pinmap_text()
            echo.append(f"pinmap:{Path(self.cfg.pinmap).name} -> "
                        f"{'文本' if pm else '空'} ({len(pm)}字符)")
        return items, echo

    def _read_pinmap_text(self) -> str:
        """读取结构化引脚表 .md(F-25 主路径文本)。空配置/读失败返回 ''。"""
        p = (self.cfg.pinmap or "").strip()
        if not p:
            return ""
        try:
            text, _note = extract_text_file(p)
        except Exception:  # noqa: BLE001
            return ""
        return (text or "").strip()

    # ---------- 单文件 ----------
    def _read_one(self, path: str, kind: str, *, force: bool) -> DocItem:
        """带缓存的单文件抽取。"""
        cached = None if force else self._cache.get(path)
        try:
            st = Path(path).stat()
            key = (st.st_mtime, st.st_size)
        except OSError as exc:
            return DocItem(path=path, kind=kind, role="meta_only",
                           text="", note=f"文件不可访问:{exc}")
        if cached and (cached[0], cached[1]) == key:
            return cached[2]

        item = self._extract(path, kind)
        self._cache[path] = (key[0], key[1], item)
        return item

    def _extract(self, path: str, kind: str) -> DocItem:
        """按后缀分发抽取。"""
        ext = _ext(path)
        exists = Path(path).exists()
        if not exists:
            return DocItem(path=path, kind=kind, role="meta_only",
                           text="", note="文件不存在")

        # ---- 需求文档:抽文本 ----
        if kind == "requirement":
            if ext == ".docx":
                text, note = extract_docx(path)
            elif ext == ".pdf":
                text, note = extract_pdf_meta(path)
            elif ext in TEXT_SUFFIXES:
                text, note = extract_text_file(path)
            else:
                # 未知后缀:尝试当文本读
                text, note = extract_text_file(path)
                if not text and not note:
                    note = "未知后缀,按文本读取"
            text = self._clip(text, self.cfg.max_chars_per_doc)
            role = "text" if text.strip() else "meta_only"
            return DocItem(path=path, kind=kind, role=role, text=text, note=note)

        # ---- 原理图:视觉优先(图片/PDF→图片) ----
        if ext in IMAGE_SUFFIXES:
            data_url, meta, note = load_image_as_data_url(path, self.cfg.max_image_mb)
            if data_url:
                return DocItem(path=path, kind=kind, role="image",
                               text=meta, image_data_url=data_url, note=note)
            return DocItem(path=path, kind=kind, role="meta_only", text=meta, note=note)
        if ext == ".pdf":
            urls, meta, note = render_pdf_pages_as_images(path)
            if urls:
                # 多页:首页作为 image item(带 data_url),其余页 text 段附上
                first = urls[0]
                extra = "\n".join(urls[1:])
                return DocItem(path=path, kind=kind, role="image",
                               text=meta + (f"\n(另附 {len(urls)-1} 页 data_url)" if extra else ""),
                               image_data_url=first, note=note)
            return DocItem(path=path, kind=kind, role="meta_only",
                           text=meta or f"原理图 PDF {Path(path).name}", note=note)

        # 未知原理图后缀:记元信息
        return DocItem(path=path, kind=kind, role="meta_only",
                       text=f"原理图 {Path(path).name}(后缀 {ext or '无'} 未识别)",
                       note="未识别的原理图格式")

    # ---------- 渲染 ----------
    def render_text_block(self, items: list[DocItem]) -> str:
        """把文本/元信息渲染成 prompt 用的「项目文档上下文」段(含预算截断)。"""
        blocks: list[str] = ["# 项目文档上下文(预读)"]
        used = 0
        budget = self.cfg.max_chars_total
        any_text = False
        # 引脚表优先置顶(F-25 主路径:视觉模型看不到原理图时,引脚信息仍在上文)
        pm = self._read_pinmap_text()
        if pm:
            seg = "## 引脚表(用户校验,优先遵循)\n" + pm
            blocks.append(seg)
            used += len(seg) + 1
            any_text = True
        for it in items:
            label = f"{'需求文档' if it.kind == 'requirement' else '原理图'} {Path(it.path).name}"
            if it.role == "image":
                # 图片本身走多模态,这里只放一行元信息提示
                seg = f"- {label}:[图片,随消息多模态发送] {it.text}".rstrip()
            else:
                body = it.text.strip()
                seg = f"## {label}\n{body}" if body else f"- {label}:(无正文,{it.note or '空'})"
            if used + len(seg) > budget:
                remain = budget - used
                if remain > 40:
                    seg = seg[:remain].rstrip() + f"\n{_TRUNCATED_MARK}"
                    blocks.append(seg)
                break
            blocks.append(seg)
            used += len(seg) + 1
            any_text = True
        if not any_text:
            blocks.append("(无可用文档上下文)")
        return "\n".join(blocks)

    def render_images(self, items: list[DocItem]) -> list[str]:
        """返回供多模态发送的 data_url 列表(受 max_images / vision 约束)。"""
        if not self.cfg.vision:
            return []
        urls: list[str] = []
        for it in items:
            if it.role == "image" and it.image_data_url:
                urls.append(it.image_data_url)
                if len(urls) >= self.cfg.max_images:
                    break
        return urls

    # ---------- 辅助 ----------
    @staticmethod
    def _echo_line(item: DocItem) -> str:
        rel = Path(item.path).name
        role_tag = {"text": "文本", "image": "图片", "meta_only": "元信息"}[item.role]
        size_info = f" {len(item.text)}字符" if item.text else ""
        note = f"  [{item.note}]" if item.note else ""
        return f"{item.kind}:{rel} -> {role_tag}{size_info}{note}"

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + f"\n{_TRUNCATED_MARK}"
