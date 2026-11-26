# 长跑报告索引

> 手写索引；各份报告本身由 `python -m evals.soak --write` 生成。这份页面的唯一职责是回答
> 「哪份是当前结论、哪份是当时的快照、新加一份时它在验证什么」。

## 归纳

同一套 HTTP 长跑（mock provider、loopback、`batch`）的证据已经收敛：

| 配置 | 运行 | 结果 |
| --- | ---: | --- |
| **1 worker + batch** | 5h04m | 195454/195454，0 错误、0 重启、0 丢写；P99.9 169ms，最大 490ms |
| **1 worker + batch** | 2h00m | 142751/142751，0 错误、0 丢写 |
| **1 worker + batch** | 4h24m（手动截止） | 164956/164956，0 错误、0 丢写；服务端 P99.9 5.09ms |
| 2 worker + batch | 1h00m | 69103 请求，11 个错误；P99.9 218ms，最大 30s |
| 4 worker + batch | 2h00m | 134042 请求，20 个错误；第 10 分钟就开始出现 30s 级尾延迟 |

**结论**：当前 SQLite 写模型下，`batch` 配 **1 worker** 是唯一有长跑证据支持的稳定配置；
2 worker 起就出现多进程写竞争，不应把 worker 数当作吞吐旋钮。要扩多 worker，先做单写者/外部队列，
而不是改 `--workers`。

**边界**：mock provider、单机 loopback、10–20 req/s；不是容量上限，也没有覆盖真实模型延迟与公网。

单写者 sidecar 的对照见 [single-writer.md](single-writer.md)：它把写者收敛为一个进程，1 worker 下干净，但 **4 worker 仍有 11 次超时/30 分钟**，所以不能把尾延迟全部归因于 SQLite 写竞争。
进一步用 `off`（完全不落库）排除持久化：4 worker 20 分钟仍有 4 次 30s 超时，说明根因在 API 多进程/调度侧，不在 SQLite。

## 当前结论

- [soak.md](soak.md) —— `make soak` 的默认入口（30 分钟、**1 worker**、`batch`），每次覆盖，代表**当前默认配置**。
- [soak-ab.md](soak-ab.md) —— 1 vs 4 worker **交替**对照，回答「长尾是否与 worker 数相关」；`--write` 覆盖，`--rerender` 只重算报告。

## 快照（按时间）

| 报告 | 配置 | 结果 | 它回答的问题 |
| --- | --- | --- | --- |
| [soak-sync-30.md](soak-sync-30.md) | `sync`、4 worker、1.0s | 1722/1722、0 错误 | sync 模式长跑是否稳定 |
| [soak.md](soak.md) | `batch`、**1 worker**、1.5s、30 分钟 | **1154/1154、0 错误、0 慢请求** | 当前默认配置基线（E29 后从 4 worker 切换） |
| [soak-partial-4h24.md](soak-partial-4h24.md) | `batch`、1 worker、并发 6、0.5s | 164956/164956、0 错误、0 丢写 | 8 小时计划被手动截止在 4h24m；已从残留 SQLite 补算服务端分位 |
| [soak-45m.md](soak-45m.md) | `batch`、1 worker、并发 6、0.5s、45 分钟 | **28816/28816、0 错误、0 慢请求** | 按 21:00 截止的完整替代运行 |
| [soak-5h.md](soak-5h.md) | `batch`、1 worker、并发 6、0.5s、304 分钟 | **195454/195454、0 错误、0 慢请求** | 目前最长的完整干净长跑 |
| [soak-off-4w-20m.md](soak-off-4w-20m.md) | `off`、4 worker、并发 6、20 分钟 | 25966 请求、4 个错误、最大 30s | 排除持久化：不落库仍有尾延迟 |
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
