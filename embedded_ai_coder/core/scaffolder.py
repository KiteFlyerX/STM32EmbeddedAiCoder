"""
工程骨架生成:Scaffolder(F-25 一键生成整项目)。

基于【用户指定的官方 SDK 包路径】+【芯片型号】,从中抽取启动文件 / system /
HAL 配置模板 / 链接脚本,并生成 Makefile + 最小 main/it/msp 骨架,拼出一个
**最小可编译的 STM32 GCC 工程**。纯确定性、无 AI —— 让生成→编译→烧录链路
有「可编译底座」(这是 implement_and_deploy / 五阶段编排的硬前提)。

设计要点:
- SDK 的 Drivers 整目录**不复制**(几万文件太重);Makefile 用 ``SDK_ROOT`` 变量
  直接 ``-I`` / ``C_SOURCES +=`` 指向 SDK 内的 HAL/CMSIS。
- 链接脚本 ``.ld``:Templates/gcc 下**没有**,取自 ``Projects/NUCLEO-*/.../STM32CubeIDE/``
  (STM32CubeIDE 导出的 GCC ld,如 STM32G0B1RETX_FLASH.ld);找不到则用内置默认布局降级。
- 落盘全部经 ``CoderImpl.write_files`` —— 复用其自动建目录 / 备份 / 防穿越 / 回滚登记。
- 与 builder 衔接:apply() 后由编排器把 ``build_command`` 置为 ``"make"``,
  即可复用 MakeBuilder + 编译自愈,builder 零改动。

SPDX-License-Identifier: GPL-3.0-or-later
Copyright (c) KiteFlyerX
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# 芯片系列 → (cpu, fpu, float_abi)。fpu=None 表示该核无 FPU(M0+/M3)。
CHIP_ABI: dict[str, tuple[str, Optional[str], str]] = {
    "STM32C0": ("cortex-m0plus", None, "soft"),
    "STM32F0": ("cortex-m0", None, "soft"),
    "STM32F1": ("cortex-m3", None, "soft"),
    "STM32F2": ("cortex-m3", None, "soft"),
    "STM32F3": ("cortex-m4", "fpv4-sp-d16", "hard"),
    "STM32F4": ("cortex-m4", "fpv4-sp-d16", "hard"),
    "STM32F7": ("cortex-m7", "fpv5-d16", "hard"),
    "STM32G0": ("cortex-m0plus", None, "soft"),
    "STM32G4": ("cortex-m4", "fpv4-sp-d16", "hard"),
    "STM32H7": ("cortex-m7", "fpv5-d16", "hard"),
    "STM32L0": ("cortex-m0plus", None, "soft"),
    "STM32L1": ("cortex-m3", None, "soft"),
    "STM32L4": ("cortex-m4", "fpv4-sp-d16", "hard"),
    "STM32L5": ("cortex-m33", "fpv5-sp-d16", "hard"),
    "STM32U5": ("cortex-m33", "fpv5-sp-d16", "hard"),
    "STM32WB": ("cortex-m4", "fpv4-sp-d16", "hard"),
    "STM32WL": ("cortex-m4", "fpv4-sp-d16", "hard"),
}


@dataclass
class ScaffoldPlan:
    """scaffold 的抽取 + 生成计划(可独立审查,apply 时落盘)。"""
    chip: str                       # 归一化大写,如 STM32G0B1
    chip_lower: str                 # stm32g0b1
    family: str                     # STM32G0(chip 前 7 字符)
    mcu_family: str                 # STM32G0xx
    mcu_family_lower: str           # stm32g0xx(文件名前缀)
    chip_define: str                # STM32G0B1xx(HAL 的 -D 宏)
    abi: dict                       # {cpu, fpu, float_abi}
    sdk_root: str                   # SDK 绝对路径(正斜杠)
    sdk_paths: dict = field(default_factory=dict)   # 探测到的关键 SDK 文件
    copy_files: list[dict] = field(default_factory=list)  # [{from, to}]
    gen_files: list[dict] = field(default_factory=list)   # [{path, content}]
    hal_modules: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Scaffolder:
    """从官方 SDK 拼出最小可编译 STM32 GCC 工程骨架。"""

    def __init__(self, sdk_root: str | Path, project_root: str | Path):
        self.sdk_root = Path(sdk_root).expanduser().resolve() if sdk_root else Path()
        self.root = Path(project_root).expanduser().resolve()

    # ---------- 主入口 ----------
    def plan(self, chip: str, hal_modules: Optional[list[str]] = None) -> ScaffoldPlan:
        """生成 scaffold 计划(只读 SDK 探测,不落盘)。"""
        chip = (chip or "").strip().upper().replace("-", "")
        if not chip:
            raise ValueError("scaffold 需要 chip(芯片型号,如 STM32G0B1)")
        family = chip[:7]                       # STM32G0B1 -> STM32G0
        mcu_family = family + "xx"              # STM32G0xx
        mcu_family_lower = mcu_family.lower()   # stm32g0xx
        chip_lower = chip.lower()               # stm32g0b1
        chip_define = f"{chip}xx"               # STM32G0B1xx

        abi = self._abi_for(family)
        sdk_paths = self._probe_sdk(mcu_family, chip, chip_lower)
        copy_files = self._plan_copy_files(sdk_paths, mcu_family_lower)
        gen_files = self._plan_gen_files(
            chip=chip, mcu_family_lower=mcu_family_lower, abi=abi,
            startup_name=Path(sdk_paths.get("startup", "startup.s")).name,
            ld_name=Path(sdk_paths.get("ld", f"{chip}TX_FLASH.ld")).name,
        )
        return ScaffoldPlan(
            chip=chip, chip_lower=chip_lower, family=family, mcu_family=mcu_family,
            mcu_family_lower=mcu_family_lower, chip_define=chip_define, abi=abi,
            sdk_root=str(self.sdk_root).replace("\\", "/"),
            sdk_paths=sdk_paths, copy_files=copy_files, gen_files=gen_files,
            hal_modules=list(hal_modules or []), notes=[],
        )

    def apply(self, plan: ScaffoldPlan, coder: Any) -> dict:
        """落盘:copy_files(读 SDK 内容)+ gen_files,统一经 coder.write_files。"""
        files: list[dict] = []
        for cf in plan.copy_files:
            src = Path(cf["from"])
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                plan.notes.append(f"读取 SDK 文件 {src.name} 失败:{exc}")
                logger.warning("scaffold 读取 %s 失败:%s", src, exc)
                continue
            files.append({"path": cf["to"], "content": content,
                          "reason": f"从 SDK 复制 {src.name}"})
        files.extend(plan.gen_files)
        written, skipped = coder.write_files(files)
        logger.info("scaffold 落盘:写入 %d / 跳过 %d(copy %d, gen %d)",
                    written, skipped, len(plan.copy_files), len(plan.gen_files))
        return {"written": written, "skipped": skipped,
                "copied": len(plan.copy_files), "gen": len(plan.gen_files),
                "notes": plan.notes}

    # ---------- ABI ----------
    def _abi_for(self, family: str) -> dict:
        entry = CHIP_ABI.get(family)
        if entry is None:
            logger.warning("未知芯片系列 %s,按 Cortex-M0+/soft 兜底", family)
            entry = ("cortex-m0plus", None, "soft")
        cpu, fpu, float_abi = entry
        return {"cpu": cpu, "fpu": fpu, "float_abi": float_abi}

    # ---------- SDK 探测 ----------
    def _probe_sdk(self, mcu_family: str, chip: str, chip_lower: str) -> dict:
        """在 SDK 里定位 startup / system / hal_conf / ld。找不到记 None。"""
        found: dict[str, Optional[str]] = {}
        if not self.sdk_root or not self.sdk_root.exists():
            logger.error("scaffold:SDK 路径不存在或未配置:%s", self.sdk_root)
            return found

        dev = self.sdk_root / "Drivers" / "CMSIS" / "Device" / "ST" / mcu_family
        hal = self.sdk_root / "Drivers" / f"{mcu_family}_HAL_Driver"

        # startup:Templates/gcc/startup_{chip_lower}xx.s
        startup = dev / "Source" / "Templates" / "gcc" / f"startup_{chip_lower}xx.s"
        found["startup"] = str(startup) if startup.exists() else self._rglob_first(
            dev, f"startup_{chip_lower}xx.s")

        # system:Templates/system_{mcu_family_lower}.c
        sys_name = f"system_{mcu_family.lower()}.c"
        system = dev / "Source" / "Templates" / sys_name
        found["system"] = str(system) if system.exists() else self._rglob_first(
            dev, sys_name)

        # HAL conf 模板:{mcu_family_lower}_hal_conf_template.h
        conf_name = f"{mcu_family.lower()}_hal_conf_template.h"
        conf = hal / "Inc" / conf_name
        found["hal_conf"] = str(conf) if conf.exists() else self._rglob_first(hal, conf_name)

        # .ld:Templates/gcc 没有 → 取 Projects/NUCLEO-* 下的 STM32CubeIDE ld
        ld_glob = f"{chip}*FLASH.ld"
        projects = self.sdk_root / "Projects"
        found["ld"] = self._rglob_first(projects, ld_glob)
        return {k: v for k, v in found.items() if v}

    @staticmethod
    def _rglob_first(root: Path, pattern: str) -> Optional[str]:
        if not root or not root.exists():
            return None
        try:
            for p in root.rglob(pattern):
                return str(p)
        except Exception:  # noqa: BLE001
            return None
        return None

    # ---------- copy 计划 ----------
    def _plan_copy_files(self, sdk_paths: dict, mcu_family_lower: str) -> list[dict]:
        copies: list[dict] = []
        if startup := sdk_paths.get("startup"):
            copies.append({"from": startup, "to": f"Core/Startup/{Path(startup).name}"})
        if system := sdk_paths.get("system"):
            copies.append({"from": system, "to": f"Core/Src/{Path(system).name}"})
        if hal_conf := sdk_paths.get("hal_conf"):
            # 模板复制为正式 hal_conf.h(MVP 不裁剪,模板默认启用常用模块即可编译)
            copies.append({"from": hal_conf,
                           "to": f"Core/Inc/{mcu_family_lower}_hal_conf.h"})
        if ld := sdk_paths.get("ld"):
            copies.append({"from": ld, "to": Path(ld).name})   # 工程根
        return copies

    # ---------- gen 计划 ----------
    def _plan_gen_files(self, *, chip: str, mcu_family_lower: str, abi: dict,
                        startup_name: str, ld_name: str) -> list[dict]:
        target = self.root.name or "firmware"
        return [
            {"path": "Makefile", "content": self._makefile(
                target=target, chip=chip, mcu_family_lower=mcu_family_lower,
                abi=abi, startup_name=startup_name, ld_name=ld_name),
             "reason": "GCC 构建脚本(由芯片型号派生)"},
            {"path": "Core/Src/main.c", "content": self._main_c(
                chip=chip, mcu_family_lower=mcu_family_lower),
             "reason": "main + SystemClock_Config 骨架(阶段④整文件覆盖)"},
            {"path": f"Core/Src/{mcu_family_lower}_it.c",
             "content": self._it_c(mcu_family_lower=mcu_family_lower),
             "reason": "中断处理(SysTick→HAL_IncTick 等)"},
            {"path": f"Core/Src/{mcu_family_lower}_hal_msp.c",
             "content": self._msp_c(mcu_family_lower=mcu_family_lower),
             "reason": "HAL MSP 初始化骨架(阶段③按引脚表填充)"},
            {"path": "Core/Inc/main.h",
             "content": self._main_h(mcu_family_lower=mcu_family_lower),
             "reason": "主头文件"},
        ]

    # ---------- 模板 ----------
    def _makefile(self, *, target: str, chip: str, mcu_family_lower: str,
                  abi: dict, startup_name: str, ld_name: str) -> str:
        family = chip[:7]                      # STM32G0
        mcu_family = family + "xx"             # STM32G0xx
        fpu_line = f"-mfpu={abi['fpu']}" if abi.get("fpu") else ""
        sdk = str(self.sdk_root).replace("\\", "/")
        TAB = "\t"
        return f"""###########################################################################
