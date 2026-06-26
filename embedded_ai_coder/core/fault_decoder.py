"""
故障解码:FaultDecoder(M2 / F-09)。

Cortex-M HardFault 解码:fault 寄存器(CFSR / HFSR / MMFAR / BFAR)逐位译义
+ 栈帧(``#n 0xADDR symbol + off (file:line)``)解析。纯文本解析,可在 GUI 线程
同步调用;若提供 elf,则对缺 ``file:line`` 的帧补跑 ``addr2line``(可选,失败忽略)。

对应需求 F-09。基于 interfaces.FaultDecoder。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Optional

from .interfaces import FaultDecoder

logger = logging.getLogger(__name__)

# CFSR 位定义(MMFSR/BFSR/UFSR 合并为一个 32 位寄存器)
_CFSR_BITS: dict[int, str] = {
    # MMFSR(bits 0-7)
    0: "IACCVIOL(指令访问违例)",
    1: "DACCVIOL(数据访问违例)",
    3: "MUNSTKERR(出栈时 MemManage 错误)",
    4: "MSTKERR(入栈时 MemManage 错误)",
    7: "MMARVALID(MMFAR 有效)",
    # BFSR(bits 8-15)
    8: "IBUSERR(指令总线错误)",
    9: "PRECISERR(精确数据总线错误)",
    10: "IMPRECISERR(非精确数据总线错误)",
    11: "UNSTKERR(出栈时总线错误)",
    12: "STKERR(入栈时总线错误)",
    15: "BFARVALID(BFAR 有效)",
    # UFSR(bits 16-31)
    16: "UNDEFINSTR(未定义指令)",
    17: "INVSTATE(无效状态,如 EPSR.T)",
    18: "INVPC(无效 PC 加载)",
    19: "INVCP(无效协处理器)",
    24: "UNALIGNED(非对齐访问)",
    25: "DIVBYZERO(除零)",
}

# HFSR 位定义
_HFSR_BITS: dict[int, str] = {
    1: "VECTTBL(读取向量表失败)",
    30: "FORCED(强制 HardFault:由可配置 fault 升级而来,请看 CFSR)",
    31: "DEBUGEVT(调试事件触发)",
}

_ADDR_RE = re.compile(r"(?<![0-9A-Fa-f])0x([0-9A-Fa-f]{4,16})\b")
# 形如:  #0 0x080004A0  sensor_samples_read + 48   (demo_sensor.c:23)
_FRAME_RE = re.compile(
    r"#(?P<idx>\d+)\s+(?P<addr>0x[0-9A-Fa-f]+)\s+(?P<sym>[A-Za-z_]\w*)"
    r"\s*\+\s*(?P<off>\d+)\s*(?:\((?P<file>[^:\s)]+):(?P<line>\d+)\))?"
)


def _parse_hex(line: str, name: str) -> Optional[int]:
    """从一行里提取 ``NAME=0x...`` 的值。"""
    m = re.search(name + r"\s*=\s*0x([0-9A-Fa-f]+)", line, re.IGNORECASE)
    return int(m.group(1), 16) if m else None


def _decode_bits(value: int, table: dict[int, str]) -> list[str]:
    """把寄存器值按位表译义;返回置位项列表。"""
    flags = []
    for bit, desc in table.items():
        if value & (1 << bit):
            flags.append(f"bit{bit}: {desc}")
    return flags


class FaultDecoderImpl(FaultDecoder):
    """Cortex-M HardFault 解码实现。"""

    def __init__(self, elf: Optional[str] = None, addr2line: str = "arm-none-eabi-addr2line"):
        self.elf = elf
        self._addr2line = addr2line

    def decode(self, fault_dump: str) -> dict:
        """解析 fault 文本。

        返回 ``{reason, registers, stack}``:
        - reason: 一句话根因(综合各寄存器位);
        - registers: {name: {value, hex, flags:[...]}};
        - stack: [{index, addr, symbol, file, line}]。
        """
        regs: dict[str, dict] = {}
        cfsr = hfsr = None
        for line in fault_dump.splitlines():
            low = line.lower()
            for reg in ("cfsr", "hfsr", "mmfar", "bfar"):
                if reg in low:
                    val = _parse_hex(line, reg)
                    if val is None:
                        continue
                    if reg == "cfsr":
                        cfsr = val
                        regs[reg] = {"value": val, "hex": f"0x{val:08X}",
                                     "flags": _decode_bits(val, _CFSR_BITS)}
                    elif reg == "hfsr":
                        hfsr = val
                        regs[reg] = {"value": val, "hex": f"0x{val:08X}",
                                     "flags": _decode_bits(val, _HFSR_BITS)}
                    else:
                        regs[reg] = {"value": val, "hex": f"0x{val:08X}",
                                     "flags": [f"访问地址 = 0x{val:08X}"]}

        stack = self._parse_stack(fault_dump)

        reason = self._summarize(cfsr, hfsr, regs)
        return {"reason": reason, "registers": regs, "stack": stack}

    # ---------- 栈帧 ----------
    def _parse_stack(self, fault_dump: str) -> list[dict]:
        frames: list[dict] = []
        for m in _FRAME_RE.finditer(fault_dump):
            file_ = m.group("file")
            line = int(m.group("line")) if m.group("line") else None
            if file_ is None and line is None and self.elf:
                file_, line = self._addr2line_lookup(m.group("addr"))
            frames.append({
                "index": int(m.group("idx")),
                "addr": m.group("addr"),
                "symbol": m.group("sym"),
                "offset": int(m.group("off")),
                "file": file_ or "",
                "line": line,
            })
        return frames

    def _addr2line_lookup(self, addr: str) -> tuple[Optional[str], Optional[int]]:
        """用 arm-none-eabi-addr2line 把地址映射到 file:line(可选,失败忽略)。"""
        if not self.elf or not shutil.which(self._addr2line):
            return None, None
        try:
            proc = subprocess.run(
                [self._addr2line, "-e", self.elf, "-f", "-s", addr],
                capture_output=True, text=True, timeout=10.0,
                encoding="utf-8", errors="replace",
            )
        except Exception as exc:
            logger.debug("addr2line 失败:%s", exc)
            return None, None
        if proc.returncode != 0:
            return None, None
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None, None
        func, loc = lines[0], lines[1]
        if loc in ("??", "??:0", "??:?"):
            return None, None
        m = re.match(r"^(?P<f>.+):(?P<n>\d+)$", loc)
        if not m:
            return loc, None
        return m.group("f"), int(m.group("n"))

    # ---------- 根因汇总 ----------
    @staticmethod
    def _summarize(cfsr: Optional[int], hfsr: Optional[int],
                   regs: dict) -> str:
        bits: list[str] = []
        if cfsr is not None:
            bits += _decode_bits(cfsr, _CFSR_BITS)
        if hfsr is not None:
            bits += _decode_bits(hfsr, _HFSR_BITS)
        if not bits:
            if not regs:
                return "未识别到 fault 寄存器信息(可能日志不含 fault 现场)。"
            return "识别到寄存器但无已知置位,需人工进一步分析。"
        forced = hfsr is not None and (hfsr & (1 << 30))
        prefix = "由可配置 fault 升级为 HardFault(FORCED)。" if forced else ""
        return prefix + "置位项:" + ";".join(b.split(":", 1)[1].split("(")[0].strip()
                                              for b in bits)


def make_fault_decoder(config: dict) -> FaultDecoderImpl:
    """从 config 构造解码器(elf 取 flasher.firmware;addr2line 用默认工具链名)。"""
    flasher_cfg = config.get("flasher", {}) or {}
    elf = flasher_cfg.get("firmware", "") or None
    return FaultDecoderImpl(elf=elf)
