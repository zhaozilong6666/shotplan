> 由 `python -m evals.stage_profile --write` 生成，请勿手工编辑。

# 单 worker 请求成本拆解

同一 brief 各跑 50 次；mock provider，1 worker，sync/off 各起一次服务。

## 总量

| 路径 | 均值 (ms) | 中位数 (ms) | trace wall (ms) | 非 pipeline (ms) |
| --- | ---: | ---: | ---: | ---: |
| in-process | 1.09 | 0.89 | 0.48 | — |
| HTTP off | 3.32 | 2.80 | 0.61 | 2.71 |
| HTTP sync | 25.89 | 25.14 | 0.64 | 25.25 |

- SQLite 同步提交（sync − off）：**22.58 ms/请求**
- HTTP/JSON 与客户端（off − in-process）：**2.22 ms/请求**
- pipeline（in-process）：**1.09 ms/请求**
- 三部分合计与 sync 的差（噪声）：0.00 ms

## 阶段均值

| 阶段 | in-process (ms) | HTTP off (ms) | HTTP sync (ms) |
| --- | ---: | ---: | ---: |
| `generate` | 0.17 | 0.20 | 0.21 |
| `parse` | 0.04 | 0.04 | 0.04 |
| `preflight` | 0.07 | 0.12 | 0.13 |
| `retrieve` | 0.04 | 0.05 | 0.06 |
| `validate` | 0.08 | 0.11 | 0.11 |

## 结论

- 最大的 pipeline 阶段是 `generate`（0.17 ms/请求）。mock provider 下 `generate` 只有亚毫秒级，别把离线数字当成真实模型延迟。
- 同步写库占 **87%** 的单请求时间；HTTP/JSON 占 **9%**。
- 这只解释「单 worker 为什么慢」，不解释「4 worker 为什么变快」；后者是多个进程摊薄了同一份单请求成本，见 write-lock-retest.md。

## 边界

mock provider、单机 loopback、无 TLS/反代；真实模型请求里 `generate` 会主导，本表的比例不适用于线上延迟。
