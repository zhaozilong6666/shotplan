# ADR 0017 · 一个请求只有一个配置对象

- 状态：已采纳（2026-11-14）
- 相关：[ADR 0015 可复现运行轨迹](0015-reproducible-run-trace.md)、[架构评审](../architecture-review.md)

## 背景

`Settings` 曾经有三条解析路径、一个请求里两个实例：

| 入口 | 行为 |
| --- | --- |
| `get_settings()` | `load_dotenv()` + `Settings.from_env()` |
| `run_plan()` 未显式注入 | 直接 `Settings.from_env()`，**跳过 .env** |
| `build_provider()` 未显式注入 | 再 `Settings.from_env()`，**又一个对象** |

API 在 lifespan 固定 `app.state.settings` 传给 pipeline，却用无参 `build_provider()` 构造 provider。
于是 `trace.config` 记的是前者，实际发出的 HTTP 请求用的是后者；`dependency_overrides[app_settings]`
只改得到一半。评测 harness 也是 `provider=build_provider(), settings=settings` 的双对象模式。

这直接动摇了 [ADR 0011 受控实验](../adr/0011-controlled-experiment-over-inference.md) 的前提：
如果评测和线上用的不是同一套生效配置，区间再窄也不能迁移。

## 决策

1. **`get_settings()` 是唯一的解析入口**。`Settings.from_env()` 保留为纯解析器，仅供 `get_settings()`
   与少数配置单测直接调用；业务代码不得再直接调用它来「取默认配置」。
2. **`build_provider(settings)` 必须显式传参**，签名里没有默认值。
3. **`run_plan` 未显式注入时走 `get_settings()`**，与 CLI/API/eval 同一入口；未注入 provider 时
   用**同一个 cfg** 调 `build_provider(cfg)`。
4. **运行时不变量**：若 provider 带 `.settings` 且不是本次的 cfg，抛 `ConfigError`。
   `ConfigError` 不会被顶层错误边界吞掉——配置漂移是调用方 bug，不是请求级故障。
5. **每次运行的完整生效配置进快照**：`Settings.resolved()` 返回全部非敏感字段（密钥只留布尔），
   评测快照写 `effective_config`，断点元信息也含它，换配置续跑会被拒绝。

## 为什么用 opt-in 而不是改协议

`PlanProvider` 全仓有 10+ 个实现（离线 planner、故障注入 test double）。为传 deadline 或配置
去改协议，会把「修一个架构问题」变成「改十几个文件」，还让所有下游实现被迫关心它们不需要的东西。
因此：

- deadline 用 `accepts_deadline` 类属性声明，`pipeline.call_generate()` 只对声明者传参；
- 配置用**同一个对象传递**而非协议扩展。

保持协议窄、把不变量放在编排层，是这里更小的耦合面。

## 后果

**变好**

- 一条请求里 pipeline、provider、trace 说的是同一份配置；`trace.config` 可解释实际发出的请求。
- 离线 lab 与 CLI/API 不再因 `.env` 是否被加载而分叉。
- 评测快照自带完整生效配置，复现不再依赖「当时机器上环境变量恰好是什么」。
- `app.dependency_overrides[app_settings]` 恢复为真正可用的替换点。

**代价**

- `build_provider()` 无参调用是破坏性改动，已同步修改 4 个生产调用点与 2 个 monkeypatch 测试。
- `run_plan` 多一层顶层错误边界；`ConfigError` 明确排除在降级之外。
- 断点文件现在对配置敏感：改任何 `SHOTPLAN_*` 后续跑会被拒绝，需要重新开一个 checkpoint。
  这是有意的——拿不同配置的结果拼一份报告比不续跑更糟。

## 验证

- `tests/test_config_truth.py`：签名无默认值、双对象被拒、同对象通过、`get_settings` 是唯一入口、
  `resolved()` 不泄密。
- 全量测试 + 19/19 门禁。
- 真实链路复测：见 [架构评审末节](../architecture-review.md)。