# 由 EmbeddedAiCoder scaffold 自动生成 —— 勿手动改文件名/路径结构
# 目标芯片 : {chip}  ({abi['cpu']}, float-abi={abi['float_abi']})
# 官方 SDK  : {sdk}
###########################################################################
TARGET   ?= {target}
SDK_ROOT ?= {sdk}

PREFIX = arm-none-eabi-
CC      = $(PREFIX)gcc
AS      = $(PREFIX)gcc -x assembler-with-cpp
CP      = $(PREFIX)objcopy
HEX     = $(CP) -O ihex
BIN     = $(CP) -O binary -S
SZ      = $(PREFIX)size

CPU      = -mcpu={abi['cpu']}
FPU      = {fpu_line}
FLOAT-ABI = -mfloat-abi={abi['float_abi']}
MCU      = $(CPU) -mthumb $(FPU) $(FLOAT-ABI)

C_DEFS = -DUSE_HAL_DRIVER -D{chip}xx

C_INCLUDES = \\
-ICore/Inc \\
-I$(SDK_ROOT)/Drivers/{mcu_family}_HAL_Driver/Inc \\
-I$(SDK_ROOT)/Drivers/{mcu_family}_HAL_Driver/Inc/Legacy \\
-I$(SDK_ROOT)/Drivers/CMSIS/Device/ST/{mcu_family}/Include \\
-I$(SDK_ROOT)/Drivers/CMSIS/Device/ST/{mcu_family}/Source/Templates \\
-I$(SDK_ROOT)/Drivers/CMSIS/Include

