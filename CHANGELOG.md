# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

阶段 4 剩余项：接真实模型跑 `openai_compatible`、记录 prompt v1/v2 的实测差异、用真实数据填满成本/延迟表。阶段 5 剩余项：可交互的在线 Demo、简历与面试材料定稿。详见 `docs/ROADMAP.md`。

## [0.7.0] - 2026-11-12

**把失败案例库从展示材料变成新用例的输入**：80/80 全绿也可能只是对着 80 道题调规则。新增一层独立的盲测挑战集，检验判定链在没见过的组合上是否一致。

### Added

- **盲测挑战集挖掘器**（`evals/case_miner.py`）：从 517 条失败记录按「问题码 × 城市」聚类，结合知识库构造确定性网格（5 城 × 5 风格 × 9 种压力配置 × 4 季轮换日期 = 225 个候选）。每个候选跑 `good` / `naive` 双 provider，需同时满足「不与主集重复、good 可执行且 ≥ 0.70、naive 被拒、得分差 ≥ 0.30」才纳入。**挖掘器不读取任何用例的期望输出**。
- `evals/challenge/cases.jsonl`（200 个盲测 brief）、`evals/case_mining.json`、[docs/case-mining.md](docs/case-mining.md) 三份产物由代码生成、逐字节可复现；`--check` 作为 CI 第 12 道门禁，会重新挖掘并比对产物。
- **负向对照**：25 个「同一地点既指定又排除」的不可满足 brief 被生成前可行性判定挡下；门禁要求它们**继续被筛掉**，否则判定为前置检查被削弱的回归，而不是覆盖率提升。
- [ADR 0010](docs/adr/0010-holdout-challenge-set.md)：为什么挑战集独立存放、不并入主集、不污染 v1–v6 基线。
- 8 个新测试（`tests/test_case_miner.py`）：网格确定性、指纹规范化、与主集不重复、产物一致性、负向对照与覆盖不变量、以及「手工篡改产物 / 负向对照消失 / 挑战集缩水」都会被门禁抓住。

### Changed

- CI 新增 Case-mining gate（12 道门禁）；`Makefile` 新增 `mine` 目标并纳入 `gates`。
- 展示页新增「盲测挑战集」区块（数据来自 `case_mining.json`），hero 说明、复现入口与 ADR 列表同步；中英文 README、路线图、任务手册、评测方法论、排错手册、面试讲稿、贡献指南按 v0.7.0 同步。

### Fixed

- **扩展性门禁会在小 workload 下误报失败**：CI 用 `--requests 300 --workers 1 2`，此时进程池 IPC / 调度开销会盖过并行收益，实测 2 进程吞吐（约 3599/s）低于 2 线程（约 4256/s），旧门禁把「进程不比线程慢」当硬断言，`SCALING GATE FAILED` 会让 CI 偶发红。速度是**机器与 workload 相关**的结果，不该进门禁（ADR 0008）。改为只检查机器无关的契约：0 失败、每个 arm 有耗时、多 worker 进程 arm 确实持有 ≥2 个真实 OS 进程 PID；速度对比降级为控制台与报告里的 informational note。连续三次 `--check` 退出码均为 0。`tests/test_scaling.py` 重写为验证这套新契约（含「进程慢 10 倍不应让门禁失败」的回归测试）。

### Measured

- 225 候选 → **200 纳入 / 25 筛掉**；城市 × 风格覆盖 **19/25 → 25/25**，主集未覆盖的新格子 6 个。
- 盲测集 good 硬约束通过率 **100%**、均分 0.9881；naive **0%**、均分 0.19；最小得分差 **0.7687**。
- `no_outdoor` 与 `micro_budget` 共 50 个候选全部通过，复核了室内备选硬编码与 C24 紧预算超支两处旧修复。
- 测试 **191 个**、覆盖率 **89%**；主评测集仍 80/80、0.9788 / 0.3014，历史基线不受影响。

## [0.6.0] - 2026-11-12

**给门禁本身做回归测试**：80/80 全绿不等于检查有效。注入 8 处受控故障后发现旧门禁漏掉 4 处「悄悄删掉一条校验规则」的改动，补齐对照组规则覆盖后 8/8 全部拦截。

### Added

