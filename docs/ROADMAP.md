# 长期路线图

项目按数周到两个月的节奏推进。每个阶段都有**可验证的产出**，不是「继续优化」这种无法验收的任务。打勾表示代码与文档已经落地并通过本地验证。

## 当前焦点 · 交互深度（2026-11-17 起）

单轮已经到顶：80 例硬约束 80/80、均分 0.93，继续调它的边际收益接近零。真正没被
测量过的是**第一轮之后**——真实用户不会一次把需求说清楚。

第一次测量见 [multi-turn.md](multi-turn.md)：12 条多轮用例，**5 条通过（42%）**。
`followup.py` 是一条关键词白名单，只认识「加」，不认识「砍」「便宜 300」「预算两千」
「不要唐制」；7 条失败收敛到三个缺口——只做加法、不解析数字、不碰字段级修改。

所以当前位置：把追问从「字段微调器」做成「能收敛的对话」。验收标准先立好——
同一套 12 例通过率往上走，且每条失败都能指到一个明确的规则缺口，而不是「模型没懂」。

发布 Demo 与公网流量不在此列：没有真实用户时，它增加的是维护面，不是信息量。

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
- [x] **盲测挑战集**：从 527 条失败记录聚类出 360 个候选、纳入 320 个未见 brief，城市 × 风格覆盖 19/40 → 40/40；独立存放不污染 v1–v6 基线（见 [case-mining.md](case-mining.md)、[ADR 0010](adr/0010-holdout-challenge-set.md)）
- [x] **真实 HTTP 服务压测**：启动 1/2/4 个 uvicorn worker 走 loopback 发真实请求，1000 次请求 0 错误 0 丢写；发现多 worker 抢共享 SQLite 写锁反而变慢（见 [http-load.md](http-load.md)）
- [x] **持久化对照实验**：`SHOTPLAN_PERSIST_RUNS` 开关在同一份产品代码上做开/关对照，验证 v0.8.0 的写锁归因；判定允许输出「不确定」，结论随数据动态生成（见 [worker-bottleneck.md](worker-bottleneck.md)、[ADR 0011](adr/0011-controlled-experiment-over-inference.md)）
- [x] **写入异步化**：`off/sync/async` 三组同批对照，异步相对同步 1 worker 2.96×、4 worker 1.95×，拿回约 47% 写库开销，三组 0 丢写；默认仍为 `sync`，硬杀丢失窗口用 `os._exit` 实测（见 [persistence.md](persistence.md)、[ADR 0012](adr/0012-write-behind-persistence.md)）
- [x] **流水线契约与墙钟预算**：6 个节点写成状态表，图与逐节点 I/O 由代码生成、CI 比对漂移；超预算时降级返回最优草稿（见 [pipeline-contract.md](pipeline-contract.md)、[ADR 0013](adr/0013-explicit-stage-table-and-budget.md)）
- [x] GitHub Actions：测试 + lint + **29 道质量门禁**（单元测试跑 3.10–3.13 矩阵，重门禁在 3.12 执行一次）

验收：`python -m evals.run_eval --write-report` 退出码 0；实测 80/80 通过、均分 0.9796、对照组 0.2974；`python -m evals.mutation_lab --check` 退出码 0 且 0 处变异存活；`python -m evals.case_miner --check` 退出码 0 且 320 个盲测 brief 全部与主集不重复；`python -m evals.http_load --check` 退出码 0 且真实 HTTP 请求 0 错误、0 丢写；`python -m evals.worker_lab --check` 退出码 0 且持久化开关语义正确（开 = 全落库、关 = 0 落库）；`python -m evals.persist_lab --check` 退出码 0 且三组各自满足落库契约；`python scripts/build_pipeline_doc.py --check` 退出码 0 且契约文档与代码一致。

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

- [x] ~~在线 Demo 部署~~ —— **判定不做**（2026-11-17）。理由不是做不到，是价值排序：
  没有真实用户、没有反馈渠道，上线只多出一个需要长期维护的公开链接，以及一次
  必须做对的脱敏打包（仓库里有 `.env`）。GitHub 上的代码、CI 与可 `--check`
  的评测报告已经让结论完整可复现，公开链接没有增加任何可验证的信息。
- [x] ~~README 顶部放 Demo 链接~~ —— 同上，等第一批真实用户出现再说。
- [x] 可观测面板：按 provider / prompt 版本聚合通过率、延迟与成本（CLI / API / Web）
- [x] 静态展示页（GitHub Pages）：指标、分组结果、修复实验、失败案例与 ADR 入口一页呈现
- [x] 中文 + 英文 README 互链（投外企时切换）
- [ ] 简历 3 条 bullets 与 10 分钟讲稿定稿
- [ ] 按目标 JD 调整 README 关键词顺序

验收：任意一个陌生人 clone 仓库后，按 README 两步跑通一次生成并看到校验报告，无需 key、无需部署。

**需要你做的**：无。这一阶段剩下的只是简历 bullets 与讲稿，不需要账号或域名。

## 明确不做

