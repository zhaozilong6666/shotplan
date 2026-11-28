# 部署配置

这里只放**能被 CI 检查**的部署物；不能检查的（真实 Prometheus、TLS、反向代理）
写在 [docs/release-readiness.md](../docs/release-readiness.md)，不在这里假装完成。

## systemd（无人值守）

`deploy/shotplan.service` 是一个可直接启用的 unit：`Restart=always`、`RestartSec=5`，
崩溃或 OOM 后由 systemd 拉起；`StateDirectory=shotplan` 把数据库放在
`/var/lib/shotplan`，`EnvironmentFile=/etc/shotplan.env` 注入密钥与配置。

```bash
sudo useradd --system --home /opt/shotplan --shell /usr/sbin/nologin shotplan
sudo install -d -o shotplan -g shotplan /opt/shotplan
# 把仓库放到 /opt/shotplan，建好 .venv 并安装 .[api]
sudo cp deploy/shotplan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shotplan
systemctl status shotplan
```

长跑验证：`make soak`（默认 30 分钟、**1 worker**、batch 持久化），报告写到
`docs/soak.md`；它每 30 秒探活、连续失败 3 次或进程退出会自动重启，并在结束时
核对「成功请求数 = 落库行数」。全部长跑报告的配置与结论见
[docs/soak-index.md](../docs/soak-index.md)。

### worker 数怎么选

unit 的默认是 **`batch` + 1 worker**，这是长跑验证过的稳定组合；**代码/CLI 的默认仍是
`sync`**（每个请求 commit 后才返回）。`SHOTPLAN_WORKERS` 固定在 1，不要用 `--workers N`
扩 API；需要并发时走“独立端口 + 反向代理”。

| 持久化模式 | 建议 | 依据 |
| --- | --- | --- |
| `batch`（unit 默认） | **1 worker** | 2 worker 起就出现 30s 级尾延迟（2w/60m 11 次超时；4w/2h 20 次），1 worker 2h/14.3 万请求 0 超时、0 丢写（[E29](../docs/soak-ab.md)、[120 分钟单 worker](../docs/soak-high-120-1w.md)） |
| `sync` | 单 worker；并发用独立端口 | 4 个 `--workers 1` 进程 + 代理已验证 30 分钟 0 错误（[E31](../docs/soak-proxy-30m.md)）；不要用 `--workers N` |
| `async` | 短压测里多 worker 更快，但没有长跑对照 | 不要把 200 请求的吞吐当成耐久性证据 |

> 注意：`uvicorn --workers N` 在 Windows 上共享监听 socket，长跑会出现 30s 级 accept 卡顿
> （4 个独立端口 + 同一 SQLite 的 20 分钟对照为 0 错误，见 [docs/single-writer.md](../docs/single-writer.md)）。
> 需要多进程扩展时，用 N 个独立端口 + 反向代理，不要直接加 `--workers`。

长尾不随平均延迟出现：4 worker 的 P99 与 1 worker 接近（约 120–130ms），差异在 P99.9 之后。


### 多端口 + 反向代理（已做本地代理验证）

不要用 `uvicorn --workers N` 扩 API。用 `deploy/shotplan@.service` 起 N 个单 worker 实例：

```bash
sudo systemctl enable --now shotplan@8101 shotplan@8102 shotplan@8103 shotplan@8104
sudo cp deploy/nginx.conf /etc/nginx/conf.d/shotplan.conf
sudo nginx -t && sudo systemctl reload nginx
```

`deploy/nginx.conf` 是生产配方（upstream 8101–8104、`least_conn`、POST 不重试）；
本机没有 nginx，所以用 `scripts/edge_proxy.py` 做了同构的 asyncio 边缘代理验证：
4 个单 worker 后端 + 边缘代理，30 分钟 **34,113 请求、0 错误、最大 663ms、P99.9 332ms**
（[docs/soak-proxy-30m.md](../docs/soak-proxy-30m.md)）。nginx 配置本身仍需在 Linux 上
`nginx -t` 和一次真实请求验证，不能把本地代理结果冒充 nginx 已验证。

### 单写者 sidecar（实验性）

`deploy/shotplan-writer.service` 提供一个可选的单写者进程，API 通过
`SHOTPLAN_WRITER_URL=http://127.0.0.1:8001` 把运行历史交给它。

**不要把它当成 4 worker 的修复**：1 worker 下干净，但 4 worker 仍有 30s 级
`ReadTimeout`（30 分钟 11 次）。结论与对照见 [docs/single-writer.md](../docs/single-writer.md)。
生产 unit 默认 `batch + 1 worker`；代码/CLI 默认 `sync`。两者都保证“返回前已 commit”。

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
