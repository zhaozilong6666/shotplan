# 长期路线图

项目按数周到两个月的节奏推进。每个阶段都有**可验证的产出**，不是「继续优化」这种无法验收的任务。打勾表示代码与文档已经落地并通过本地验证。

## 阶段 1 · 把「可执行」定义清楚 ✅

- [x] 领域模型与规范化输入
- [x] 人工整理的知识库（8 个地点 / 5 种风格）+ 精确检索工具
- [x] 15 条硬/软约束及其单一定义源
- [x] 不依赖模型的校验器（另加生成前的可行性判定，见 ADR 0007）
- [x] 离线 provider（含故意犯错的对照模式）
- [x] CLI：`plan` / `rules` / `config` / `history` / `eval` / `serve`

验收：`python -m shotplan.cli plan` 无需任何凭证即可输出完整方案与校验报告。

## 阶段 2 · 让它成为一个系统 ✅

- [x] 编排：可行性判定 → 检索 → 生成 → 解析 → 校验 → 修复（拒绝更差结果）
- [x] 运行追踪：每步耗时、token、估算成本、修复轮次
- [x] SQLite 运行历史与聚合摘要
- [x] 可选 FastAPI 层与原生单页界面（五种视图）
- [x] 版本化 prompt、真实模型 provider（urllib + 重试 + 降级）
- [x] 301 个单元测试，逐条覆盖规则、可行性与边界

验收：`python -m pytest` 全绿；`python -m shotplan.cli serve` 后界面能切换 good/naive 并看到问题清单。

## 阶段 3 · 证明质量可被测量 ✅

- [x] 80 个评测用例，分 normal / conflict / edge / infeasible 四组
- [x] 对照组 provider + 基线快照（`v1` 12、`v2` 36、`v3` 50、`v4` 60、`v5` 70、`v6` 80 用例时代，均保留为历史快照）
- [x] 自动生成的 Markdown 评测报告（含成本/延迟维度与不可满足需求识别）
- [x] 回归阈值与「测量失效」检查
- [x] 输入模糊测试：1000 个变异 brief、可复现种子、0 未捕获异常（见 [fuzz-report.md](fuzz-report.md)）
- [x] 性能基准：P50/P95、吞吐、堆峰值，延迟与内存分轮测量（见 [benchmark.md](benchmark.md)）
- [x] 并发负载测试：并发 1/4/8 下 0 错误、0 丢写，并记录饱和点（见 [loadtest.md](loadtest.md)）
- [x] 线程 vs 进程扩展性：线程约 1.04×、进程约 1.72×，证明瓶颈在 GIL（见 [scaling.md](scaling.md)）
- [x] 真实输出示例：四份由代码生成、可 `--check` 比对的结果（见 [examples.md](examples.md)）
- [x] **门禁有效性**：8 处受控变异注入 + 回滚比对，旧门禁只拦住 4/8，补齐对照组规则覆盖后 8/8（见 [mutation-report.md](mutation-report.md)、[ADR 0009](adr/0009-gate-sensitivity.md)）
- [x] **盲测挑战集**：从 527 条失败记录聚类出 225 个候选、纳入 200 个未见 brief，城市 × 风格覆盖 19/25 → 25/25；独立存放不污染 v1–v6 基线（见 [case-mining.md](case-mining.md)、[ADR 0010](adr/0010-holdout-challenge-set.md)）
- [x] **真实 HTTP 服务压测**：启动 1/2/4 个 uvicorn worker 走 loopback 发真实请求，1000 次请求 0 错误 0 丢写；发现多 worker 抢共享 SQLite 写锁反而变慢（见 [http-load.md](http-load.md)）
- [x] **持久化对照实验**：`SHOTPLAN_PERSIST_RUNS` 开关在同一份产品代码上做开/关对照，验证 v0.8.0 的写锁归因；判定允许输出「不确定」，结论随数据动态生成（见 [worker-bottleneck.md](worker-bottleneck.md)、[ADR 0011](adr/0011-controlled-experiment-over-inference.md)）
- [x] **写入异步化**：`off/sync/async` 三组同批对照，异步相对同步 1 worker 2.96×、4 worker 1.95×，拿回约 47% 写库开销，三组 0 丢写；默认仍为 `sync`，硬杀丢失窗口用 `os._exit` 实测（见 [persistence.md](persistence.md)、[ADR 0012](adr/0012-write-behind-persistence.md)）
- [x] **流水线契约与墙钟预算**：6 个节点写成状态表，图与逐节点 I/O 由代码生成、CI 比对漂移；超预算时降级返回最优草稿（见 [pipeline-contract.md](pipeline-contract.md)、[ADR 0013](adr/0013-explicit-stage-table-and-budget.md)）
- [x] GitHub Actions：测试 + lint + **16 道质量门禁**（单元测试跑 3.10–3.13 矩阵，重门禁在 3.12 执行一次）

