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
- 🧠 **STM32_TokenBase**(已独立为同级项目 `../STM32_TokenBase`):专为 AI 消费的数据库范式(Atom / Lens / Intent Query / Token Budget);本项目的源码上下文检索(F-07)将基于它。见 [RFC](../STM32_TokenBase/docs/RFC.md)

## 授权

本项目为**开源项目,采用 [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html) 协议**。UI 库 PyQt-Fluent-Widgets 同为 GPLv3,二者兼容,免费使用;Qt 绑定选 PySide6(LGPL)。详见需求文档第〇节。

## 快速开始

```bash
# 1) 安装依赖(建议虚拟环境)
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt

# 2) 启动 Fluent 桌面应用
python -m embedded_ai_coder
#    → 控制台勾选「演示模式」(默认开)→ 「启动闭环」即可无硬件/无 Key 跑通完整闭环
#    (演示模式:回放 examples/demo_log.txt + mock 补丁 + dry-run 烧录)
#    关闭演示模式则走真实串口 + 真实 AI Key + pyOCD(由 config/*.yaml 驱动)

# 3) CLI(无 GUI,适合脚本/CI)
python -m embedded_ai_coder run --dry-run --demo-log examples/demo_log.txt \
       --project examples/demo_project --max-iter 2
python -m embedded_ai_coder collect --port COM3        # 只看串口日志
python -m embedded_ai_coder index examples/demo_project  # 为工程建 tokenbase 索引
```

> 个人配置与密钥放 `config/local.yaml`(已 gitignore);界面「设置」页可直接编辑并保存到 local.yaml。

## 当前状态

**M1(引擎 MVP)+ M2(Fluent GUI)已实现并验证。** ✅ 开源(GPLv3)。

- **后台引擎**:采集(pyserial 串口 + 文件回放)→ 过滤 → AI 诊断/改码(OpenAI 兼容 HTTP + mock)→
  代码回写(tree-sitter/正则定位 + .bak 备份 + 回滚)→ 构建 → pyOCD 烧录 → 复采;HardFault 解码器
  (CFSR/HFSR/MMFAR/BFAR + 栈帧);tokenbase 上下文桥接(F-07)。CLI 与 GUI 共用同一引擎。
- **Fluent 桌面应用(PySide6-Fluent-Widgets)**:8 页全部接通引擎——
  ①控制台(启停闭环/演示模式/实时日志/AI 任务)、②日志监控(高亮+过滤)、③故障诊断(解码+栈帧表)、
  ④AI 编码(diff 预览+应用/回滚)、⑤构建烧录、⑥工程(源码树)、⑦设置(存 local.yaml)、⑧历史(导出 JSON)。
  闭环在后台 QThread 运行,支持暂停/恢复/停止,关闭窗口优雅退出。

剩余开放问题:目标芯片 / 构建方式 / 日志通道 / 调试器等真实硬件相关项(见需求文档第十一节),
按 `config/local.yaml` 配置后即可对接;RTT 采集、烧录后端切换、多模型、.ioc 联动等属 M3/M4 增强。