- **变异测试实验室**（`evals/mutation_lab.py`）：3 处改生成端、5 处改校验端，全部在进程内注入并回滚，**不修改任何源文件**；跑同一批 80 用例、调用与 CI 完全相同的门禁函数。产物 [docs/mutation-report.md](docs/mutation-report.md) 与 `evals/mutation.json` 由代码生成、逐字节可复现，`--check` 作为 CI 第 11 道门禁。
- **对照组规则覆盖门禁**（`evals/scorers.py`）：`naive` 必须继续命中每条已知缺陷（`TRANSFER_BUFFER ≥ 100`、`UNKNOWN_LOCATION ≥ 90`、`BUDGET_EXCEEDED ≥ 30`、`TIME_OVERRUN ≥ 30` 及三条软规则各 ≥ 30），另加差值下限 0.45、对照组均分上限 0.45、硬约束通过率上限 0.30。门禁实现收敛到唯一函数 `eval_gate_problems`，实验室与 `run_eval` 共用。
- [ADR 0009](docs/adr/0009-gate-sensitivity.md)：记录盲点、决策、阈值取舍与备选方案。
- [docs/eval-report.md](docs/eval-report.md) 新增「对照组规则覆盖」与一处由代码生成的「改动 → 分数下降 → 门禁拦截 → 回滚复原」实例（good 均分 0.9788 → 0.1375 → 0.9788），满足阶段 4 的该项验收，无需 API key。
- 14 个新测试（`tests/test_mutation_lab.py` 13 个 + `tests/test_simulated_provider.py` 1 个），含「实验室与 CI 使用同一个门禁函数」的对象身份断言，以及成本报告延迟取整的确定性回归测试。

### Changed

- 展示页新增「门禁有效性」区块，且把原先手写的分组说明、对照组说明改为脚本生成（修掉页面仍写着「60 个用例 / 109 个单元测试」的漂移）。
- 中英文 README、路线图、任务手册、面试讲稿按 v0.6.0 同步；英文 README 补上此前遗漏的五座城市与 80 用例口径。
- `Makefile` 新增 `mutation` 目标并纳入 `gates`；CI 新增 Mutation gate。

### Measured

- 变异体 **8/8 被拦截、0 存活**；旧门禁只能拦住 **4/8**（漏掉的 4 个全是校验端削弱，good 均分保持 0.9788 不变）。
- 8 次回滚后与基线逐字段一致；测试 **180 个**、覆盖率 **89%**、评测 80/80、CI 11 道门禁 × 4 个 Python 版本。

## [0.5.0] - 2026-11-12

补上「不用装环境也能看输出」和「瓶颈到底在哪」两块：可复现的真实输出示例，以及线程 vs 进程的扩展性对比。

### Added

- **真实输出示例**（`evals/examples.py` + [docs/examples.md](docs/examples.md)）：四份实际运行结果——正常需求、带排除约束、12 小时上限、**不可满足预算**。每份含时间轴、花费、备选方案、假设、校验结论与运行步骤顺序。
- **线程 vs 进程扩展性实验**（`evals/scaling.py` + [docs/scaling.md](docs/scaling.md)）：同一 workload 下线程最好 **1.04×**、进程最好 **1.72×**（18 逻辑核），把「瓶颈是 GIL」从常识变成可复现数据。
- CI 增加两道门禁：示例文件必须与代码生成结果一致（`evals.examples --check`）、扩展性实验不得出现失败或「进程比线程慢」的异常。

### Changed

- 示例与扩展性报告只输出**确定性字段**：`plan_id` / `run_id` / 耗时一律不写入，否则文件在不同机器上无法逐字节一致，CI 比对只会变成噪声。

### Measured

- 线程：1 / 2 / 4 并行分别约 1.00× / 1.04× / 1.03×（GIL 上限）
- 进程：1 进程约 0.94×（启动与调度开销）、2 进程约 1.66×、4 进程约 1.72×
- 测试 **166 个**，覆盖率 **89%**

## [0.4.0] - 2026-11-12

第五座城市南京、评测扩到 80 用例，并补上并发这一层：**并发下不丢写、不报错**，顺手修掉两个只有压测才会暴露的存储缺陷。

### Added