多 Agent 辩论、向量数据库、用户系统、真实下单支付、移动端适配。理由：这些不会让「质量是否可测量」这个核心论证更可信，只会稀释它。若未来要做，先写一条 ADR 说明它解决的新问题。

## 阶段 6–9 · 从「做完」到「做深」（建议 6–8 周）

前五个阶段解决的是「功能完整 + 可测量」。真正拉开深度的是下面四件事：每一件都能产出**可证伪的结论**，而不是再加一个功能。顺序按「能改变结论」排列。

### 阶段 6 · 测量严谨性（1–2 周）

- [x] 真实模型评测支持 `--repeat N`，每个用例跑 N 次，报告用配对均值 ± 区间。实测 3 轮：通过率 94.2%（极差 0.0pp）、得分极差 1.38pp；4 例因断供从未响应。见 [measurement.md](measurement.md#一点五实测三次重复跑出了-00pp-的极差)
- [x] prompt 版本 A/B：第一次在饱和子集上测不出差异（+0.0115，区间跨 0）；换到光线敏感用例后 v3 得分 +0.0431（区间不跨 0）；但 holdout 前瞻验证未通过。**最终仍不采纳 v3 为默认**。见 [prompt-ab.md](prompt-ab.md) 与 [router-validation.md](router-validation.md)
- [x] 写清样本量：要检测 3pp 差异需要多少用例（功效分析），并说明本项目的 69 例能检测多大效应（见 [measurement.md](measurement.md)）
- [x] 把「区间重叠 → 不下结论」写进评测文档，作为评审规则

验收：能拿出一份报告，明确说出「v3 比 v2 好 X 个百分点，95% 区间 [a, b]，区间不跨 0」——或者诚实地说明测不出来。

### 阶段 7 · 失败驱动的改进闭环（2 周）

- [x] 失败分类学升级为**可执行清单**：每类失败对应一个修复策略与一个回归用例（见 [rule-map.md](rule-map.md)，由 `scripts/build_rule_map.py` 生成并纳入门禁）
- [x] 统计「修复轮次 × 失败类型 × 是否修好」：8 份快照 / 315 条逐例记录 / 25 条触发修复。未修复用例通过率 96.6%，修过 1 次的只有 80%；`MISSING_REQUIRED_LOCATION` 修复成功率 71%（5/7），`UNKNOWN_SOURCE_REF` 3/3。**没有哪种失败被证明救不了**（样本都 <10，明确拒绝下结论）。见 [repair-attribution.md](repair-attribution.md)
- [x] 红队集：30 条「语义合理但可执行性紧张」的 brief（光线/多地点/预算贴底/排除冲突/输入噪声 + 6 条预检拒绝）。先离线验证难度再花钱：naive 0/24、规则 Agent 21/24、真实模型 23/24（95.8%）。结论：**硬约束已饱和，真正的弱点是 `LIGHT_MISUSE` 这类软问题**。见 [redteam-analysis.md](redteam-analysis.md)
- [x] 下一版红队瞄准软问题：12 条光线敏感 brief，`LIGHT_MISUSE` 触发 9/12（75%，主集 38%）。用这批题重测 `planner-v3`：得分 +0.0431（95% CI [+0.0127, +0.0763]），**第一次 A/B 的「v3 无效」是被饱和样本骗了**。见 [redteam-analysis.md](redteam-analysis.md) 与 [prompt-ab.md](prompt-ab.md)
- [x] 实现按 brief 特征路由（`SHOTPLAN_PROMPT_ROUTER=1`：偏好含上午/正午/不拍傍晚/室内为主，或必去晨光/夜景限定地点时用 v3）。**默认关闭**
- [x] 在**一批新用例**上前瞻验证路由：另造 16 条 holdout（8 光线 + 8 普通，城市/日期/措辞全换），两臂各一轮。**结果未通过**：得分 +0.0007（95% CI [−0.0343, +0.0338]），光线组仅 +0.0025，`LIGHT_MISUSE` 7→8。调参集上的 +0.0431 没有迁移，属选择偏差。见 [router-validation.md](router-validation.md)，**路由保持关闭**

验收：随机抽一类失败，能说清「它为什么发生、哪条代码负责、哪个用例会拦住它」。

### 阶段 8 · 知识库与真实用户（2–3 周）

- [x] 写一个复核脚本：`scripts/check_knowledge_freshness.py` 列出超过 N 天未核验的条目，同时检查 `source` 缺失与日期非法，已进 CI 门禁（当前 45 条记录全部 1 天内核验）
- [x] **城市选择由知识库数据驱动**（`GET /api/catalog` + 动态下拉）：修掉「前端只写死西安、另外 4 城不可选」的产品缺陷，扩城时界面自动跟上
- [ ] 城市从 5 个扩到 10 个 —— **数据契约已就绪，真实数据仍需人工核验**：`scripts/validate_knowledge.py` 校验 14 个地点字段 / 11 个风格字段、id 唯一、光线枚举、价格区间、日期合法性，现有 8 城 67 地点 5 风格 0 问题；`--check` 是门禁。扩城流程与「为什么不能靠编数据勾掉」见 [knowledge-contract.md](knowledge-contract.md)
- [ ] 找 5–10 个真实用户试用，记录「哪些建议被采纳、哪些被无视」，形成定性结论
- [ ] 两人独立标注 30 个方案的「可执行性」，算 Cohen's kappa —— **工具链已备好，等真人数据**：`evals/annotation_kit.py` 生成 30 份**盲标注**方案（校验器判定不出现在标注表）、两张空白表、无序 κ 与二次加权 κ 计算器（已知答案自检通过）、分歧案例输出。`--check` 是门禁。**报告不含 κ 结论**：没有真人就写数字是伪造证据。见 [annotation-kappa.md](annotation-kappa.md)

验收：能回答「你的评分和人的判断一致吗」，并给出 kappa 与分歧案例。

### 阶段 9 · 生产化（2 周）

- [x] 指标导出（Prometheus 文本格式）：`GET /metrics` 输出请求量、通过率、P95、修复率、花费、错误分类，并按 provider + prompt_version 下钻。全部标为 gauge（滑动窗口，不是累计计数），空库也输出完整 HELP/TYPE；见 [observability.md](observability.md)
- [x] **架构评审 P1/P2 收敛**：见 [architecture-review.md](architecture-review.md) 末节与 [ADR 0017](adr/0017-one-settings-object-per-run.md)。`get_settings()` 成为唯一入口、`build_provider` 强制注入、双 Settings 对象触发 `ConfigError`；快照写入完整 `effective_config`；顶层错误边界不再冒 500；`REPAIR_FAILED` 可见；异常文案脱敏；写锁按库分片；窗口排序加 tiebreak。**剩余最高风险已清零**
- [x] 端到端一致性复测（¥0.24）：router 开启下 4 条 holdout 的快照给出 `{v2:2, v3:2}` 逐例分布；CLI 两次 `--persist` 在 DB 中分别落 v3/v2；注入一条 502 后生产侧 `hard_pass_rate` 仍是 1.0、`error_rate=0.5`。见 [architecture-review.md](architecture-review.md) 末节
- [x] **P1/P2 重构后复测**（¥0.14）：同批 4 例，重构前后通过率均 1.0、逐例版本分布完全一致；新快照带 22 项 `effective_config`、`temperature` 读自 settings。首次运行因上游挂起触发 deadline 降级，并暴露「只认 `PIPELINE_ERROR`」的报告 bug，已修（`_fatal_message` + 单测）；断点续跑 4/4 零错误。得分 0.82→0.8662 **不归因于重构**（temperature 变了且单轮 4 例）
- [x] 故障注入矩阵（`evals/fault_injection.py`，12/12 通过，纯离线、已进门禁）：provider 超时/429/502、解析失败、SQLite 锁、磁盘满 ENOSPC、无权限、知识库损坏、校验器异常、断供不污染分母、API 502/503 映射。顺带修掉 `_safe_save` 只捕获 `StorageError` 而漏掉 `OSError` 的真实缺陷。见 [fault-injection.md](fault-injection.md)
- [x] 定义 SLO 与监控查询：见 [slo.md](slo.md) + [slo-baseline.md](slo-baseline.md)（由 7 份快照生成并纳入门禁）。**修正原假设**：「95% 请求 30s 内返回」与实测 P50 40.5–91.5s、P95 63.9–171.8s 矛盾，改为 P50 ≤95s / P95 ≤180s / 通过率 ≥90% / 上游错误率 ≤5% / 单次 ≤¥0.08，并写清 gauge 窗口语义与黑盒探针必要性
- [x] **一句话需求理解 + 假设透明 + 约束协商**：见 [intelligence-roadmap.md](intelligence-roadmap.md)
- [ ] 部署到公网，跑一周真实流量，回收延迟与失败分布 —— **当前不投入**：需要真实部署环境与真实用户，两者都不具备，而离线实验无法替代。判断依据见下方「当前焦点」；上线前的检查清单与「只有上线后才知道」的清单仍保留在 [release-readiness.md](release-readiness.md)，具备条件时可原样执行

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

## 过程记录：读的时候请当成历史

以下文档记录的是当时的过程与判断，**不是当前结论**。保留它们是因为「为什么那样决定」
比「最后是什么」更难重建；但引用其中的数字前，先看有没有更新的一份。

- [optimization-report.md](optimization-report.md) —— 一次完整优化的逐项记录，含被推翻的方案
- [review-2026-11-13.md](review-2026-11-13.md) —— 外部视角对未提交改动的审核与整改
- [real-model-baseline-2026-11-16.md](real-model-baseline-2026-11-16.md) —— 换通道 + low 推理后的第一份全量（已被 `real-model.md` 取代）
- [publish-status.md](publish-status.md) —— 发布相关状态；公网 Demo 已判定不做，判断见阶段 5

报告之间是否重复、哪些链接已失效、哪些没人链接，由
[report-audit.md](report-audit.md) 自动生成（`scripts/audit_reports.py`，第 29 道门禁）。
