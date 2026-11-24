# 长跑报告索引

> 手写索引；各份报告本身由 `python -m evals.soak --write` 生成。这份页面的唯一职责是回答
> 「哪份是当前结论、哪份是当时的快照、新加一份时它在验证什么」。

## 当前结论

- [soak.md](soak.md) —— `make soak` 的默认入口（30 分钟、**1 worker**、`batch`），每次覆盖，代表**当前默认配置**。
- [soak-ab.md](soak-ab.md) —— 1 vs 4 worker **交替**对照，回答「长尾是否与 worker 数相关」；`--write` 覆盖，`--rerender` 只重算报告。

## 快照（按时间）

| 报告 | 配置 | 结果 | 它回答的问题 |
| --- | --- | --- | --- |
| [soak-sync-30.md](soak-sync-30.md) | `sync`、4 worker、1.0s | 1722/1722、0 错误 | sync 模式长跑是否稳定 |
| [soak.md](soak.md) | `batch`、**1 worker**、1.5s、30 分钟 | **1154/1154、0 错误、0 慢请求** | 当前默认配置基线（E29 后从 4 worker 切换） |
| [soak-batch-60.md](soak-batch-60.md) | `batch`、4 worker、1.0s、60 分钟 | 3402/3402、0 错误 | 时间拉长到 1 小时是否劣化 |
| [soak-batch-120.md](soak-batch-120.md) | `batch`、4 worker、1.0s、120 分钟 | 6784/6784、0 错误 | 时间拉长到 2 小时是否劣化 |
| [soak-restart-30.md](soak-restart-30.md) | `batch`、4 worker、第 10 分钟硬杀 | 1730/1730、1 次重启 | 进程死后自动重启是否丢写 |
| [soak-high-60.md](soak-high-60.md) | `batch`、4 worker、并发 6、0.25s | 71887 请求、6 个 `ReadTimeout` | 更高写入压力下有没有长尾 |
| [soak-high-120.md](soak-high-120.md) | 同上、120 分钟 | 134042 请求、20 个 `ReadTimeout` | 长尾是否随时间恶化 |
| [soak-high-120-1w.md](soak-high-120-1w.md) | 同负载、**1 worker** | 142751/142751、0 错误 | 同负载换成单进程后长尾是否消失 |
| [soak-ab.md](soak-ab.md) | 1/4 worker **交替**、并发 8、0.1s | 见报告 | 单次对照能否在交替实验中复现（E29） |

## 新增一份之前先回答

按 [report-audit.md](report-audit.md) 的命名规则：`X.md` 是当前结论（覆盖），`X-<差异>.md` 是快照。
新增快照要能回答「这次与上一次的假设有什么不同」；只是把时长再加一小时，不构成新的假设。