- **第五座城市南京**（8 个地点：中华门瓮城、老门东、秦淮河夫子庙、中山陵、南京博物院、玄武湖、鸡鸣寺、室内影棚）；评测集扩到 **80 个用例**，`normal` 30、`conflict` 23、`edge` 16、`infeasible` 11，新增 `v6-naive.json` 基线。
- **并发负载测试**（`evals/loadtest.py` + [docs/loadtest.md](docs/loadtest.md)）：并发 1 / 4 / 8 各 100 次请求，断言「全部有应答、全部落到数据库」；同时给出吞吐、p50/p95 与饱和点。CI 增加第 8 道门禁。
- API 端点改为 `await run_in_threadpool(...)`：同步流水线不再阻塞事件循环。离线 provider 下只是「能同时处理第二个请求」，接真实模型后这是服务能否并发的分水岭。

### Fixed

- **并发写库丢数据 / 422**：API 每请求新建 `RunStore`，多个线程同时执行 `CREATE TABLE` + `PRAGMA journal_mode=WAL` 会互相抢锁，偶发 `attempt to write a readonly database`（表现为 HTTP 422）。改为进程级初始化锁 + 写锁 + 对 `locked` / `readonly` 的有限重试。
- **SQLite 写放大**：`journal_mode=WAL` 之后，并发 8 的 p95 从约 200ms 降到约 27ms，吞吐从约 100/s 提到约 310/s（同一台机器、同一批 brief）。
- 分位计算统一到 `evals.benchmark.percentile`，避免负载测试与基准各写一份、边界行为不一致。

### Measured

- 评测：80/80 通过，硬约束 100%，均分 **0.9788**；对照组 **0.3014**（13.8%），差值 **0.6774**。
- 修复实验：69 个可满足 brief，`repairable` 一次修复 **69/69**；`naive` 与 `regressing` 各 69 次候选全部被拒绝。
- 失败案例库：**517 条**（转场 198 / 编造地点 186 / 预算 69 / 超时 64）。
- 并发：600 次请求 0 错误、600/600 写入；p95 从约 200ms（修复前）降到约 27ms。
- 测试 **155 个**，覆盖率 **89%**。

## [0.3.0] - 2026-11-12

从「三城市 60 用例」扩展到「四城市 70 用例」，并把质量工程补上两层：**任意输入不崩**的模糊测试与**可门禁的性能基准**。同时让 malformed brief 不再可能触发 500。

### Added

- **第四座城市苏州**（8 个地点：平江路、拙政园、苏州博物馆、虎丘、山塘街、沧浪亭、金鸡湖、室内影棚）；评测集扩到 **70 个用例**，`normal` 26、`conflict` 20、`edge` 14、`infeasible` 10，新增 `v5-naive.json` 基线。
- **输入模糊测试**（`evals/fuzz.py` + [docs/fuzz-report.md](docs/fuzz-report.md)）：从真实 brief 变异出 1000 个垃圾输入（错类型、`NaN`、`1e400`、emoji 洪水、NULL 字段、不存在的日期、注入片段），断言 0 未捕获异常、0 不可序列化结果。每个用例带独立种子，`--only-seed` 可单条复现。
- **性能基准**（`evals/benchmark.py` + [docs/benchmark.md](docs/benchmark.md)）：延迟分位、吞吐、Python 堆峰值与分步耗时；延迟与内存**分两轮测量**，避免 `tracemalloc` 的开销污染延迟数字。实测 p50 ≈0.2ms / p95 ≈0.6ms、单核数千次/秒、堆峰值 0.02MB（数字随机器变化，门禁看绝对上限）。
- **`status="error"` 语义**：provider 抛错或输出不可解析时返回结构化错误结果（`PIPELINE_ERROR`），不再让异常穿透成 500。
- **`DATE_INVALID` 预检原因**：非法日期（如 `2026-02-30`）在生成前结构化拒绝，而不是让 `light_window` 抛 `KnowledgeError`。
- 新增两个 CI 门禁：`evals.fuzz --check`、`evals.benchmark --check`；展示页新增性能块，并把两处手写计数改为脚本生成。
- `Makefile` 新增 `fuzz`、`bench`、`gates`、`showcase` 目标，`report` 的基线修正为 `v5`。
- [ADR 0008](docs/adr/0008-fuzz-and-benchmark-gates.md)：记录两项门禁的决策、测量偏差（`tracemalloc` 污染延迟）与备选方案（Hypothesis、pytest-benchmark）被拒的理由。

