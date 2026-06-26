# EmbeddedAiCoder

> STM32 程序编码自动化 **桌面工具** —— 采集板子的串口/RTT 日志与 HardFault 现场,回喂给 AI 自动分析问题、编写/修改 C 代码,并自动编译、烧录、复采验证,形成闭环。提供 Fluent Design(Win11 风格)可视化界面。

## 项目简介(STM32 + 桌面 GUI)

EmbeddedAiCoder 接管 STM32 开发中最耗时的「看日志 / 解码 fault → 定位 → 改码」循环:

- **采集**:UART 串口 / SWD·RTT 实时日志,自动解码 Cortex-M HardFault
- **分析**:过滤关键日志,结合 HAL 源码 / .ioc 构造上下文
- **编码**:回喂给 AI,诊断问题并生成 / 修改 C 代码
- **闭环**:arm-none-eabi-gcc 构建 → pyOCD 烧录 → 复采验证,迭代直至解决
- **界面**:PyQt-Fluent-Widgets 桌面应用,实时日志 / AI diff 预览 / 一键启停,全程可视化、可介入

## 技术栈

- **GUI**:PySide6 + [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)(Fluent Design)
- 采集:`pyserial`(UART) + `pyOCD`(RTT/SWD)
- 烧录:**pyOCD**(默认)/ STM32_Programmer_CLI / OpenOCD(可切换)
- 构建:arm-none-eabi-gcc + Make/CMake/Ninja
- 故障定位:addr2line / objdump
- AI:大模型 SDK(Claude / OpenAI),可扩展本地模型

## 文档

- 📄 [需求文档 REQUIREMENTS](docs/REQUIREMENTS.md) —— 完整需求、UI 设计、模块划分、里程碑与开放问题
- 🧠 [AIDB 设计白皮书(RFC)](docs/AIDB-RFC.md) —— **核心 IP**:专为 AI 消费的数据库范式(Atom / Lens / Intent Query / Token Budget),让 Claude Opus / Codex 省 token 快速定位代码

## 授权

本项目为**开源项目,采用 [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html) 协议**。UI 库 PyQt-Fluent-Widgets 同为 GPLv3,二者兼容,免费使用;Qt 绑定选 PySide6(LGPL)。详见需求文档第〇节。

## 当前状态

需求 v0.3(桌面可视化版)。✅ 已确认开源(GPLv3)。剩余开放问题:目标芯片 / 构建方式 / 日志通道 / 调试器(见需求文档第十一节),确认后进入 M1(引擎 MVP)与 M2(Fluent GUI)实现。
