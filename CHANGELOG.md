# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

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

## [Unreleased]

阶段 4（真实模型与 prompt 版本对比）与阶段 5（在线 Demo、可观测面板、简历材料定稿），详见 `docs/ROADMAP.md`。

### Added

- **修复循环实验**（`evals/repair_lab.py` + [docs/repair-experiment.md](docs/repair-experiment.md)）：三种刻意构造的 provider 在同一批 30 个可满足 brief 上对照，统计一次修复成功率、接受/拒绝次数；trace 新增 `accepted` 字段，让「单调保护拦截了几次」成为可测量数据。
- **结构化失败案例库**（`evals/collect_failures.py` + `evals/failures/cases.jsonl` + [docs/failure-gallery.md](docs/failure-gallery.md)）：170 条记录、4 类硬约束违规，每条含输入 brief、输出片段、期望规则与修复建议。
- **可观测聚合面板**（`src/shotplan/observability.py` + [docs/observability.md](docs/observability.md)）：按 provider / prompt 版本聚合通过率、均分、P50/P95 延迟、成本、修复轮次与不可行数量；接入 `cli stats`、`/api/stats` 与 Web「运行统计」标签页。
- 英文 [README.en.md](README.en.md) 与中文版互链；GitHub Pages 静态展示页（`docs/index.html`，含指标、分组结果、修复实验、失败案例与 ADR 入口）。
- 成本/延迟模拟（`evals/simulated_provider.py` + `evals/cost_latency.py` + [docs/cost-latency-simulation.md](docs/cost-latency-simulation.md)）：用可配置价格卡验证 token → trace → 报告 → 预算换算链路，并明确标注为模拟值。
- CI 新增两道门禁：`evals.repair_lab --check`（一次修复成功率、更差修复不得被接受）与 `evals.collect_failures --check`（案例数量、覆盖类别、字段完整性）。
- 生成前的**可行性判定**（`domain/feasibility.py`）：预算低于最低可执行成本、时长过短、未知城市、约束互相矛盾时，返回 `status=infeasible` + 原因/缺口/建议/理论下限，不调用 provider、不生成硬凑方案。CLI 退出码 3、API 与 Web 同步展示，见 ADR 0007。
- 评测集扩到 **36 个用例**（normal 13 / conflict 10 / edge 7 / infeasible 6），新增 `evals/baselines/v2-naive.json` 基线；`v1-naive.json` 保留为 12 用例时代的历史快照。
- 评测报告新增「不可满足需求识别」与「运行成本与延迟」（P50/P95、token 汇总、估算成本、修复轮次）两节。
- prompt v1/v2 版本表、`get_system_prompt()`、真实模型 provider 的版本参数与 `--prompt-version` 评测参数，为接真实模型后的对比实验备好框架。
- 9 个可行性单元测试（含「provider 绝不被调用」与边界值）、2 个打分器测试、1 个 API 测试；测试总数 77 → 90。

### Fixed

- 紧预算用例 C24 暴露的实现缺陷：规则 planner 会挑收费景点导致 `BUDGET_EXCEEDED`。现在按剩余预算裁剪地点组合，并让 `best_style_for()` 先为转场预留 50 元、必要时自动降级妆造档位。修的是实现，不是调低用例期望。
- 评分聚合改用 `math.fsum` 并统一规整到 6 位小数：修复 Python 3.10/3.11 下满分方案得到 `1.0000000000000002`、导致 CI 两个矩阵失败的问题（3.12 起 `sum()` 使用补偿求和，掩盖了该差异）。评测聚合同步改为精确求和，新增固定精度契约测试与 ADR 0006（测试数 76 → 77，ADR 5 → 6 份）。

### Changed

- 首次推送 GitHub 并接入 CI 后补齐仓库描述、topics 与真实徽章链接；`docs/USER_TASKS.md` 改为按当前状态记录已完成/待办事项。
- 新增 `scripts/push_via_api.py`：代理只放行 `api.github.com` 时，通过 Git Data API 完成等价推送（逐对象校验 SHA、密钥不落盘），并在排错手册记录故障处置。