### Fixed

- `PlanRequest.normalized()` 改为**全域函数**：`hours="abc"`、`budget_cny=None`、`styles` 传字符串或字典、`NaN`/`inf` 都不再抛异常，统一退化为文档化默认值并夹紧区间。此前这些输入会让 API 返回 500。
- `styles="tang"` 曾被按字符拆成 `["t","a","n","g"]`，现按单值处理。

### Measured

- 评测：70/70 通过，硬约束 100%，均分 **0.9778**；对照组 **0.3057**（14.3%），差值 **0.6721**。
- 修复实验：60 个可满足 brief，`repairable` 一次修复 **60/60**；`naive` 与 `regressing` 各 60 次候选全部被拒绝。
- 失败案例库：**437 条**（转场 171 / 编造地点 150 / 预算 60 / 超时 56）。
- 模糊测试：1000 次迭代，0 崩溃、0 不可序列化。
- 性能：p50 ≈0.2ms、p95 ≈0.6ms、p99 ≈2ms、单核数千次/秒、堆峰值 0.02MB。
- 测试 **148 个**，覆盖率 **89%**。

## [0.2.0] - 2026-11-12

从「单城市 12 用例」扩展到「三城市 60 用例」，并补齐生成前判定、修复实验、失败案例库与运行统计。核心主张不变：**质量必须可测量**。

### Added

- 生成前的**可行性判定**（`domain/feasibility.py`）：预算低于最低可执行成本、时长过短、未知城市、约束互相矛盾时，返回 `status=infeasible` + 原因/缺口/建议/理论下限，不调用 provider、不生成硬凑方案。CLI 退出码 3，API 与 Web 同步展示，见 ADR 0007。
- 评测集扩展到 **60 个用例**，覆盖西安 / 洛阳 / 杭州三座城市：`normal` 22、`conflict` 17、`edge` 12、`infeasible` 9；新增 `v4-naive.json` 基线（`v1`–`v3` 保留为历史快照）。
- **修复循环实验**（`evals/repair_lab.py` + [docs/repair-experiment.md](docs/repair-experiment.md)）：三种刻意构造的 provider 在 51 个可满足 brief 上对照；trace 新增 `accepted` 字段，让「单调保护拦截了几次」成为可测量数据。实测一次修复成功率 51/51，51 次更差修复全部被拒绝。
- **结构化失败案例库**（`evals/collect_failures.py` + `evals/failures/cases.jsonl` + [docs/failure-gallery.md](docs/failure-gallery.md)）：357 条记录、4 类硬约束违规，每条含输入 brief、输出片段、期望规则与修复建议。
- **可观测聚合面板**（`src/shotplan/observability.py` + [docs/observability.md](docs/observability.md)）：按 provider / prompt 版本聚合通过率、均分、P50/P95 延迟、成本、修复轮次与不可行数量；接入 `cli stats`、`/api/stats` 与 Web「运行统计」标签页。
- **成本/延迟模拟**（`evals/simulated_provider.py` + `evals/cost_latency.py` + [docs/cost-latency-simulation.md](docs/cost-latency-simulation.md)）：用可配置价格卡验证 token → trace → 报告 → 预算换算链路，并明确标注为模拟值。
- **CI 门禁**：修复实验（一次修复成功率、更差修复不得被接受）、失败案例库（数量、类别、字段完整性）、**覆盖率 ≥ 85%**、**展示页与评测产物一致**。
- **GitHub Pages 展示页**（`docs/index.html`）：指标、分组结果、修复实验、失败案例与 ADR 入口一页呈现；数据块由 `scripts/build_showcase.py` 从评测产物生成，杜绝手写数字漂移。
- prompt v1/v2 版本表、`get_system_prompt()`、真实模型 provider 的版本参数与 `--prompt-version` 评测参数，为接真实模型后的对比实验备好框架。
- 英文 [README.en.md](README.en.md) 与中文版互链；新增 `SECURITY.md` 与 Dependabot 配置。
- CLI 契约测试（`tests/test_cli.py`）覆盖退出码、JSON 输出、持久化、统计与命令转发。

### Fixed

