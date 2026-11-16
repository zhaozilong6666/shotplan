# 上线就绪度评估

> 2026-11-15。回答「现在能不能部署到公网」，以及「哪些事只有上线后才知道」。
> 依据：[架构评审](architecture-review.md)、[HTTP 全链路冒烟](http-smoke.md)、
> [故障注入矩阵](fault-injection.md)、[SLO](slo.md)、[测量方法](measurement.md)。

## 结论

**代码层面可以部署；证据层面还没到可以宣称「生产可用」。**

> 更新（2026-11-15 晚）：代码已完整同步到 [github.com/zhaozilong6666/shotplan](https://github.com/zhaozilong6666/shotplan)，远端 `main` 与本地 SHA 一致。但**内置在线应用发布通道仍不可用**——平台分配的沙箱实例域名不响应（基础域 302 可达、实例子域连上后零字节），因此目前仍没有可直接体验产品的公网链接。别人可以 clone 后本地运行，这不是「已上线」。
阶段 9 的最后一项「部署到公网，跑一周真实流量」**尚未做**，
它不能由离线实验替代，也不能由本文档代替。

## 已经具备的（可核验）

| 能力 | 证据 |
| --- | --- |
| 全端点在真实 uvicorn 进程下可用 | `http_smoke` 10/10，门禁自动跑 |
| 配置单一真相源 | ADR 0017 + `test_config_truth.py`；API 真实调用里 trace 能解释实际请求 |
| 逐例有效 prompt 版本可追溯 | API 冒烟里 trace 同时有 `planner-v2` 与实际 `planner-v3` |
| 失败分类与降级 | 故障注入 12/12：429/502/超时/解析失败/锁库/磁盘满/知识库/校验器/502-503 映射 |
| 指标口径一致且不会被断供污染 | `/api/summary` `/api/stats` `/metrics` 三处统一；注入 error 后 pass 仍 1.0 |
| 知识库不会悄悄过期 | `check_knowledge_freshness.py`，超 180 天 CI 红 |
| 快照无法悄悄含凭据 | `check_snapshot_hygiene.py`，历史例外显式列出 |
| 门禁数量与文档不会漂移 | `check_gate_count.py`，26 道 |
| 质量与成本可见 | 26 道 `make gates` 全过、mypy/ruff 干净、全量测试通过 |
| 有可报警的线 | SLO 由 7 份快照推出，附 PromQL 与告警规则 |

## 上线前必须做（还没做）

1. **配置抓取端**：`/metrics` 全是滑动窗口 gauge，必须由 Prometheus 定期抓取并保存时间序列，
   否则 30 天误差预算无从计算。当前仓库没有 `scrape_configs` 或 recording rules 的部署配置。
2. **黑盒探针**：服务挂掉时自己不会报指标，`probe_success` / `up` 需要部署方提供。
3. **密钥注入方式**：`load_dotenv` 会把 `.env` 写进 `os.environ` 并被子进程继承
   （`http_load.py` 的 uvicorn 子进程、`cli --reload` worker）。公网部署应改用环境变量注入，
   不要挂载 `.env` 文件。参见架构评审 R11。
4. **反向代理注入 `X-API-Key`**：内置面板不发该头；若设了 `SHOTPLAN_API_KEY`，
   要么代理注入，要么换独立前端。Prometheus 抓取同理（3.x 可用 `http_headers`）。
5. **SLO 重新校准**：上线一周后用真实分布重跑 `slo_baseline --write`。
   现在的阈值来自 12–69 例的离线评测，不是生产流量。
6. **多进程写锁复核**：写锁已按 db path 分片，但多 worker 同时写同一库的行为
   只在 `http_load.py` 压测里量过一次；上线 worker 数变化后需要复测。

## 只有上线后才会暴露的（承认不知道）

- 真实 brief 的分布与 80 个离线用例差多少；红队集覆盖不了的措辞、方言、超长输入。
- 上游 provider 在真实并发下的限流曲线（离线评测并发只有 1–3）。
- 用户是否采纳建议（阶段 8 的「哪些建议被无视」需要真人试用）。
- 评分与人类判断是否一致（Cohen's kappa 需要两人独立标注 30 个方案）。
- 长尾延迟：`pipeline_budget_s` 已能约束 generate，但 urllib 的 socket timeout
  不是严格的整请求 deadline；慢速流式响应仍可能超出，只在下一次尝试前才被拦住。

## 最小部署检查清单

```bash
# 1. 全量门禁（26 道；CI 另跑 mypy 与展示页一致性）
make gates

# 2. 真实 HTTP 冒烟（会启动/关闭本地 uvicorn）
python -m evals.http_smoke --check

# 3. 启动前确认生效配置（密钥只显示布尔）
python -c "from shotplan.config import get_settings; import json; print(json.dumps(get_settings().resolved(), indent=2, ensure_ascii=False))"

# 4. 启动后逐项验证
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS -H 'X-API-Key: <key>' http://127.0.0.1:8000/metrics | grep shotplan_
curl -fsS -H 'X-API-Key: <key>' http://127.0.0.1:8000/api/rules | head
```

**验收标准（阶段 9）仍然是**：能给出一周真实流量的分布图，并指出一个只有上线后才暴露的问题。
这一条目前是空的，不能靠增加离线门禁来勾掉。
