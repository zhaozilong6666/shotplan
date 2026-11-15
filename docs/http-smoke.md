# HTTP 全链路冒烟

> 由 `python -m evals.http_smoke --write` 生成；`--check` 是门禁。
> 真实 `uvicorn` 子进程 + loopback 请求，不是 TestClient；零 API 成本（mock provider）。

| 端点 | 方法 | HTTP | 契约 | 结果 |
| --- | --- | ---: | --- | --- |
| `/api/health` | GET | 200 | 200 + status ok | PASS |
| `/api/config` | GET | 200 | masked config exposes provider | PASS |
| `/api/rules` | GET | 200 | at least 15 rules | PASS |
| `/api/plan` | POST | 200 | feasible brief -> status ok | PASS |
| `/api/plan` | POST | 200 | budget 100 -> structured infeasible | PASS |
| `/api/runs` | GET | 200 | both runs persisted | PASS |
| `/api/summary` | GET | 200 | status split: 1 ok + 1 infeasible, pass stays 1.0 | PASS |
| `/api/stats` | GET | 200 | dashboard agrees with summary | PASS |
| `/metrics` | GET | 200 | Prometheus text with status-aware gauges | PASS |
| `/` | GET | 200 | built-in web UI served | PASS |

**10/10 通过。**

## 为什么需要这一层

单元测试走 TestClient（进程内 ASGI），跳过 lifespan 的配置快照、loopback 网络栈、
真实 SQLite 文件与多端点之间的状态传递。`config.py` / `api.py` / `observability.py`
的改动恰恰最容易在这些地方出问题，而单测仍全绿。

重点验证的三条契约：

1. 预检拒绝（`infeasible`）会被持久化，但不算进质量分母：
   `total_runs=2, ok_runs=1, hard_pass_rate=1.0`。
2. `/api/summary`（全表 SQL）与 `/api/stats`（最近 N 条）在状态口径上一致。
3. `/metrics` 输出状态感知的 gauge（`error_rate` / `llm_cost_cny`），且是 `text/plain`。

## 覆盖边界

- 只用 mock provider，不验证真实模型调用；真实链路复测见 [architecture-review.md](architecture-review.md)。
- 单 worker、并发 1，不覆盖多进程写锁（见 [http-load.md](http-load.md)）。
- 不设 `SHOTPLAN_API_KEY`，鉴权矩阵另见 `tests/test_api.py`。