- 紧预算用例 C24 暴露的实现缺陷：规则 planner 会挑收费景点导致 `BUDGET_EXCEEDED`。现在按剩余预算裁剪地点组合，并让 `best_style_for()` 先为转场预留 50 元、必要时自动降级妆造档位。修的是实现，不是调低用例期望。
- 多城市用例暴露的两处硬编码：妆造地点写死为西安 `indoor_studio`、客流备选写死为 `qinglong_temple`。已改为按当前城市的 `indoor_fallback` 与低客流地点动态选择。
- 评分聚合改用 `math.fsum` 并统一规整到 6 位小数：修复 Python 3.10/3.11 下满分方案得到 `1.0000000000000002`、导致 CI 两个矩阵失败的问题（3.12 起 `sum()` 使用补偿求和）。新增固定精度契约测试与 ADR 0006。
- trace 现在采用 provider 上报的 `duration_ms`，真实网络耗时与模拟延迟都能进入 P50/P95 统计。

### Changed

- `docs/USER_TASKS.md` 改为按当前状态记录已完成/待办事项；GitHub 建仓、推送、描述与 topics 已由本机凭证完成。
- 新增 `scripts/push_via_api.py`：代理只放行 `api.github.com` 时，通过 Git Data API 完成等价推送（逐对象校验 SHA、密钥不落盘），并在排错手册记录故障处置。
- GitHub Pages 源设为 `main` 的 `/docs` 目录，附 `.nojekyll`。

### Measured

- 单元测试：116 passed，覆盖率 88%（`pytest --cov=src/shotplan`）。
- 评测：规则 Agent 硬约束通过率 100%、均分 0.9762、60/60 用例通过；对照基线 15.0% / 0.3115；差值 0.6647。
- 修复实验：51/51 一次修复成功；51 次更差修复全部拦截。
- CI：Python 3.10 / 3.11 / 3.12 / 3.13 四版本矩阵全绿。

### Known limitations

- 知识库为静态小样本（三城 24 个地点），无实时客流与票价。
- 离线 `good` provider 是当前规则下的上限参考，不是真实模型效果；评测体系的压力测试待接入真实模型。
- 成本/延迟报告是模拟值，不是厂商实测。
- 无用户鉴权、无并发写入、无真实预约/支付。

## [0.1.0] - 2026-11-12

阶段 1–3 完成：一个可评测、带硬约束校验的旅拍方案 Agent。

### Added

- 核心域：`PlanRequest` / `Plan` / `TimeSlot` / `ValidationReport` / `RunTrace`，仅用标准库实现。
- 12 条硬/软约束及其单一定义源 `domain/constraints.py`。
- 不依赖模型的确定性校验器，覆盖时间、流程、转场、预算、地点溯源与质量信号。
- 人工整理的知识库（西安 8 个地点 / 5 种妆造风格）与精确检索工具。
- 离线 provider（`good` 规则实现 + `naive` 对照组）、OpenAI 兼容 provider（urllib + 重试 + 降级）。
- 编排层：检索 → 生成 → 解析 → 校验 → 修复，修复结果单调不降保护。
- 运行追踪（耗时 / token / 估算成本）与 SQLite 运行历史。
- CLI（`plan` / `rules` / `config` / `history` / `eval` / `serve`）、可选 FastAPI 层、无构建原生单页界面。
- 评测体系：12 个用例（normal / conflict / edge）、对照基线、自动 Markdown 报告、回归阈值与「测量失效」检查。
- 76 个单元测试，逐条覆盖规则与边界。
- 文档：README、架构说明、问题与指标、评测方法论、5 份 ADR、路线图、排错手册、面试笔记、用户操作手册。
- CI 工作流、Docker / compose、pre-commit 配置、Issue 模板、一键初始化脚本。

### Measured

- 单元测试：76 passed。
- 评测：规则 Agent 硬约束通过率 100%、均分 0.9734、12/12 用例通过；对照基线 0% / 0.1900；差值 0.7834。
- 核心链路在零 API key、零第三方依赖环境下完整可运行。

### Known limitations

- 知识库为静态小样本，无实时客流与票价。
- 离线 `good` provider 是当前规则下的上限参考，评测体系的压力测试待阶段 4 接入真实模型。
- 未显式处理预算低于最低妆造成本的不可满足需求。
- 无用户鉴权、无并发写入、无真实预约/支付。
