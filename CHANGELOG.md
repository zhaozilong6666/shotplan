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

### Fixed

- 评分聚合改用 `math.fsum` 并统一规整到 6 位小数：修复 Python 3.10/3.11 下满分方案得到 `1.0000000000000002`、导致 CI 两个矩阵失败的问题（3.12 起 `sum()` 使用补偿求和，掩盖了该差异）。评测聚合同步改为精确求和，新增固定精度契约测试与 ADR 0006（测试数 76 → 77，ADR 5 → 6 份）。

### Changed

- 首次推送 GitHub 并接入 CI 后补齐仓库描述、topics 与真实徽章链接；`docs/USER_TASKS.md` 改为按当前状态记录已完成/待办事项。
