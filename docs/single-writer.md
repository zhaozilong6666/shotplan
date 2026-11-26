# 单写者 sidecar：实现、验证与否定结果

> 假设：多 worker 的 30s 尾延迟来自多个进程同时写 SQLite。做法是把所有写入集中到一个
> sidecar 进程，API 仍保留 4 个 worker。**结果是否定的**：sidecar 在 1 worker 下干净，
> 但 4 worker 仍有 11 次 `ReadTimeout`、最大 30s。因此写竞争不是唯一原因，不能把它当默认。

## 设计

```
uvicorn worker 1 ─┐
uvicorn worker 2 ─┼─ POST /save (loopback) ─→ writer_service (1 process)
uvicorn worker 3 ─┤                              │
uvicorn worker 4 ─┘                              └─ 单个 RunWriter → SQLite
```

- 行形状仍由 `RunStore.row_for()` 生成，API 和 sidecar 不维护两份 schema；
- sidecar 的 `/save` 把行交给 `RunWriter.submit_row(wait=True)`，并发请求共享一次 commit；
- API 通过 `SHOTPLAN_WRITER_URL` 启用 `RemoteRunSink`，失败映射为 503，不静默丢行；
- `writer_service` 是独立进程，没有 provider 密钥，也不做规划。

关键文件：

- `src/shotplan/writer_service.py`
- `src/shotplan/remote.py`
- `src/shotplan/storage.py`（`save_rows` / `submit_row`）
- `evals/soak.py --writer`
- `tests/test_writer_service.py`

## 验证结果

| 配置 | 运行 | 请求 | 错误 | 最大 | P99.9 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 worker，本地写 | 2h | 142,751 | 0 | — | 169ms | 稳定 |
| 4 worker，本地写 | 2h | 134,042 | 20 | 30s | — | 有长尾 |
| **1 worker + sidecar** | 10m | 11,957 | **0** | **512ms** | 434ms | 干净 |
| **4 worker + sidecar** | 30m | 32,650 | **11** | **30s** | 441ms | **仍有长尾** |
| **4 worker，`off`（完全不落库）** | 20m | 25,966 | **4** | **30s** | 38.6ms | **同样有长尾** |

4 worker + sidecar 的 11 个错误全是 `ReadTimeout`，行基本都已落库（32,648 行 / 32,650 请求），
不是丢写；15 个请求 >1s，14 个 >5s，最大 30.0s。和 4 worker 本地写的形态一致。

更关键的是 `off` 对照：4 worker **完全不落库**仍在 20 分钟内出现 4 次 30s 超时、5 个 >1s 请求。
所以尾延迟不依赖 SQLite、也不依赖 sidecar，**持久化整体可以被排除**。

## 结论

- sidecar **确实**把 SQLite 写者收敛为一个进程，1 worker 下干净、可复现；
- 但它**没有消除** 4 worker 的尾延迟；`off` 对照（0 次落库）仍有 4 次超时，说明
  “多进程写 SQLite”不是原因，持久化可以被排除；
- 尾延迟与 **API worker 数**相关，下一步应查 uvicorn 多进程 accept/调度、API 侧 httpx 连接池、
  Windows 调度与线程池，而不是继续改 SQLite；
- 当前默认仍是 **1 worker + batch**；sidecar 仅作为可选实验，不设为默认。

## 复现

```bash
# 可选 sidecar
uvicorn shotplan.writer_service:app --host 127.0.0.1 --port 8001 --workers 1

# 4 worker + sidecar，30 分钟
SHOTPLAN_WRITER_URL=http://127.0.0.1:8001 \
python -m evals.soak --minutes 30 --workers 4 --mode batch \
  --concurrency 6 --interval 0.25 --writer --write \
  --report docs/soak-writer-30.md
```

## 没解决什么

没有证明多 worker 尾延迟的根因，只排除了“SQLite 写竞争是唯一原因”。
要定位它，需要给 API 进程加 accept/queue/threadpool 指标，或在 1/2/4 worker 下做同样对照。
