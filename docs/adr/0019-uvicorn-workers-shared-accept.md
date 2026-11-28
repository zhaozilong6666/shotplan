# ADR 0019 · 多实例扩 API 用独立端口，不用 uvicorn --workers

- 状态：已采纳
- 日期：2026-11-28
- 相关：[0011 对照实验](0011-controlled-experiment-over-inference.md)、[0012 写后置队列](0012-write-behind-persistence.md)、[E29](../soak-ab.md)、[E30](../soak-independent-20m.md)、[E31](../soak-proxy-30m.md)

## 背景

4 worker 的真实 HTTP 长跑出现 20 次 `ReadTimeout`，最大 30s，而 1 worker 两小时 0 次。
第一次归因是「多进程抢 SQLite 写锁」，但对照实验依次排除了：

- **SQLite 写路径**：`off`（完全不落库）在 4 worker 下 20 分钟仍有 4 次 30s 超时；
- **BaseHTTPMiddleware**：换成纯 ASGI 中间件后仍有 1 次 30s 超时；
- **单写者 sidecar**：把写入收敛到一个进程后，4 worker 仍有 11 次超时/30 分钟；
- **请求分段**：30.3s 的慢请求里，所有应用段合计只有 26–49ms，其余时间在端点进入前。

最后用 **4 个独立端口、每个单 worker、共用同一个 SQLite** 跑 20 分钟：25,001 请求
0 错误、P99.9 260ms、最大 514ms。范围因此收敛到 `uvicorn --workers N` 在 Windows 上
共享监听 socket 的 accept 调度，而不是应用代码或数据库。

## 决策

1. **API 默认 1 worker**。systemd unit 使用 `batch + 1 worker`；代码/CLI 默认仍是
   `sync`。两种默认都在响应返回前 commit，区别只是 batch 合并并发请求的提交。
2. 需要并发时，用 **N 个单 worker 进程 + 边缘代理**（`deploy/shotplan@.service`、
   `deploy/nginx.conf` 或 `scripts/edge_proxy.py`），不使用 `uvicorn --workers N`。
3. **sidecar 不设为默认**。它确实把写者收敛为一个进程，1 worker 下干净；但它没有消除
   4 worker 的尾延迟，还多一个需要部署和监控的进程。代码保留为可选实验，不再扩写。
4. **冻结持久化模式**：`off/sync/async/batch` 之外不再新增模式。代码/CLI 默认
   `sync`，systemd unit 显式用 `batch`；两者都对外承诺「每个被接受的请求都已落库」。
   不允许把 `batch` 与 `--workers N` 组合。

## 后果

- 部署文档与 systemd unit 以「单 worker」为默认；多端口配方是扩展路径，不是默认路径。
- CI 不重跑 30 分钟长跑；`soak` 证据是本地快照，不是每次提交的保证。
- Windows 的 `--workers N` 长尾无法在 Linux CI 上复现，结论只对 Windows + 当前 uvicorn 版本成立。
- 如果 uvicorn 修复了共享 accept 的调度，或 Linux 上实测无此问题，应该用一次 1/4 worker
  对照重新打开这个决策，而不是凭印象改回去。

## 什么情况下推翻

- 同一台 Windows 机器、同一 uvicorn 版本下，`--workers 4` 连续 30 分钟 0 错误且最大延迟 <1s；
- 或独立端口 + 代理的运维成本超过收益（例如代理自身出现 30s 长尾，或端口数成为部署瓶颈）。
