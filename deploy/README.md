# 部署配置

这里只放**能被 CI 检查**的部署物；不能检查的（真实 Prometheus、TLS、反向代理）
写在 [docs/release-readiness.md](../docs/release-readiness.md)，不在这里假装完成。

## Prometheus

| 文件 | 作用 |
| --- | --- |
| `prometheus/shotplan.yml` | 抓取配置：`/metrics`、15s 间隔、加载告警规则 |
| `prometheus/shotplan.rules.yml` | 5 条告警；**由 `docs/slo.md` 的 YAML 块生成，不要手改** |
| `prometheus/blackbox.yml` | blackbox_exporter 的 `http_2xx` 模块；接上 exporter 后启用注释里的 job |

```bash
python scripts/check_deploy_config.py --write   # 从 docs/slo.md 重新生成规则
python scripts/check_deploy_config.py --check   # CI：规则过期、指标名不存在、告警缺字段都会红
promtool check config deploy/prometheus/shotplan.yml   # 有 Prometheus 时的最终校验
```

`--check` 能验证的是结构：抓取路径、规则与文档同源、每条告警有 `expr/for/severity/summary`、
表达式里的 `shotplan_*` 指标确实在 `observability.py` 里存在。它**不**验证 PromQL 语义，
那需要 `promtool` 或一个真实 Prometheus。

## 密钥

`/metrics` 在设置了 `SHOTPLAN_API_KEY` 时要求 `X-API-Key`。仓库里不写密钥：

- 推荐让反向代理注入该头，Prometheus 只抓代理；
- Prometheus 3.x 也可用 `http_headers`，值由配置管理/secret manager 提供（`shotplan.yml` 里有注释示例）。

`.env` 只读进 `shotplan.config` 的私有字典，不会写 `os.environ`，所以子进程不会顺带继承；
生产仍应使用环境变量注入，并可设 `SHOTPLAN_SKIP_DOTENV=1` 彻底忽略 `.env`。

## 黑盒探针

`/metrics` 报不了自己的死亡（服务挂了就没有指标）。`blackbox.yml` 已给出 `http_2xx` 模块，
`shotplan.yml` 里保留了 `shotplan-health` 的注释 job；启动 `blackbox_exporter` 后取消注释即可。
没有 exporter 时不要启用，否则 `up{job="shotplan-health"}` 会永久为 0。

## 还没做的

- Prometheus 实际部署与 30 天误差预算：需要真实流量，离线门禁替代不了。
- SLO 阈值重校准：上线一周后用 `evals.slo_baseline --write` 重跑。