AS_SOURCES = \\
Core/Startup/{startup_name}

# 工程自有源 + SDK 全部 HAL 源(wildcard:HAL Conf 模板默认启用常用模块,未启用的 .c 因 #ifdef 空编译)
C_SOURCES = \\
Core/Src/main.c \\
Core/Src/{mcu_family_lower}_it.c \\
Core/Src/{mcu_family_lower}_hal_msp.c \\
Core/Src/system_{mcu_family_lower}.c \\
$(wildcard $(SDK_ROOT)/Drivers/{mcu_family}_HAL_Driver/Src/{mcu_family_lower}_hal_*.c)

OPT     = -Og -g3 -Wall -fdata-sections -ffunction-sections
CFLAGS  = $(MCU) $(C_DEFS) $(C_INCLUDES) $(OPT) -std=c11
ASFLAGS = $(MCU) $(C_INCLUDES)

LDSCRIPT = {ld_name}
LIBS     = -lc -lm -lnosys
LDFLAGS  = $(MCU) -specs=nano.specs -specs=nosys.specs -T$(LDSCRIPT) $(LIBS) \\
-Wl,-Map=$(BUILD_DIR)/$(TARGET).map,--cref -Wl,--gc-sections

BUILD_DIR = build

OBJECTS = $(addprefix $(BUILD_DIR)/,$(notdir $(C_SOURCES:.c=.o)))
vpath %.c $(sort $(dir $(C_SOURCES)))
OBJECTS += $(addprefix $(BUILD_DIR)/,$(notdir $(AS_SOURCES:.s=.o)))
vpath %.s $(sort $(dir $(AS_SOURCES)))

