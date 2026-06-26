# AIDB — AI-First Database

### 一份设计白皮书 / RFC

| 项 | 值 |
| --- | --- |
| 状态 | Draft v0.1(征求意见) |
| 日期 | 2026-06-26 |
| 作者 | KiteFlyerX / EmbeddedAiCoder Project |
| 许可 | 随仓库 GPLv3 |
| 关联 | EmbeddedAiCoder 的上下文引擎;可独立孵化 |

---

## 0. 摘要(TL;DR)

传统数据库的根本假设是「**由人来读**」:它优化存储成本与查询延迟,以「行/文档」为单位,用 SQL 做精确匹配,输出给人看的结果集。

大模型改变了这个假设。当数据库的消费者变成 **AI(以 token 为食、有上下文窗口上限、会幻觉、需要可追溯证据)**,优化目标随之彻底改变:**从「查询效率」转向「token 经济学」——在固定 token 预算内,向 AI 传递最高密度的相关信息。**

**AIDB** 是为这一新假设设计的数据库范式。本文定义其四个第一性抽象(**Atom / Lens / Intent Query / Token Budget**)、两个增强(**Provenance / Diff Stream**)、查询协议、数据模型、分层架构与落地路径。

> 核心主张:**范式是新的,引擎可复用**。AIDB 的创新在「抽象层与查询协议」,不必重写存储引擎;但为极致 token 效率,可为 Atom 设计专用序列化格式。

---

## 1. 动机

在 AI 辅助编程(尤其嵌入式/STM32)中,核心瓶颈不是「模型不够强」,而是**上下文供给**:

- 整个工程(HAL 库动辄数万行)远超上下文窗口;
- 每次把无关代码喂给模型 = 烧钱(token)+ 稀释注意力;
- 模型需要的是「**与当前问题相关的、带定位的、密度最高的片段**」,而非整库。

现有方案(向量库 / 全文检索 / SCIP 索引)各自解决一部分,但都是**为人或为单一检索模式**设计的组件,**没有一个把「AI 作为读者」作为第一性原则**。AIDB 填补这一空白。

---

## 2. 核心论点:AI 改变了「读者」假设

| 维度 | 传统 DB(为人) | AIDB(为 AI) |
| --- | --- | --- |
| 优化目标 | 存储成本、查询延迟 | **token 经济学**(信息密度/token) |
| 基本单元 | 行 / 文档 / KV | **语义原子**(Atom) |
| 查询方式 | 精确匹配、SQL join | **意图 + 多跳规划** |
| 输出形态 | 结果集(表格) | **prompt-ready token 包** |
| 交互模型 | 无状态、一次性 | **有状态、迭代探索** |
| 可信度假设 | 人能自行判断 | **自带证据指针**(防幻觉) |
| 一等公民 | 数据完整性(ACID) | **变更流 + 可寻址性** |

每一行都是范式级差异,不是工程包装。

---

## 3. 范式定义

> **AIDB 是一种以「语义原子」为基本单元、以「token 预算」为一等约束、以「意图查询」为接口、以「prompt-ready 包」为输出的数据库范式。**

它不是某一种存储引擎,而是一组**抽象 + 协议**。任何实现这套抽象的系统都是 AIDB 实例。

---

## 4. 四大核心抽象

### 4.1 Atom(语义原子)—— 最小可独立消费单元

Atom 不是行,而是一个**自洽的语义实体**:一个函数、一个类型、一个配置项、一个事实。每个 Atom 携带其完整身份与多种分辨率。

**Schema:**
```json
{
  "uri": "atom://stm32/uart_send",
  "kind": "function",
  "lens": { "...": "见 4.2" },
  "provenance": "Core/Src/uart.c:42",
  "embed": [0.012, -0.033, "..."],
  "version": "a1b2c3"
}
```

- **URI 寻址**:`atom://<scope>/<path>`,稳定、可引用。AI 可在回答中写「见 `atom://stm32/uart_send`」。
- **kind**:function / type / macro / config / fact / register …,决定可用 Lens。

### 4.2 Lens(多分辨率镜头)—— 同一 Atom 的多视图

一个 Atom 同时存储多个分辨率投影,AI 按需切换,**先廉价概览定位、再付费展开**:

