# 性能基准

> 由 `python -m evals.benchmark --write-report` 生成，请勿手工编辑。

## 结论

- 单 brief 重复 300 次（已预热 5 次）：**p50 0.22ms / p95 0.367ms / p99 0.475ms**
- 吞吐 **4106.38 次/秒**，Python 堆峰值 **0.02MB**
- 全评测集 80 个真实 brief 扫一遍：p95 0.296ms，耗时 0.018s

## 分步耗时（平均每次）

| 步骤 | 毫秒 | 占比 |
|---|---|---|
| `validate` | 0.038 | 27.8% |
| `generate` | 0.028 | 20.8% |
| `parse` | 0.024 | 17.4% |
| `preflight` | 0.023 | 17.1% |
| `retrieve` | 0.023 | 16.9% |

## 运行环境

- Python 3.13.14 · Windows 11
- 机器：AMD64 / Intel64 Family 6 Model 170 Stepping 4, GenuineIntel

## 怎么读这些数字

这是**离线规则 provider** 的开销，不是模型延迟。它测的是检索、解析、校验、
修复循环与 trace 的常数成本——真实模型的端到端延迟由 provider 决定，会在
`docs/cost-latency-simulation.md` 的价格卡里单独估算。

门禁设在 `p95 < 50ms`、`吞吐 > 50 次/秒`、`堆峰值 < 64MB`，比实测宽一个数量级：
目的是抓「意外的 O(n²)」「每次调用重载知识库」这类回归，而不是卡 CI 抖动。

展示页读取的是仓库里提交的快照 `evals/benchmark.json`（本机测得），而不是 CI 的
实时结果——时间数字在不同机器上无法复现，放进一致性门禁只会造成假失败。
刷新快照：`python -m evals.benchmark --out evals/benchmark.json --write-report`。

## 已知限制

- 内存用 `tracemalloc` 只统计 Python 堆，不含解释器与 C 扩展的常驻内存
- 单机测量，未做并发压测；API 是 async 但流水线是同步计算
- 数字随机器变化，跨机器比较无意义，只与同机历史版本比较