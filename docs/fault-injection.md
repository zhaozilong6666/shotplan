# 故障注入矩阵

> 由 `python -m evals.fault_injection --write` 生成；`--check` 是门禁。
> 全部离线，零 API 成本。每行都驱动真实的 `run_plan` / API，只注入一处故障。

绿色不等于「没抛异常」，而是「失败被分类、可追溯、且没有污染质量指标」。

| 场景 | 类别 | 注入 | 观测结果 | 结论 |
| --- | --- | --- | --- | --- |
| HTTP 429 限流 | `provider` | `ProviderError` | `error` | PASS |
| HTTP 502 上游 | `provider` | `ProviderError` | `error` | PASS |
| 连接超时 | `provider` | `TimeoutError` | `error` | PASS |
| 模型输出无法解析 | `provider` | `ParseError` | `error` | PASS |
| SQLite 锁 | `persistence` | `StorageError` | `ok` | PASS |
| 磁盘满 ENOSPC | `persistence` | `OSError` | `ok` | PASS |
| 目录无权限 | `persistence` | `PermissionError` | `ok` | PASS |
| 知识库读取失败 | `runtime` | `KnowledgeError` | `error` | PASS |
| 校验器意外异常 | `runtime` | `RuntimeError` | `error` | PASS |
| 断供不污染质量分母 | `metrics` | `1 ok + 1 error` | `pass=1.0 error_rate=0.5` | PASS |
| API 上游失败 -> 502 | `api` | `ProviderError` | `502` | PASS |
| API 存储故障 -> 503 | `api` | `StorageError` | `503` | PASS |

**12/12 通过。**

## 关键契约

- provider 超时 / 429 / 5xx / 解析失败 → `status=error` 且带 `PIPELINE_ERROR`，
  不是 500，也不是一个看起来正常的低分方案。
- SQLite 锁 / 磁盘满 / 无权限 → 方案仍返回 `status=ok`，追加 `PERSISTENCE_FAILED` **soft** issue。
  持久化是辅助能力，不能否决已经算出来的结果（`_safe_save` 同时捕获 `StorageError` 与 `OSError`）。
- 预检、知识库、校验器等未设局部守卫的阶段抛异常，由 `run_plan` 顶层边界统一兜底。
- 一条 provider 断供 + 一条正常方案 → `hard_pass_rate` 仍是 1.0、`error_rate` 是 0.5。
  基础设施故障不算模型质量差。
- API 层 `ProviderError → 502`、`StorageError → 503`，与客户端输入错误（422）区分。

## 覆盖边界

- 用注入的异常模拟故障，不是真的把磁盘写满或锁住数据库文件；
  真实并发写锁行为另见 [loadtest.md](loadtest.md) 与 [persistence.md](persistence.md)。
- 未覆盖进程被 SIGKILL、网络分区、DNS 劫持等需要真实环境的情况。
