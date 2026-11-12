# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

阶段 5 剩余项：可交互的在线 Demo、可观测面板的更多维度、简历与面试材料定稿。详见 `docs/ROADMAP.md`。

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