$(BUILD_DIR)/%.o: %.c Makefile | $(BUILD_DIR)
{TAB}$(CC) -c $(CFLAGS) -Wa,-a,-ad,-alms=$(BUILD_DIR)/$(notdir $(<:.c=.lst)) $< -o $@

$(BUILD_DIR)/%.o: %.s Makefile | $(BUILD_DIR)
{TAB}$(AS) -c $(CFLAGS) $< -o $@

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) Makefile
{TAB}$(CC) $(OBJECTS) $(LDFLAGS) -o $@
{TAB}$(SZ) $@

$(BUILD_DIR)/$(TARGET).hex: $(BUILD_DIR)/$(TARGET).elf
{TAB}$(HEX) $< $@

$(BUILD_DIR)/$(TARGET).bin: $(BUILD_DIR)/$(TARGET).elf
{TAB}$(BIN) $< $@

$(BUILD_DIR):
{TAB}mkdir -p $@

all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).hex $(BUILD_DIR)/$(TARGET).bin

clean:
{TAB}-rm -fR $(BUILD_DIR)

.PHONY: all clean
"""

    def _main_c(self, *, chip: str, mcu_family_lower: str) -> str:
        return f"""/* 由 EmbeddedAiCoder scaffold 生成:{chip} 最小骨架。
 * 阶段④(状态机集成)会整文件覆盖本文件,补全时钟树与主循环调度。 */
