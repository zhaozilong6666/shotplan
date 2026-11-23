> 由 `python -m evals.stage_profile --write` 生成，请勿手工编辑。

# 单 worker 请求成本拆解

同一 brief 各跑 50 次；mock provider，1 worker，sync/off 各起一次服务。

## 总量

| 路径 | 均值 (ms) | 中位数 (ms) | trace wall (ms) | 非 pipeline (ms) |
| --- | ---: | ---: | ---: | ---: |
| in-process | 0.91 | 0.81 | 0.44 | — |
| HTTP off | 3.08 | 2.89 | 0.52 | 2.55 |
| HTTP sync | 17.16 | 15.99 | 0.59 | 16.57 |

- SQLite 同步提交（sync − off）：**14.09 ms/请求**
- HTTP/JSON 与客户端（off − in-process）：**2.17 ms/请求**
- pipeline（in-process）：**0.91 ms/请求**
- 注意：0.91 + 2.17 + 14.09 恰好等于 sync 总量——这是上面两个差值的定义式，不是独立校验

## 阶段均值

| 阶段 | in-process (ms) | HTTP off (ms) | HTTP sync (ms) |
| --- | ---: | ---: | ---: |
| `generate` | 0.17 | 0.18 | 0.20 |
| `parse` | 0.03 | 0.04 | 0.04 |
| `preflight` | 0.05 | 0.07 | 0.08 |
| `retrieve` | 0.03 | 0.05 | 0.06 |
| `validate` | 0.09 | 0.10 | 0.13 |

## 结论

- 最大的 pipeline 阶段是 `generate`（0.17 ms/请求）。mock provider 下 `generate` 只有亚毫秒级，别把离线数字当成真实模型延迟。
- 同步写库占 **82%** 的单请求时间；HTTP/JSON 占 **13%**。
- 这只解释「单 worker 为什么慢」，不解释「4 worker 为什么变快」；后者是多个进程摊薄了同一份单请求成本，见 write-lock-retest.md。

## 边界

mock provider、单机 loopback、无 TLS/反代；真实模型请求里 `generate` 会主导，本表的比例不适用于线上延迟。
