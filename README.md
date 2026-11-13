# shotplan

> 把一句模糊的「想去西安拍汉服」，变成一份**能执行、能核对、能追溯**的小时级方案。
> 这是一个可评测的 Agent 系统，不是又一个聊天框。

[English README](README.en.md) · [在线展示](https://zhaozilong6666.github.io/shotplan/) · ![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![CI](https://github.com/zhaozilong6666/shotplan/actions/workflows/ci.yml/badge.svg) ![coverage](https://img.shields.io/badge/coverage-89%25-brightgreen)

---

## 为什么做这个

大模型很擅长生成「看起来很美」的方案：一天排五个外景、妆造排在拍摄之后、景点名字是编的、预算超一倍。这类输出在 demo 里令人惊艳，在真实业务里完全不可用。

这个项目主张一件很朴素的事：**AI 应用的质量必须能被测量，而不是靠感觉。** 所以它没有把「生成」当作终点，而是在生成之前先判断需求是否可满足、在生成之后加一层不依赖模型的硬约束校验器，把「可执行」写成可测试的规则；再用一套带**对照组**的评测集，证明这套测量确实能区分好方案和坏方案。

场景选的是汉服旅拍，因为约束足够真实：光线有时段、景点之间要转场、妆造必须先于拍摄、预算是硬上限、天气要有备选。而且这个领域的好坏，人是能判断的——这是定义「什么算好结果」的前提。

## 和常见 AI Demo 的区别

| | 常见做法 | 本项目 |
| --- | --- | --- |
| 输出 | 自由文本 | 结构化 `Plan`，带时间块、成本、引用 |
| 质量保证 | 换 prompt 碰运气 | 12 条确定性规则 + 分项打分 |
| 需求不可行 | 硬凑一份方案 | 生成前判定，返回原因、缺口、修改建议与理论下限 |
| 出错处理 | 重新生成一次 | 校验报告转成修复提示，且**拒绝接受更差的结果** |
| 可验证性 | 截图 | 80 个回归用例 + 200 个盲测 brief + 真实 HTTP 压测 + 对照组 + 13 道 CI 门禁 |
| 门禁本身 | 「CI 全绿」即视为有效 | 8 处受控变异注入，要求真实门禁全部拦截 |
| 没见过的题 | 只有一套手写用例 | 失败案例库聚类出 200 个盲测 brief，独立存放不污染基线 |
| 溯源 | 无 | 每个时间块引用知识库条目，编造地点会被拦截 |
| 运行记录 | 无 | 每次运行保存完整 trace、耗时、token 与估算成本 |
| 运行门槛 | 需要 API key | 核心零依赖，`git clone` 后直接可跑 |

## 快速开始（不需要账号、网络或 API key）

```bash
git clone https://github.com/zhaozilong6666/shotplan.git
cd shotplan
python -m shotplan.cli plan          # 生成一份完整方案 + 校验报告 + 追踪
python -m evals.run_eval --write-report   # 跑评测集并生成 docs/eval-report.md
python -m evals.mutation_lab --check   # 验证质量门禁本身有效
python -m evals.case_miner --check     # 盲测挑战集：检验没见过的组合
python -m evals.http_load --check       # 真实 uvicorn 服务的 HTTP 压测
```

如果没安装包，`python -m shotplan.cli` 需要 `PYTHONPATH=src`，或者执行 `python -m pip install -e ".[api,dev]"`（推荐，会同时装好 Web 与测试依赖）。

示例输出（节选）：

```
方案 plan_a1b2...  |  xian  2026-10-04  |  provider=mock
------------------------------------------------------------------------------
  12:00-14:05  makeup  唐制妆造               indoor_studio          400 元
  14:05-14:25  travel  转场至大雁塔北广场     big_wild_goose_pagoda   50 元
  14:25-16:02  shoot   大雁塔北广场拍摄       big_wild_goose_pagoda    0 元
  16:02-16:22  travel  转场至西安城墙         city_wall                0 元
  16:22-18:00  shoot   西安城墙拍摄           city_wall              420 元
------------------------------------------------------------------------------
  总花费 870 元 / 预算 1500 元
  得分 0.975  硬约束 通过
```

## 一次运行发生了什么

```mermaid
flowchart LR
    A[模糊 brief] --> Z[可行性判定<br/>不经过模型]
    Z -->|不可行| Z2[结构化拒绝<br/>原因 / 缺口 / 建议]
    Z -->|可行| B[结构化检索<br/>地点 / 风格 / 光线]
    B --> C[Provider 生成<br/>mock 或真实模型]
    C --> D[解析为 Plan<br/>信任边界]
    D --> E[硬约束校验<br/>不经过模型]
    E -->|失败| F[修复提示]
    F --> C
    E -->|通过| G[方案 + 校验报告 + trace]
    G --> H[SQLite 留痕]
```

关键点：**可行性判定与校验器都在模型之外**。模型可以换、prompt 可以改，但「什么算可执行」的定义不变，因此质量可以跨版本比较。详见 [ADR 0002](docs/adr/0002-validator-outside-model.md) 与 [ADR 0007](docs/adr/0007-feasibility-before-generation.md)。

## 硬约束校验器

规则集中定义在 `src/shotplan/domain/constraints.py`，校验器、评测打分器、文档和 `/api/rules` 共用同一份定义。

| 类别 | 规则 | 级别 |
| --- | --- | --- |
| 时间 | 时间块不重叠、总时长不超限 | 硬 |
| 流程 | 必须含妆造与拍摄，且妆造在拍摄之前 | 硬 |
| 空间 | 跨地点拍摄间至少 20 分钟转场 | 硬 |
| 预算 | 总花费不超过预算（取声明值与时间块求和的较大者） | 硬 |
| 溯源 | 地点 id 必须存在于知识库，拦截编造 | 硬 |
| 质量 | 拍摄块时长、黄金光线利用、备选方案、引用数量、风格匹配 | 软 |

完整规则表：`python -m shotplan.cli rules` 或 `docs/problem-and-metrics.md`。

## 生成前：需求不可满足时明确拒绝

有些 brief 在数学上不可能满足：预算低于最低可执行成本、时长短于「妆造 → 转场 → 拍摄」最短闭环、城市不在知识库、必去与排除互相矛盾。这类请求**不会调用模型**，直接返回结构化说明：

```bash
python -m shotplan.cli plan --budget 100
# [BUDGET_BELOW_MINIMUM] 预算 100 元低于最低可执行方案成本 350 元
#   缺口：还差 250 元
#   建议：把预算提高到至少 350 元，或改用最简妆造套餐与免费场景
```

CLI 退出码 `3`；API/JSON 返回 `status: "infeasible"` 与 `feasibility` 字段；Web 界面渲染专门的不可行视图。理论下限（350 元 / 1.5 小时）从知识库动态计算，详见 [ADR 0007](docs/adr/0007-feasibility-before-generation.md)。

## 评测：一个带控制组的实验

只对自己写的 planner 跑评测，很容易写出一套永远给满分的评分器。所以评测集会在**完全相同的 80 个用例**上跑两个 provider：

| 指标 | 规则 Agent (`good`) | 对照基线 (`naive`) |
| --- | ---: | ---: |
| 硬约束通过率 | **100%** | 13.8% |
| 平均得分 | **0.9788** | 0.3014 |
| 用例通过 | **80 / 80** | 11 / 80 |

差值 **0.6774** 分说明校验器和打分器确实在测量真实差异。`naive` 会故意忽略时间、转场、预算、溯源并编造地点。门禁不止比两个均分：它要求对照组**每条已知缺陷都仍被报出来**（规则级覆盖下限），另加差值下限与对照组上限。否则悄悄删掉一条硬规则是看不出来的——对照组方案坏得很彻底，`constraints` 维度早已被 clamp 到 0，少一条问题均分几乎不动。这条教训来自实测：旧门禁只拦住 8 处受控变异中的 4 处，见 [ADR 0009](docs/adr/0009-gate-sensitivity.md) 与 [变异测试报告](docs/mutation-report.md)。唯一门禁实现在 `evals/scorers.py`。

80 个用例分四组：`normal` 30、`conflict` 23、`edge` 16、`infeasible` 11。最后一组描述的是**数学上不可满足**的 brief，正确行为是结构化拒绝而非硬凑方案；因为可行性判定在生成之前且不确定于 provider，两组在这 11 个用例上同分，区分度由其余 69 个用例提供。

```bash
python -m evals.run_eval --write-report                            # 生成本次报告
python -m evals.run_eval --compare evals/baselines/v6-naive.json  # 与当前基线对比
python -m evals.run_eval --prompt-version planner-v1              # 记录 prompt 版本
python -m evals.run_eval --min-score 0.85                         # 提高回归门槛
```

报告由代码生成并随仓库提交（[docs/eval-report.md](docs/eval-report.md)），包含分组结果、逐用例明细、不可满足需求识别、成本/延迟（P50/P95、token）、对照组规则覆盖，以及一处可复核的「改动 → 分数下降 → 门禁拦截 → 回滚复原」实例——招聘方不用跑代码就能看到结论。方法论见 [docs/evaluation.md](docs/evaluation.md)。

## 十一份可核查的材料

光有评测分数还不够，这个仓库另外提供十一块可核查的证据：

1. **[修复循环实验](docs/repair-experiment.md)** —— 三种刻意构造的 provider 跑同一批 69 个可满足 brief：`repairable` 一次修复把硬约束通过率从 0% 拉到 100%（69/69）；忽略修复提示的 `naive` 有 69 次候选被拒绝；「越修越差」的 `regressing` 69 次全被单调保护拦下，均分不降。每次接受/拒绝都读 trace 里的 `accepted` 字段，不靠推断。
2. **[失败案例库](docs/failure-gallery.md)** —— 517 条结构化记录、4 类硬约束违规，每条包含输入 brief、观察到的输出片段、期望规则与修复建议。新增规则之前先在这里找证据；接真实模型后按同样格式追加新错误。
3. **[运行统计面板](docs/observability.md)** —— `python -m shotplan.cli stats`、`GET /api/stats` 与 Web「运行统计」标签页，按 provider / prompt 版本聚合通过率、均分、P50/P95 延迟、成本、修复轮次与不可行数量。数据直接来自 SQLite 里每次运行的完整 payload，不维护第二份指标表。
4. **[输入模糊测试](docs/fuzz-report.md)** —— 1000 个由真实 brief 变异出来的垃圾输入（错类型、NaN、`1e400`、emoji 洪水、NULL 字段、不存在的日期），断言 **0 个未捕获异常**。请求从 JSON 直接进入领域模型，没有 schema 层，所以「不能崩」必须是可测试的契约而不是承诺。
5. **[性能基准](docs/benchmark.md)** —— 单 brief p50 **≈0.2ms** / p95 **0.4–0.6ms**、单核 **数千次/秒**、Python 堆峰值 **0.02MB**；CI 设 p95 < 50ms、吞吐 > 50/s、堆 < 64MB 的绝对上限，用来抓「意外的 O(n²)」而不是卡抖动。延迟与内存分两轮测量，避免把 `tracemalloc` 的开销算成延迟。
6. **[并发负载测试](docs/loadtest.md)** —— 并发 1 / 4 / 8 各 100 次请求：**0 错误、600/600 写入不丢**。第一版在并发 8 下 p95 高达 180–230ms、吞吐比单线程还低，踩到两个真问题：Rollback journal 的 fsync 让写者互斥、API 每请求重建 `RunStore` 导致建表与 WAL 初始化互相抢锁（偶发 422）。改为 WAL + 进程级初始化/写锁 + 有限重试后，p95 降到约 27ms、吞吐约 310/s。
7. **[真实输出示例](docs/examples.md)** —— 四份实际跑出来的方案（正常 / 带排除约束 / 时长上限 / **不可满足**），含时间轴、花费、备选方案、假设与校验结论。由代码生成、`--check` 比对，任何人不用装环境就能先看输出。
8. **[线程 vs 进程扩展性](docs/scaling.md)** —— 同一 workload 两种并行方式：线程最好 **1.04×**、进程最好 **1.72×**（本机 18 逻辑核）。结论很直接：瓶颈是 GIL，不是代码结构；要提吞吐应当加 worker 进程而不是加线程。
9. **[门禁有效性：变异测试](docs/mutation-report.md)** —— 80/80 全绿不能证明检查本身有效。在进程内注入 **8 处受控故障**（3 处改生成端、5 处改校验端），跑同一套用例并要求真实门禁拒绝，再逐字段验证回滚。旧门禁只拦住 **4/8**，补齐对照组规则覆盖后 **8/8** 全部拦截、0 处存活；`--check` 是 CI 第 11 道门禁。比一路绿灯更有说服力的是一次「故意改坏 → 被拦下 → 复原」的记录。

10. **[盲测挑战集](docs/case-mining.md)** —— 「80/80 全绿」也可能只是对着 80 道题调规则。从 517 条失败记录与知识库交叉出 **225 个候选 brief**，挖掘器**不读取任何用例的期望输出**；筛掉 25 个「同一地点既指定又排除」的负向对照后，纳入 **200 个 main set 从未见过的组合**，把城市 × 风格覆盖从 19/25 补到 **25/25**。good 硬约束通过率 100%、naive 0%，最小得分差 0.7687。挑战集独立存放在 `evals/challenge/`，不并入主集，因此 v1–v6 基线仍可比；`--check` 是 CI 第 12 道门禁。理由见 [ADR 0010](docs/adr/0010-holdout-challenge-set.md)。

11. **[真实 HTTP 服务压测](docs/http-load.md)** —— 之前的吞吐都是直接调 Python 函数；这里启动真正的 `uvicorn`（1 / 2 / 4 worker），用 httpx 走 loopback 发 `POST /api/plan`，把 HTTP 解析、JSON 序列化、网络栈与**多进程同时写同一个 SQLite** 一起算进来。单 worker 并发 8 约 **251/s**、P95 **39.9ms**，1000 次真实请求 **0 错误、0 丢写**。最有意思的是**加 worker 没有线性提速**：4 worker 吞吐反而只有单 worker 的 0.52×——瓶颈从 GIL 变成了共享 SQLite 写锁，说明瓶颈会随架构层变化。吞吐 / P95 只进报告不进门禁（沿用 ADR 0008），`--check` 是 CI 第 13 道门禁。

## 架构与目录

```
shotplan/
├── src/shotplan/
│   ├── domain/        # 纯数据结构、规则与生成前可行性判定（零 I/O、零框架）
│   ├── knowledge/     # 人工整理的地点 / 风格知识库 + 检索工具
│   ├── agents/        # provider、版本化 prompt v1/v2、解析器、校验器
│   ├── pipeline.py    # 编排：可行性 → 检索 → 生成 → 解析 → 校验 → 修复
│   ├── tracing.py     # 耗时 / token / 成本 / 事件
│   ├── storage.py     # SQLite 运行历史
│   ├── observability.py # 按 provider / prompt 版本聚合运行质量
│   ├── api.py         # 可选 FastAPI 层
│   └── cli.py         # 命令行入口
├── web/               # 无构建步骤的原生单页界面（5 个视图）
├── evals/             # 80 用例 + 200 盲测 brief、变异/挖掘/HTTP 压测实验室、基准、基线
├── tests/             # 201 个单元测试，逐条覆盖规则、健壮性与边界
└── docs/              # 架构、指标、10 份 ADR、运维、面试笔记
```

核心引擎只用 Python 标准库；FastAPI / pytest / ruff 都是可选层。这个分层让「没有 key 也能完整跑通」成为设计，而不是妥协。

## 技术取舍（ADR）

| 编号 | 决策 | 一句话理由 |
| --- | --- | --- |
| [0001](docs/adr/0001-core-zero-dependency.md) | 核心域不依赖 pydantic / 任何框架 | 保证零门槛可运行，框架校验放在 API 边界 |
| [0002](docs/adr/0002-validator-outside-model.md) | 校验器不经过模型 | 质量定义必须稳定，才能跨模型、跨 prompt 比较 |
| [0003](docs/adr/0003-exact-retrieval-over-vectors.md) | 精确检索而非向量库 | 知识库小、约束需精确匹配、每条都要可引用 |
| [0004](docs/adr/0004-versioned-prompts.md) | prompt 视为代码，带版本与变更记录 | 质量退化要能归因到具体改动 |
| [0005](docs/adr/0005-no-frontend-framework.md) | 前端不用 React / 无构建步骤 | 把复杂度预算留给 Agent 逻辑 |
| [0006](docs/adr/0006-scoring-determinism.md) | 评分固定精度 + 精确求和 | 同一输入在 3.10–3.13 上必须逐位一致 |
| [0007](docs/adr/0007-feasibility-before-generation.md) | 可行性判定在生成之前 | 「能不能做」是算术问题，不该交给模型 |
| [0008](docs/adr/0008-fuzz-and-benchmark-gates.md) | 模糊测试与性能基准作为门禁 | 「不崩」和「不慢」必须是可判定的，不能靠人工 review |
| [0009](docs/adr/0009-gate-sensitivity.md) | 用变异测试验证门禁本身 | CI 全绿不等于检查有效；实测旧门禁漏掉 4/8 处校验端削弱 |
| [0010](docs/adr/0010-holdout-challenge-set.md) | 盲测挑战集独立存放 | 主集要能跨版本比较，泛化检验不该污染回归基线 |

## 可选：接真实模型、启动界面、部署

```bash
cp .env.example .env
# 编辑 .env：SHOTPLAN_PROVIDER=openai_compatible 并填入 API key
python -m pip install -e ".[api,dev]"
python -m shotplan.cli serve          # 打开 http://127.0.0.1:8000
```

任何 OpenAI 兼容端点都可用（OpenAI、DeepSeek、本地 Ollama / vLLM）。不填 key 时全部功能仍可在 `mock` 模式下运行。

Docker：

```bash
docker compose up --build          # http://127.0.0.1:8000
```

界面内置「规则 Agent / 对照基线」切换，可以直观看到同一份需求下，不可执行的方案被逐条标红；把预算改到 100 元则能看到结构化拒绝视图。

## 长期路线图

项目按数周到两个月的周期持续打磨，分五个阶段，每阶段都有可验证的产出。完整清单见 [docs/ROADMAP.md](docs/ROADMAP.md)：

- [x] 阶段 1：领域模型、知识库、校验器、离线 provider、CLI
- [x] 阶段 2：pipeline、trace、SQLite、API、Web 界面
- [x] 阶段 3：评测集、对照基线、自动报告、盲测挑战集、门禁有效性、真实 HTTP 压测、13 道 CI 门禁
- [ ] 阶段 4：离线部分已完成（80 用例、`INFEASIBLE`、prompt v1/v2、成本/延迟、修复实验、失败案例库），待接真实模型做对比实验
- [ ] 阶段 5：可观测面板与英文 README 已完成；待在线 Demo 部署、简历与面试材料定稿

## 已知限制（写在前面，不藏）

- 知识库是人工整理的小样本，不是实时商家/票价数据；`docs/adr/0003` 解释了为什么这样选。
- 可行性下限（350 元 / 1.5 小时）来自静态知识库的理论计算，不含实时票价、闭馆、天气与档期。
- 离线 `good` provider 是**按规则写的确定性实现**，它代表当前规则下的上限参考。评测体系真正的压力测试，发生在接入真实模型（阶段 4）之后；现阶段 `naive` 对照组负责给出下限。
- 报告里的成本/延迟维度在离线模式下估算成本恒为 0，只验证可观测链路；真实模型性价比对比尚未实测。
- 尚未支持真实预约/支付与用户鉴权；知识库目前覆盖五座城市（西安 / 洛阳 / 杭州 / 苏州 / 南京），新增城市要同时补数据、用例与基线。
- 性能与并发数字都来自单机测量（并发 1/4/8、线程 vs 进程），不是生产环境 QPS；流水线本身是同步计算，API 通过线程池把它移出事件循环。
- 模糊测试证明「任意输入都不崩」，不证明「任意输入都有好方案」——质量仍由评测集负责。
- 8 处变异是人为挑选的，证明这几类回归会被发现，不等于「门禁万无一失」。
- 200 个盲测 brief 来自固定网格（组合多样性），不是真实客户 brief 分布；它们只证明判定链在未见组合上一致，同样不代表真实模型效果。
- 真实 HTTP 压测是单机 loopback、无 TLS / 反向代理 / 真实模型延迟的小样本；同一台机器两次运行 4 worker 的吞吐能差近一倍，所以这些数字只作量级参考，不是生产 QPS。
- token 成本是估算值，只用于运行追踪，不是账单。

## 面试怎么讲

> 「我做了一个旅拍方案 Agent。核心不是调模型，而是先把『一份能执行的方案』写成 12 条可测试的硬约束，再在生成之前判断需求到底可不可满足；生成之后用不经过模型的校验器打分，把报告反馈给模型做修复，并且拒绝接受更差的结果。为了让质量可比较，我建了 80 个用例、覆盖五座城市的评测集，和一个故意犯错的对照 provider。更有意思的是我没有停在『80/80 全绿』：我给门禁本身做了变异测试，注入 8 处受控故障，发现旧门禁漏掉了 4 处『悄悄删掉一条校验规则』的改动——因为对照组方案太烂，少一条问题均分也不动。补上规则级覆盖后 8/8 全部拦截。我又从失败案例库聚类出 200 个主集没见过的组合做盲测，独立存放以免污染 v1–v6 基线。最后把服务真正跑起来压测，发现多 worker 抢同一个 SQLite 写锁反而变慢——瓶颈是会随架构层变化的。另外用模糊测试保证任何垃圾输入都不会让服务崩。」

深挖问答（为什么不把判定交给模型、怎么处理幻觉、怎么定义指标、修复循环如何避免回归）见 [docs/interview-notes.md](docs/interview-notes.md)。

## 需要你本人动手的事

GitHub 建仓与推送已用本机已登录凭证完成；现在只剩**简历表述**（以及可选的 API key、部署账号）。分步手册见 [docs/USER_TASKS.md](docs/USER_TASKS.md)。

## License

MIT