| Lens | 分辨率 | 大致 token | 用途 |
| --- | --- | --- | --- |
| `overview` | 一句话摘要 | ~20 | 概览/定位 |
| `signature` | 函数签名/类型声明 | ~50 | 接口判断 |
| `body` | 完整实现 | ~200+ | 改码/深入 |
| `callgraph` | 调用/被调/引用 | ~50 | 关系定位 |
| `config` | 关联配置(.ioc/寄存器) | ~30 | 外设诊断 |

> 这是「渐进式披露(progressive disclosure)」的原生化:传统库要 AI 自己决定读多少,AIDB 把分辨率做成一等维度。

### 4.3 Intent Query(意图查询)—— 声明式,内置规划

AI 不写 `SELECT`,而是声明**意图**,AIDB 内部自动规划多跳检索并综合:

```
POST /query
{
  "intent": "定位串口初始化失败的可能原因",
  "budget_tokens": 2000,
  "scope": "atom://stm32/**",
  "hints": { "symbols": ["MX_USART2_UART_Init"], "error": "HAL_ERROR" }
}
```

DB 内部规划(示例):`uart_init 原子 → 取 signature+config → 找调用方 → 关联 .ioc 波特率配置 → 检查 Error_Handler 路径`。

- **规划器**:M1 用规则图遍历;M2 可选 LLM 辅助规划。
- 输出是综合后的上下文包,而非原始命中行。

### 4.4 Token Budget(预算契约)—— 一等约束

每次查询携带 token 预算,AIDB **在预算内最大化信息增益**。这是传统 DB 完全没有的维度。

**调度算法(贪心背包变体):**
1. 多路召回候选 Atom × Lens 组合,每个标注 `(gain, cost_tokens)`;
2. 按优先级分桶:**精确命中 > 关系链 > 语义召回**;
3. 桶内按 `gain / cost` 降序贪心选取,直到预算用尽;
4. **分辨率降级**:若超预算,将已选 Atom 的 Lens 从 `body`→`signature`→`overview` 逐级降级,腾出预算纳入更多相关 Atom。

结果:给定 2000 token,模型拿到的是「**8 个高度相关函数的签名 + 2 个关键函数体**」,而非「1 个无关大文件的全文」。

### 4.5 增强:Provenance(证据指针)

每条返回事实附带 `provenance`(文件:行 / 寄存器地址 / 配置路径)。模型生成答案时引用它,**显著降低幻觉**,并支持回查验证。

### 4.6 增强:Diff Stream(变更流)

AI 最常问「这次改了什么 / 上一轮为何失败」。AIDB 原生维护版本与变更:查询可带 `since=<version>`,只返回变更的 Atom。闭环调试天然受益。

---

## 5. 查询协议(Query Protocol)

一套面向 AI 客户端的、与传输无关的协议(可走 stdio / HTTP / **MCP**)。

### 5.1 请求
```json
{
  "intent": "<自然语言意图>",
  "budget_tokens": 2000,
  "scope": "atom://stm32/**",
  "hints": { "symbols": [], "errors": [], "files": [] },
  "since": "<可选版本,启用 Diff Stream>",
  "lens_pref": "auto"
}
```

### 5.2 响应(prompt-ready token 包)
```json
{
  "context_pack": "<已按 token 预算打包、可直接插入 prompt 的文本>",
  "tokens_used": 1840,
  "atoms": ["atom://stm32/MX_USART2_UART_Init", "..."],
  "provenance": { "atom://...": "Core/Src/main.c:88" },
  "next_suggestions": ["展开 uart_send 函数体", "查看 USART2 中断处理"]
}
```

- `context_pack` 是**可直接喂模型**的文本,带寻址标签与关系摘要;
- `next_suggestions` 支撑**迭代探索**(AI 下一步可下钻)。

### 5.3 寻址方案
```
atom://<scope>/<path>[#<lens>][@<version>]
atom://stm32/uart_send#body@v3
```
稳定、可缓存、可对比版本。

### 5.4 典型交互(迭代探索)
```
① query("串口为何不通", budget=500)   → 返回 overview 级 8 个 Atom
② query("展开 MX_USART2_UART_Init", budget=1500) → 返回其 body + 配置
③ query("谁调用了它", budget=500)     → 返回 callgraph
```

---

## 6. 数据模型与索引

- **存储**:**SQLite**(单文件 `.aidb/index.db`,随工程走、零服务)。
  - 表:`atoms(uri, kind, provenance, version, embed_blob)`、`lenses(uri, lens, content, tokens)`、`edges(src, rel, dst)`(调用/引用图)、`meta(key, val)`。