验收：`python -m evals.run_eval --write-report` 退出码 0；实测 80/80 通过、均分 0.9778、对照组 0.3014；`python -m evals.mutation_lab --check` 退出码 0 且 0 处变异存活；`python -m evals.case_miner --check` 退出码 0 且 200 个盲测 brief 全部与主集不重复；`python -m evals.http_load --check` 退出码 0 且真实 HTTP 请求 0 错误、0 丢写；`python -m evals.worker_lab --check` 退出码 0 且持久化开关语义正确（开 = 全落库、关 = 0 落库）；`python -m evals.persist_lab --check` 退出码 0 且三组各自满足落库契约；`python scripts/build_pipeline_doc.py --check` 退出码 0 且契约文档与代码一致。

## 阶段 4 · 接真实模型，让评测真正承压 ⏳

这一阶段才会出现「中间地带」，也是项目从工程作业变成有说服力作品的关键。

- [x] 扩充评测集到 80 个用例，覆盖西安 / 洛阳 / 杭州 / 苏州 / 南京，含多风格、多人、节假日边界与 11 个不可满足需求
- [x] 显式处理 `INFEASIBLE`：生成前判定并返回原因、缺口、建议与理论下限（ADR 0007）
- [x] prompt v1 / v2 版本表 + `get_system_prompt()` + `--prompt-version` 评测参数，对比框架就绪
- [x] 成本/延迟报告：P50/P95、token 汇总、估算成本、修复轮次（离线成本恒 0，格式已验证）
- [ ] 用真实 OpenAI 兼容端点跑通 `SHOTPLAN_PROVIDER=openai_compatible`
- [ ] 记录 v1 / v2 prompt 在真实模型上的硬约束通过率差异，写进 `PROMPT_CHANGELOG`
- [x] 修复循环实验（离线）：一次修复成功率 100%（69/69）、69 次更差修复全被拦截，见 [repair-experiment.md](repair-experiment.md)
- [x] 失败案例库框架 + 离线对照组 527 条记录；真实模型典型错误待补，见 [failure-gallery.md](failure-gallery.md)
- [x] 失败案例库不再只是展示材料：它驱动盲测挑战集挖掘，见 [case-mining.md](case-mining.md)
- [x] `docs/eval-report.md` 记录至少一次「改动导致分数下降并回滚」的实例（由变异测试自动生成，离线数据即可复核）
- [ ] 用真实模型数据填满成本/延迟对比表

验收：报告里出现真实模型的一行数据，且能与基线做 `--compare`。「分数下降并回滚」一项已用离线变异测试完成，不必等 API key。

**需要你做的**：申请一个 API key 并写入 `.env`，步骤见 [portfolio/USER_TASKS.md](portfolio/USER_TASKS.md) 第 4 节。不需要把 key 发给任何人。

## 阶段 5 · 上线与叙事 ⏳

- [ ] 在线 Demo 部署（默认 `mock` 模式，避免公开课暴露密钥）
- [ ] README 顶部放 Demo 链接与 30 秒演示动图
- [x] 可观测面板：按 provider / prompt 版本聚合通过率、延迟与成本（CLI / API / Web）
- [x] 静态展示页（GitHub Pages）：指标、分组结果、修复实验、失败案例与 ADR 入口一页呈现
- [x] 中文 + 英文 README 互链（投外企时切换）
- [ ] 简历 3 条 bullets 与 10 分钟讲稿定稿
- [ ] 按目标 JD 调整 README 关键词顺序

验收：任意一个陌生人通过链接能在不改代码、不配 key 的情况下完成一次生成并看到校验报告。

**需要你做的**：部署账号与域名，步骤见 [portfolio/USER_TASKS.md](portfolio/USER_TASKS.md) 第 5 节。

