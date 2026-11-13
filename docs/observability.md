# 可观测性：按 provider / prompt 版本聚合

## 它回答什么问题

评测报告回答「当前代码质量如何」，运行统计回答「**线上/本机这段时间实际跑得怎么样**」：哪个 provider、哪个 prompt 版本产生了多少硬约束失败、延迟分位数、修复轮次、结构化拒绝有多少。两者不能互相替代 —— 前者是可复现实验，后者是实际使用记录。

## 数据来源

每次 `run_plan()` 都会把完整结果（request / plan / validation / trace / status / feasibility）写入 SQLite（`data/shotplan.db`）。`observability.build_dashboard()` 直接读这些 payload，不维护第二份指标表，因此**只能报告系统真实记录过的东西**，也无需额外的埋点服务。

字段映射：

| 面板指标 | 来源 |
| --- | --- |
| `status` | payload 顶层 `status`（`ok` / `infeasible`） |
| `provider` / `prompt_version` | `trace` |
| 硬约束通过率、均分 | `validation.hard_pass` / `validation.score` |
| P50 / P95 延迟 | `trace.total_duration_ms` |
| 估算成本 | `trace.total_cost_cny` |
| 修复轮次 | `trace.repair_count` |

## 聚合口径

- 分组键是 `(provider, prompt_version)`，按字母序输出。
- `overall` 覆盖窗口内全部运行；`window` 默认 200 条，`--limit` / `?limit=` 可调，上限 1000。
- **`infeasible` 运行计入统计**：它的 `hard_pass=false`、`score=0`，因此会拉低总体通过率。这是刻意的 —— 把「需求不可满足」藏起来，等于把一类真实的系统行为从监控里删掉。
- 离线 provider 的估算成本恒为 0；接真实模型后这里才会出现有意义的成本对比。
- 评分与聚合沿用 `math.fsum` + 固定精度（ADR 0006），分位数用最近秩法（nearest-rank），窗口内样本少时 P95 与最大值相同。

## 复现

```bash
python -m shotplan.cli plan --persist          # 产生一条记录
python -m shotplan.cli stats --limit 50        # 打印 JSON 面板

python -m shotplan.cli serve                   # 另开终端
curl http://127.0.0.1:8000/api/stats          # 同一份数据
```

Web 界面的「运行统计」标签页渲染的就是 `/api/stats`：表格按 provider / prompt 版本列出运行数、通过率、均分、P50/P95、成本、修复轮次与不可行数量。

同一页还有一块**持久化面板**（数据来自 `/api/config`）：显示 `persist_mode`；`async` 模式下额外显示写队列的 `pending / written / failed / sync_fallbacks / batches / max_batch`。`pending > 0` 是黄色（正常，几秒内归零），`failed > 0` 是红色（磁盘问题，需要处理），`sync_fallbacks > 0` 表示队列曾满、退化为同步写。每次切到该页都会重新拉取，便于演示时观察积压排空。

## 边界

- 单进程 SQLite，没有并发写入与用户隔离；这是明确的设计取舍（ADR 0001、README 已知限制）。
- 没有时间序列图、没有告警、没有按 brief 维度下钻。当前目标是让「质量与成本可见」，不是造一套监控系统。
- 运行历史被 `.gitignore` 排除，不会随仓库提交；面板展示的是本机数据，不是 CI 数据。CI 的质量结论请看 [eval-report.md](eval-report.md)。
