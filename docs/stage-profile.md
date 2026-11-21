> 由 `python -m evals.stage_profile --write` 生成，请勿手工编辑。

# 单 worker 请求成本拆解

同一 brief 各跑 50 次；mock provider，1 worker，sync/off 各起一次服务。

## 总量

| 路径 | 均值 (ms) | 中位数 (ms) | trace wall (ms) | 非 pipeline (ms) |
| --- | ---: | ---: | ---: | ---: |
| in-process | 3.64 | 1.95 | 2.36 | — |
| HTTP off | 6.92 | 5.98 | 1.23 | 5.69 |
| HTTP sync | 27.31 | 25.08 | 0.81 | 26.50 |

- SQLite 同步提交（sync − off）：**20.39 ms/请求**
- HTTP/JSON 与客户端（off − in-process）：**3.27 ms/请求**
- pipeline（in-process）：**3.64 ms/请求**
- 三部分合计与 sync 的差（噪声）：0.00 ms

## 阶段均值

| 阶段 | in-process (ms) | HTTP off (ms) | HTTP sync (ms) |
| --- | ---: | ---: | ---: |
| `generate` | 0.34 | 0.40 | 0.27 |
| `parse` | 0.07 | 0.07 | 0.06 |
| `preflight` | 1.56 | 0.33 | 0.15 |
| `retrieve` | 0.08 | 0.09 | 0.07 |
| `validate` | 0.16 | 0.18 | 0.14 |

## 结论

- 最大的 pipeline 阶段是 `preflight`（1.56 ms/请求）。mock provider 下 `generate` 只有亚毫秒级，别把离线数字当成真实模型延迟。
- 同步写库占 **75%** 的单请求时间；HTTP/JSON 占 **12%**。
- 这只解释「单 worker 为什么慢」，不解释「4 worker 为什么变快」；后者是多个进程摊薄了同一份单请求成本，见 write-lock-retest.md。

## 边界

mock provider、单机 loopback、无 TLS/反代；真实模型请求里 `generate` 会主导，本表的比例不适用于线上延迟。
