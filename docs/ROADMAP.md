# 长期路线图

项目按数周到两个月的节奏推进。每个阶段都有**可验证的产出**，不是「继续优化」这种无法验收的任务。打勾表示代码与文档已经落地并通过本地验证。

## 阶段 1 · 把「可执行」定义清楚 ✅

- [x] 领域模型与规范化输入
- [x] 人工整理的知识库（8 个地点 / 5 种风格）+ 精确检索工具
- [x] 12 条硬/软约束及其单一定义源
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
- [x] 166 个单元测试，逐条覆盖规则、可行性与边界

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
- [x] GitHub Actions：测试 + lint + 评测门禁（Python 3.10–3.13 四版本矩阵）

验收：`python -m evals.run_eval --write-report` 退出码 0；实测 80/80 通过、均分 0.9788、对照组 0.3014。

## 阶段 4 · 接真实模型，让评测真正承压 ⏳

这一阶段才会出现「中间地带」，也是项目从工程作业变成有说服力作品的关键。

- [x] 扩充评测集到 80 个用例，覆盖西安 / 洛阳 / 杭州 / 苏州 / 南京，含多风格、多人、节假日边界与 11 个不可满足需求
- [x] 显式处理 `INFEASIBLE`：生成前判定并返回原因、缺口、建议与理论下限（ADR 0007）
- [x] prompt v1 / v2 版本表 + `get_system_prompt()` + `--prompt-version` 评测参数，对比框架就绪
- [x] 成本/延迟报告：P50/P95、token 汇总、估算成本、修复轮次（离线成本恒 0，格式已验证）
- [ ] 用真实 OpenAI 兼容端点跑通 `SHOTPLAN_PROVIDER=openai_compatible`
- [ ] 记录 v1 / v2 prompt 在真实模型上的硬约束通过率差异，写进 `PROMPT_CHANGELOG`
- [x] 修复循环实验（离线）：一次修复成功率 100%（69/69）、69 次更差修复全被拦截，见 [repair-experiment.md](repair-experiment.md)
- [x] 失败案例库框架 + 离线对照组 517 条记录；真实模型典型错误待补，见 [failure-gallery.md](failure-gallery.md)
- [ ] 用真实模型数据填满成本/延迟对比表

验收：报告里出现真实模型的一行数据，且能与基线做 `--compare`；`docs/eval-report.md` 记录至少一次「改动导致分数下降并回滚」的实例。

**需要你做的**：申请一个 API key 并写入 `.env`，步骤见 [USER_TASKS.md](USER_TASKS.md) 第 4 节。不需要把 key 发给任何人。

## 阶段 5 · 上线与叙事 ⏳

- [ ] 在线 Demo 部署（默认 `mock` 模式，避免公开课暴露密钥）
- [ ] README 顶部放 Demo 链接与 30 秒演示动图
- [x] 可观测面板：按 provider / prompt 版本聚合通过率、延迟与成本（CLI / API / Web）
- [x] 静态展示页（GitHub Pages）：指标、分组结果、修复实验、失败案例与 ADR 入口一页呈现
- [x] 中文 + 英文 README 互链（投外企时切换）
- [ ] 简历 3 条 bullets 与 10 分钟讲稿定稿
- [ ] 按目标 JD 调整 README 关键词顺序

验收：任意一个陌生人通过链接能在不改代码、不配 key 的情况下完成一次生成并看到校验报告。

**需要你做的**：部署账号与域名，步骤见 [USER_TASKS.md](USER_TASKS.md) 第 5 节。

## 明确不做

多 Agent 辩论、向量数据库、用户系统、真实下单支付、移动端适配。理由：这些不会让「质量是否可测量」这个核心论证更可信，只会稀释它。若未来要做，先写一条 ADR 说明它解决的新问题。
