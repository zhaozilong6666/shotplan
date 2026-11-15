# SLO 与监控查询

> 依据：[SLO 实测基线](slo-baseline.md)（由 `evals/slo_baseline.py` 从 7 份真实模型快照生成并纳入门禁）、
> [故障注入矩阵](fault-injection.md)、[可观测性](observability.md)。
> 本文是**初始 SLO**，不是生产验证过的 SLO。

## 先纠正一个错误假设

路线图阶段 9 曾把「95% 请求在 30s 内返回且不 5xx」当作 SLO 示例。实测：

| 指标 | 观测范围（7 份快照） |
| --- | --- |
| P50 | 40.5 – 91.5 s |
| P95 | 63.9 – 171.8 s |

**这个目标从写下那天起就不可能达成。** 一个永远红色的告警会训练所有人忽略告警，
比没有告警更糟。所以 SLO 必须由已归档快照推出，且随模型/供应商变更重新校准。

## SLI 与 SLO

| SLI | 定义 | 初始 SLO | 依据 |
| --- | --- | --- | --- |
| 延迟 P50 | 调用方墙钟耗时 | ≤ 95 s | 实测最大 91.5 s |
| 延迟 P95 | 调用方墙钟耗时 | ≤ 180 s | 实测最大 171.8 s（长白天红队集） |
| 质量 | `hard_pass_rate`，**只统计 `status=ok`** | ≥ 90% | 实测 91.7–100% |
| 上游健康 | `error_rate`（provider 断供） | ≤ 5% | 实测 0–5% |
| 成本 | 单次生成的 LLM token 花费 | ≤ ¥0.08 | 实测 0.0231–0.0632 |
| 服务可用性 | 返回结构化结果（含降级为 `status=error`）且非 5xx | ≥ 99.5% | 故障注入矩阵：所有注入故障都不冒 500 |

**可用性口径的关键**：provider 502 被降级成 `200 + status=error`，
API 只在 `build_provider` 自身抛出时才映射 502/503（见故障注入矩阵）。
所以「上游挂了」不等于「服务不可用」；把两者混在一个 SLI 里，会让上游抖动直接烧掉
服务可用性预算，也会掩盖真正的服务故障。

## 误差预算

以上游错误率 SLO ≤ 5% 为例：30 天窗口内允许 5% 的请求因 provider 失败降级。
按每天 1000 次请求估算，预算是 **45,000 次/30 天**。
用 `error_rate` 的滑动窗口 gauge 无法算 30 天累计——必须由抓取端保存时间序列后积分：

```promql
# 30 天内上游错误率的累计口径（依赖 TSDB 保存历史样本）
sum_over_time(shotplan_error_rate[30d]) / count_over_time(shotplan_error_rate[30d])
```

## 监控查询

`/metrics` 的指标全是**滑动窗口 gauge**（`window` 标签明示口径），不是 counter。
所以 `rate()` 在这里是错的，直接读瞬时值：

```promql
# 延迟：直接读分位数 gauge（单位毫秒）
shotplan_duration_ms{quantile="0.95"}
shotplan_duration_ms{quantile="0.5"}

# 质量：只看真正产出方案的运行
shotplan_hard_pass_rate
shotplan_ok_runs / shotplan_runs

# 上游故障与预检拒绝分开看
shotplan_error_rate
shotplan_infeasible_rate

# 按 provider / 实际 prompt 版本定位是哪条链路退化
shotplan_group_hard_pass_rate
shotplan_group_p95_duration_ms

# 成本：LLM token 花费，不是拍摄方案价格
shotplan_llm_cost_cny
```

服务可用性需要**外部黑盒探针**，不能靠 `/metrics` 自报——服务挂了就不会有指标：

```promql
probe_success{job="shotplan-health"}
up{job="shotplan"}
```

## 告警规则

```yaml
groups:
  - name: shotplan
    rules:
      - alert: ShotplanScrapeDown
        expr: up{job="shotplan"} == 0
        for: 2m
        labels: {severity: critical}
        annotations:
          summary: "抓取不到 /metrics，服务可能已挂（指标自报不了自己的死亡）"

      - alert: ShotplanLatencyHigh
        expr: shotplan_duration_ms{quantile="0.95"} > 180000
        for: 15m
        labels: {severity: warning}
        annotations:
          summary: "P95 超过 180s；对照 slo-baseline.md 检查是否换了模型或 prompt 变长"

      - alert: ShotplanQualityRegression
        expr: shotplan_hard_pass_rate < 0.90
        for: 30m
        labels: {severity: warning}
        annotations:
          summary: "硬约束通过率跌破 90%；先确认 ok_runs 是否足够，再查规则变更"

      - alert: ShotplanUpstreamErrorsHigh
        expr: shotplan_error_rate > 0.05
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "上游错误率超过 5% 误差预算；注意不要把它算成模型质量退化"

      - alert: ShotplanCostHigh
        expr: shotplan_llm_cost_cny / shotplan_runs > 0.08
        for: 30m
        labels: {severity: warning}
        annotations:
          summary: "单次调用成本超过 ¥0.08；检查 max_tokens / adaptive budget"
```

`for` 用得比较长，是因为 `/metrics` 是滑动窗口 gauge、样本量小（默认最近 200 条），
短窗口的抖动不代表 SLO 被击穿。

## 已知限制

1. **样本不是生产流量**：7 份离线快照、每次 12–69 个用例。上线一周后必须用真实分布重算阈值，
   并重新跑 `--write` 更新基线（门禁会强制这件事）。
2. **gauge 不是 histogram**：`shotplan_duration_ms` 只有 P50/P95 两个预计算分位数，
   不能算任意分位，也不能做 `histogram_quantile()`。要多分位就得改导出格式。
3. **窗口边界**：`window` 是「最近 N 条」而非时间窗口，且 `created_at` 秒精度已加 `run_id` tiebreak；
   请求量突变时窗口覆盖的时间跨度会变，跨时段比较要小心。
4. **可用性依赖外部探针**：仓库内没有 blackbox exporter 配置，探针需由部署方提供。
5. **故障注入是模拟**：矩阵用注入异常验证降级路径，不等于真的把磁盘写满或锁库；
   真实并发写锁行为见 [loadtest.md](loadtest.md)、[persistence.md](persistence.md)。