#include "main.h"

void SystemClock_Config(void);

int main(void)
{{
  HAL_Init();
  SystemClock_Config();

  /* USER CODE BEGIN 2 */
  /* TODO: 各模块 MX_*_Init() 由阶段④集成时补充 */
  /* USER CODE END 2 */

  while (1)
  {{
    /* USER CODE BEGIN WHILE */
    /* TODO: 状态机调度 fsm_tick() 由阶段④集成 */
    /* USER CODE END WHILE */
  }}
}}

/* HSI 默认时钟(可编可跑;阶段④按原理图时钟树覆盖) */
void SystemClock_Config(void)
{{
  RCC_OscInitTypeDef RCC_OscInitStruct = {{0}};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {{0}};
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {{ Error_Handler(); }}
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK
                              | RCC_CLOCKTYPE_PCLK1;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK)
  {{ Error_Handler(); }}
}}

void Error_Handler(void)
{{
  __disable_irq();
  while (1) {{}}
}}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line) {{ (void)file; (void)line; while (1) {{}} }}
#endif
"""

    def _it_c(self, *, mcu_family_lower: str) -> str:
        return f"""/* 由 EmbeddedAiCoder scaffold 生成:异常/中断处理。 */
#include "main.h"

void NMI_Handler(void)            {{}}
void HardFault_Handler(void)      {{ while (1) {{}} }}
void SVC_Handler(void)            {{}}
void PendSV_Handler(void)         {{}}
void SysTick_Handler(void)        {{ HAL_IncTick(); }}
"""

    def _msp_c(self, *, mcu_family_lower: str) -> str:
        return f"""/* 由 EmbeddedAiCoder scaffold 生成:HAL MSP 初始化骨架。
 * 阶段③按引脚表填充各外设的 GPIO/时钟/NVIC/复用配置(HAL_xxx_MspInit)。 */
#include "main.h"

void HAL_MspInit(void)
{{
  /* USER CODE BEGIN 0 */
  /* TODO: 全局中断优先级分组 / 低功耗配置 */
  /* USER CODE END 0 */
}}
"""

    def _main_h(self, *, mcu_family_lower: str) -> str:
        return f"""#ifndef __MAIN_H
#define __MAIN_H

#include "{mcu_family_lower}_hal.h"

void Error_Handler(void);

#endif /* __MAIN_H */
"""


def make_scaffolder(config: dict) -> Scaffolder:
    """工厂:从 config 的 project.sdk_path / project.root 构造 Scaffolder。"""
    proj = config.get("project", {}) or {}
    return Scaffolder(
        sdk_root=proj.get("sdk_path", "") or "",
        project_root=proj.get("root", ".") or ".",
    )
