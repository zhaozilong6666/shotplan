# 可观测性：按 provider / prompt 版本聚合

## 它回答什么问题

评测报告回答「当前代码质量如何」，运行统计回答「**线上/本机这段时间实际跑得怎么样**」：哪个 provider、哪个 prompt 版本产生了多少硬约束失败、延迟分位数、修复轮次、结构化拒绝有多少。两者不能互相替代 —— 前者是可复现实验，后者是实际使用记录。

## 数据来源

每次 `run_plan()` 都会把完整结果（request / plan / validation / trace / status / feasibility）写入 SQLite（`data/shotplan.db`）。`observability.build_dashboard()` 只读取轻量列（provider / prompt / status / score / duration / cost / repairs），不再解析完整 payload；完整记录仍可通过 `/api/runs/<id>` 查看。这样统计成本不随历史体积线性增长，也不需要第二份指标表。

保留策略是可选的：`SHOTPLAN_RETENTION_DAYS=N` 在 API 启动时删除超过 N 天的记录（默认 0 = 永久保留），`python -m shotplan.cli history --prune-days N` 可手动执行；`history --prune N` 仍按条数保留最近 N 条。

注意 SQLite 的 `DELETE` 不会把空间还给操作系统，WAL 与已用页仍占用磁盘；需要回收时应在停服状态执行 `VACUUM`（`sqlite3 data/shotplan.db "VACUUM;"`），不要在请求路径里做——它会长时间锁库。这也是没有实现「按文件体积自动清理」的原因：按时间/条数删除是可控的，按体积删除还依赖一次昂贵的重写。

字段映射：

| 面板指标 | 来源 |
| --- | --- |
| `status` | `runs.status`（`ok` / `infeasible` / `error`） |
| `provider` / `prompt_version` | `runs.provider` / `runs.prompt_version` |
| 硬约束通过率、均分 | `runs.hard_pass` / `runs.score` |
| P50 / P95 延迟 | `runs.duration_ms`（端到端墙钟） |
| 估算成本 | `runs.cost_cny` |
| 修复轮次 | `runs.repair_count` |

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
python -m shotplan.cli history --prune 1000    # 只保留最近 1000 条（可选）
python -m shotplan.cli history --prune-days 90 # 删除超过 90 天的记录（可选）

python -m shotplan.cli serve                   # 另开终端
curl -H "X-API-Key: $SHOTPLAN_API_KEY" http://127.0.0.1:8000/api/stats
```

Web 界面的「运行统计」标签页渲染的就是 `/api/stats`：表格按 provider / prompt 版本列出运行数、通过率、均分、P50/P95、成本、修复轮次与不可行数量。

同一页还有一块**持久化面板**（数据来自 `/api/config`）：显示 `persist_mode`；`async` 模式下额外显示写队列的 `pending / written / failed / sync_fallbacks / batches / max_batch`。`pending > 0` 是黄色（正常，几秒内归零），`failed > 0` 是红色（磁盘问题，需要处理），`sync_fallbacks > 0` 表示队列曾满、退化为同步写。每次切到该页都会重新拉取，便于演示时观察积压排空。

## 日志与请求追踪

配置在启动时解析一次（`app.state.settings`），每个 HTTP 请求在 `shotplan.api` logger 上留下**一行**结构化访问日志：

```text
request_id=4f1c2a7d9e0b4c11 method=POST path=/api/plan status=200 duration_ms=31.4
```

`request_id` 优先取客户端或反向代理传的 `X-Request-ID`，只接受 `[A-Za-z0-9._-]{1,64}`（避免伪造日志行或注入响应头），否则由服务端生成 16 位十六进制串；响应头始终回传该 id，429 与 503 的 JSON body 也带 `request_id`，`/api/plan` 还会把它写进 `trace.config.request_id`——从一条历史运行可以直接反查产生它的日志行。CLI 与评测不传该值，因此离线产物保持确定性。日志级别由 `SHOTPLAN_LOG_LEVEL` 控制，默认 INFO；`/api/health` 同样记录，容器健康检查也可追踪。

**CORS 是刻意不开启的**：仓库自带的界面与 API 同源，开启通配符只会让任意页面能调用一个未设 key 的本地实例。接独立前端时应在 `api.py` 里加显式白名单，而不是 `allow_origins=["*"]`。

## 不在规则表里的运维码

`/api/rules` 返回的是**方案质量规则**（15 条：10 硬 + 5 软），全部由 `domain/constraints.py` 的 `RULES` 定义，用于判定一份行程能不能执行。

有一个码故意不进去：`PERSISTENCE_FAILED`。它由流水线在写库失败时追加为 **soft** issue——磁盘满、库被锁、路径无权限都属于运维事件，和行程本身对不对无关。把它塞进规则表会让客户端误以为这是一条可被模型修复的校验规则，所以它只出现在运行结果的 `validation.issues` 里，语义是「这次结果有效，但没存下来」。

看到它时该查的是 `SHOTPLAN_DB_PATH`、磁盘空间和写权限，不是让模型重写方案。

## 边界

- 单进程 SQLite，没有并发写入与用户隔离；这是明确的设计取舍（ADR 0001、README 已知限制）。
- 没有时间序列图、没有告警、没有按 brief 维度下钻。当前目标是让「质量与成本可见」，不是造一套监控系统。
- 运行历史被 `.gitignore` 排除，不会随仓库提交；面板展示的是本机数据，不是 CI 数据。CI 的质量结论请看 [eval-report.md](eval-report.md)。

## Prometheus 抓取

`GET /metrics`（与 `/api/stats` 同样受 `X-API-Key` 保护）返回 `text/plain; version=0.0.4`，无需额外依赖：

```
curl -H 'X-API-Key: <key>' 'http://127.0.0.1:8000/metrics?limit=200'
```

| 指标 | 类型 | 含义 |
| --- | --- | --- |
| `shotplan_runs{window}` | gauge | 观察窗口内运行数 |
| `shotplan_hard_pass_rate{window}` | gauge | 硬约束通过率 |
| `shotplan_avg_score{window}` | gauge | 平均得分 |
| `shotplan_duration_ms{window,quantile}` | gauge | P50 / P95 耗时（毫秒） |
| `shotplan_repairs_total{window}` | gauge | 修复尝试次数 |
| `shotplan_cost_cny_total{window}` | gauge | 估算花费（元） |
| `shotplan_runs_by_status{status}` | gauge | 按 ok / error / infeasible 分类 |
| `shotplan_group_*` | gauge | 按 `provider` + `prompt_version` 下钻 |

**全部是 gauge，不是 counter。** 数据来自 `build_dashboard()` 的最近 N 条滑动窗口，把它标成 counter 会让 `rate()` 算出假的增速。`window` 标签明示了口径，换 `limit` 时数值会随之变化，因此不适合做长期累计告警——要长期趋势应由抓取端按时间序列自行累加。

标签值里的反斜杠、双引号、换行都会被转义，避免畸形文本或注入。空库也会输出完整的 HELP/TYPE 行与零值，抓取端不会拿到空响应。

配 Prometheus 时：3.x 可用 `http_headers` 直接注入 `X-API-Key`；更早版本没有这个字段，需由反向代理注入该头，或让抓取端走一个带头的代理。未设置 `SHOTPLAN_API_KEY` 时，该端点与 `/api/health`、`/api/config`、`/api/rules` 一样匿名可读。
