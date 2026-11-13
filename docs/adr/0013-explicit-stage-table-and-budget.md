# ADR 0013 · 用一张状态表描述流水线，并给它一个墙钟预算

- 状态：已采纳
- 日期：2026-11-13
- 相关：[0011 对照实验优先于归因推断](0011-controlled-experiment-over-inference.md)、[0007 生成前可行性判定](0007-preflight-feasibility.md)

## 背景

流水线原本是「六个函数按顺序调用 + 一个 while 修复循环」，读代码能看懂，但有两个问题：

1. **图只存在于文档里**。`docs/architecture.md` 有一张手绘 Mermaid，`README.md` 有一句文字描述。改代码不改图，没人会发现——这类漂移不会报错，只会让文档越来越不可信。
2. **修复循环只有次数上限，没有时间上限**。`max_repairs=1` 意味着最多两次生成；但每次生成内部还有 3 次 HTTP 尝试，每次最长 `llm_timeout`（默认 30s）加退避。最坏墙钟是 `(1+1) × 3 × 30 + 3 = 183s`。也就是说，「只重试一次」的循环可以把一个 HTTP 请求拖到 3 分钟，而任何计数上限都拦不住它。

外部建议里还有第三种做法：把它改成一个通用状态机（Node / Edge / Tool Registry / Max Retries 框架）。这里没有采纳，理由见下。

## 决策

### 1. `STAGES` 是唯一事实源

`pipeline.py` 里用 `STAGES: tuple[Stage, ...]` 声明每个节点：名称、标题、输入、输出、成功去往、失败去往、是否可重试、一句话说明。`Stage` 是 frozen dataclass。

`scripts/build_pipeline_doc.py` 从这张表**生成** `docs/pipeline-contract.md`：Mermaid 图、节点契约表、每个节点的输入/输出 JSON、最坏延迟计算。`--check` 比对生成结果与仓库里的文件，进 CI。

### 2. 运行时事件必须来自这张表

文档由代码生成，只解决「文档 vs 表」；「表 vs 实际行为」由测试解决：`tests/test_pipeline_contract.py` 跑通五条路径（正常、修复、不可满足、provider 失败、预算耗尽），断言 trace 里每个事件名要么是声明的节点，要么是 `repair_N` / `budget` / `error`。加一个步骤不声明，测试红。

同时断言每个 `on_success` / `on_failure` 都指向已声明节点或终态——拼错一个字符串就会多出一条永远走不到的死路，这种错误在运行时不报错，只会让某条降级路径永远不生效。

### 3. 加墙钟预算，并且降级而不是丢弃

`Settings.pipeline_budget_s`（`SHOTPLAN_PIPELINE_BUDGET_S`，默认 60s，0 表示关闭）。检查点放在**节点之间**：

- 预检之后：若已超预算，返回 `status=error` + `DEADLINE_EXCEEDED`（此时没有草稿可降级）；
- 每轮修复之前：若已超预算，停止开启新轮次，返回**当前最优草稿**，并在校验报告追加一条 `REPAIR_BUDGET_EXCEEDED` 软问题，trace 记 `budget.exhausted=true`。

降级与失败的区别很重要：预算耗尽时我们手里通常已经有一份「大部分规则通过」的方案，把它扔掉换成错误页，是把工程问题变成产品问题。

## 没有采纳通用状态机 / Tool Registry

- 这个系统**没有多轮工具调用**。工具在编排开始前一次性取好（`kb.context_for(request)`），生成是单次 JSON 输出，不存在「模型决定下一步调哪个工具」。画一个 `Tool Calling → Observation` 的节点图，图很好看，但它描述的是另一个系统。
- 引入 `Tool Registry` 会得到一份没有调用方的注册表——死代码，而且会让人以为系统支持动态工具，从而在面试里问出「工具调用失败怎么重试」这种本项目根本不存在的问题。**如实说「没有这一层，因为不需要」比装作有更有说服力。**
- 通用 FSM 框架（transitions / LangGraph）能表达这张表，但代价是：新增一个依赖、把控制流藏进装饰器或图定义、丢掉「读一遍 `run_plan` 就知道全部路径」的性质。当前只有 6 个节点和 1 个环，收益为负。
- TypeScript 接口：项目是纯 Python + 原生 JS 单页，没有前端类型消费方。写一份 `.d.ts` 只会是无人引用的装饰。

## 代价

- 多一个生成脚本、一份生成文档、一道 CI 门禁（秒级）。
- `Stage` 的输入/输出是带类型的**说明**，不是可执行 schema。它防的是「文档缺失」，不防「字段名写错」——真正的字段校验在 `parse_plan` 与 `validate` 里。这一点写在文档里，避免过度承诺。
- 预算只能在节点之间检查。已经发出的 HTTP 请求不能被中断，仍受 `llm_timeout` 约束；要做硬中断得在 provider 层加取消，当前没有。这个限制也写进了 `pipeline-contract.md`。