## 明确不做

多 Agent 辩论、向量数据库、用户系统、真实下单支付、移动端适配。理由：这些不会让「质量是否可测量」这个核心论证更可信，只会稀释它。若未来要做，先写一条 ADR 说明它解决的新问题。

## 阶段 6–9 · 从「做完」到「做深」（建议 6–8 周）

前五个阶段解决的是「功能完整 + 可测量」。真正拉开深度的是下面四件事：每一件都能产出**可证伪的结论**，而不是再加一个功能。顺序按「能改变结论」排列。

### 阶段 6 · 测量严谨性（1–2 周）

- [x] 真实模型评测支持 `--repeat N`，每个用例跑 N 次，报告用配对均值 ± 区间
- [x] prompt 版本 A/B：`planner-v2` vs `planner-v3`，同批用例、同温度，报告差异区间。结论：+0.0115（95% CI [−0.020, +0.043]）测不出差异，成本 +53%，**不采纳 v3**。见 [prompt-ab.md](prompt-ab.md)
- [x] 写清样本量：要检测 3pp 差异需要多少用例（功效分析），并说明本项目的 69 例能检测多大效应（见 [measurement.md](measurement.md)）
- [x] 把「区间重叠 → 不下结论」写进评测文档，作为评审规则

验收：能拿出一份报告，明确说出「v3 比 v2 好 X 个百分点，95% 区间 [a, b]，区间不跨 0」——或者诚实地说明测不出来。

### 阶段 7 · 失败驱动的改进闭环（2 周）

- [ ] 失败分类学升级为**可执行清单**：每类失败对应一个修复策略与一个回归用例
- [ ] 统计「修复轮次 × 失败类型 × 是否修好」，找出哪些失败修复循环根本救不了
- [ ] 红队集：人工构造 30 个「语义合理但不可执行」的 brief（光线/体力/预约时段冲突）
- [ ] 每次 prompt 改动都跑红队集，记录通过率变化

验收：随机抽一类失败，能说清「它为什么发生、哪条代码负责、哪个用例会拦住它」。

### 阶段 8 · 知识库与真实用户（2–3 周）

- [ ] 城市从 5 个扩到 10 个，每条记录带 `source` 与 `last_verified_at`（已有字段与校验）
- [ ] 写一个复核脚本：超过 N 天未核验的条目列出清单，进 CI 门禁
- [ ] 找 5–10 个真实用户试用，记录「哪些建议被采纳、哪些被无视」，形成定性结论
- [ ] 两人独立标注 30 个方案的「可执行性」，算 Cohen's kappa，验证评分与人类判断是否一致

验收：能回答「你的评分和人的判断一致吗」，并给出 kappa 与分歧案例。

### 阶段 9 · 生产化（2 周）

- [ ] 指标导出（Prometheus 格式）：请求量、通过率、P95、修复率、错误分类
- [ ] 故障注入：provider 超时/429、SQLite 锁、磁盘满，验证降级路径与告警
- [ ] 定义 SLO（如「95% 请求在 30s 内返回且不 5xx」）并写对应监控查询
- [ ] 部署到公网，跑一周真实流量，回收延迟与失败分布

验收：能给出一周真实流量的分布图，并指出一个只有上线后才暴露的问题。

### 为什么这些算「深度」而不是「堆功能」

| 做法 | 产出 | 能不能被追问倒 |
| --- | --- | --- |
| 再加一个 Agent 功能 | 演示更花哨 | 一问「怎么证明它更好」就卡住 |
| 阶段 6 | 统计上站得住的比较 | 能，但你能用区间自证 |
| 阶段 7 | 失败与代码的对应关系 | 能指到具体行与用例 |
| 阶段 8 | 人类判断的一致性证据 | 能给出 kappa 与分歧样本 |
| 阶段 9 | 真实流量与降级路径 | 能讲出上线后才发现的坑 |

**时间不是靠排期堆出来的**：这四阶段合计约 40–60 个工作日，但真正让它像「几个月的项目」的，是每一阶段都留下「假设 → 实验 → 反证 → 改设计」的记录。目前仓库里已经有一次这样的完整闭环（`docs/failure-taxonomy.md` 第四节：先判断解析是缺陷，复跑三个用例后发现是采样长尾，于是撤回结论）。