- **符号解析**:`ctags`(MVP,快)→ `libclang`(精确,理解宏/类型)。
- **语义索引**:`SCIP/LSIF` 作为可选高质量前端。
- **检索**:多路召回(精确符号 + 图遍历 N 跳 + 向量相似 + 关键字),由 Intent 规划器编排。

---

## 7. Token 经济学(核心指标)

AIDB 引入专属度量,替代传统 DB 的 QPS/延迟:

| 指标 | 定义 |
| --- | --- |
| **信息密度** | 相关信息量 / token |
| **预算达成率** | 实际返回相关信息 / 预算内最优 |
| **命中率** | 返回 Atom 中被 AI 实际采纳的比例 |
| **幻觉关联率** | 返回但无关的比例(越低越好) |

这些指标可直接作为检索质量回归基准。

---

## 8. 分层架构(「新」的边界)

| 层 | 是否新 | 内容 |
| --- | --- | --- |
| **L3 范式层** | ✅ 全新(核心 IP) | Atom / Lens / Intent / Budget 抽象 |
| **L2 协议层** | ✅ 新 | 查询协议、prompt-ready 序列化、MCP 暴露 |
| **L1 实现层** | 🔁 可复用 | SQLite / 图索引 / 向量;日后可换专用 `.aidb` 格式 |
| **L0 引擎层** | 🔁 复用 | 除非追求学术级创新,后置 |

**结论**:把创新投入 L3/L2;L1/L0 站在巨人肩膀。日后为极致 token 效率,可为 Atom 设计专用二进制序列化(这是可选的真创新点)。

---

## 9. 落地路径

| 里程碑 | 交付 | 验证 |
| --- | --- | --- |
| **M0** | 本 RFC(范式定稿) | 评审通过 ✓ |
| **M1** | Atom + Lens + 寻址;ctags 解析 C → SQLite;**符号精确查询**(无向量) | 给定符号返回带定位的 Lens,token 显著低于喂全文 |
| **M2** | **Intent 规划器(规则版)+ Token Budget 调度器** | 预算约束下召回质量达标 |
| **M3** | 向量召回 + Provenance + Diff Stream | 多路召回融合,幻觉关联率下降 |
| **M4** | **MCP server** 暴露;独立孵化评估;`.aidb` 格式调研 | Claude/Codex/Cursor 共用一份索引 |

---

## 10. 非目标(边界)

- **不**做通用 OLTP/OLAP,不取代关系数据库;
- **不**追求高并发事务(面向单机 AI 工具);
- 第一版**不**做分布式 / 多租户;
- **不**重新实现编程语言解析器(复用 ctags/libclang/SCIP)。

---

## 11. 开放问题

1. **命名**:AIDB 是否最终定名?(备选:TokenBase / SemanticStore)
2. **Atom 粒度**:函数级为默认;是否支持语句级 / 可配置?
3. **Intent 规划**:规则优先 vs LLM 辅助,何时引入后者?
4. **增量触发**:文件保存触发 / 定时 / 手动?
5. **跨语言**:C/嵌入式先行,Python/Rust 何时支持?
6. **是否独立 repo**:作为 EmbeddedAiCoder 子模块,还是独立孵化?

---

## 12. 相关工作

- **SCIP / LSIF**(Sourcegraph):代码语义索引的工业标准,AIDB 的 Atom/Lens 与之理念相通,但 AIDB 进一步引入 token 预算与意图查询。
- **GraphRAG**:图 + 向量混合检索;AIDB 的多路召回受其启发。
- **向量库**(Chroma / Qdrant / FAISS / sqlite-vec):作为 L1 实现选项。
- **MCP(Model Context Protocol)**:AIDB 的 L2 协议可经 MCP 暴露,服务多 AI 客户端。
- **LSP**:面向 IDE/人;AIDB 面向 AI,目标读者不同。

---

## 13. 与 EmbeddedAiCoder 的关系 / 独立孵化

- **短期**:作为 EmbeddedAiCoder 的上下文引擎,支撑 F-07(源码上下文检索)与 F-23(知识库),直接降低 AI 改码的 token 成本。
- **中期**:封装为 **MCP server**,让 Claude Opus / Codex / Claude Code **共用一份索引**。
- **长期**:AIDB 的价值**可能大于** EmbeddedAiCoder 本身,具备独立开源与标准化(`.aidb` 格式)的潜力,届时拆分为独立仓库与项目。

---

*本 RFC 欢迎评审与迭代。后续修订以版本号递增记录于本表。*
