# 架构评审：测量层与运行时层已经分叉

> 2026-11-14，基于全仓只读扫描（24 个 src 模块、evals 主路径、配置与部署文件）。
> 本文不是功能清单，也不提「加测试」。它回答一个问题：**这套系统给出的数字，能不能代表它实际跑的东西？**

## 一句话结论

项目最强的能力是「测量」：95% 区间、配对 A/B、前瞻 holdout、失败归因、规则地图、19 项门禁。
但**测量层测的系统，和 API/CLI 实际运行的系统，已经不是同一套配置路径、不是同一套指标口径，也不记录同一组事实**。

因此所有「区间不跨 0」的结论，都只在评测自己的闭环里成立。继续在这个闭环里加实验，只会更精确地回答一个可能问错的问题。

---

## 先看健康的部分（避免误判）

- import 图**单向无环**：`api/cli → pipeline → agents/knowledge/storage/tracing/config → domain`。
- `domain/` 只依赖标准库与同层，没有反向依赖 FastAPI、SQLite、urllib、config。这是干净的六边形边界，不是问题所在。
- 密钥没有进入 `trace.config` 或评测快照。

所以问题**不在模块边界**，而在更上面一层：**谁拥有「真相」**。

---

## 系统性风险一：一条请求里有两个配置对象

| 入口 | 行为 |
| --- | --- |
| `get_settings()` | `load_dotenv()` + `Settings.from_env()` |
| `run_plan()` 未显式注入时 | 直接 `Settings.from_env()`，**跳过 .env** |
| `build_provider()` 未显式注入时 | 再 `Settings.from_env()`，**又一个对象** |

证据：`config.py:184,197-199`；`pipeline.py:275`；`agents/__init__.py:23`；`api.py:61-62,118-128,285,291-300`；`cli.py:85-88`。

API 在 lifespan 固定一份 `app.state.settings` 传给 pipeline，却用无参 `build_provider()` 构造 provider——timeout、attempts、temperature、router、max_tokens 全来自第二份对象。`trace.config` 记的是前者，实际发出的请求用的是后者。

**对测量的影响**：`real_model_lab.py:61` 正是 `provider=build_provider(), settings=settings` 这种模式。评测门禁、离线 lab、线上请求走的是不同的配置解析路径；本机 `.env` 与代码默认值差异极大（provider=mock→openai_compatible、timeout 30→120、attempts 3→5）。「评测通过」不等于「线上这条路径通过」。

`dependency_overrides[app_settings]` 这类测试替换只能改到 pipeline 的 cfg，改不到 provider——这是一个已经写进注释、但实际失效的可测试性承诺。

---

## 系统性风险二：我们在评测报告里修过的分母 bug，生产里还在

11-14 的三轮测量发现：4 个用例三轮全部 502、从未响应，把通过率从「已响应用例 100%」拉到 94.2%。当时的结论是「**分母里有什么，比极差多大更重要**」，并修了 `min_score`。

但 `observability._summarise()`（`observability.py:27-45`）对**所有行**计算 `hard_pass_rate` / `avg_score`，包含 `status=error` 与 `status=infeasible`；`/api/summary`（`storage.py:290-306`）用全表 SQL，完全不区分状态。刚上线的 `/metrics` 直接复用这套聚合。

也就是说：**同一个统计错误，评测侧已经修好，生产侧原样保留**。跨端点比较 `hard_pass_rate` 会得出错误结论。

配套还有两处口径冲突：
- `total_cost_cny` 在 `Plan.as_summary()` 是**拍摄方案成本**，在 storage / observability 是 **LLM token 成本**（`pipeline.py:478` vs `observability.py:43`）——同名异义，极易被当成账单。
- duration 至少三种定义（trace 事件求和 / 墙钟 / middleware 墙钟），`eval` 用前者、dashboard 用后者，P95 无法交叉验证。

---

## 系统性风险三：墙钟预算管不住最慢的那一步

`pipeline_budget_s` 的 `expired()` 只在三处检查：预检后、解析修复前、修复循环内（`pipeline.py:308,363,409`）。**`generate`（`:327`）和 `validate`（`:399`）没有 deadline。**

而 provider 内部：`_budget_ladder` 最多 3 档 × `_complete` 最多 `llm_attempts` 次 × 每次 `llm_timeout`（`openai_compatible.py:115-160`）。本机配置是 120s / 5 次 → 理论最坏 **3×5×120s = 30 分钟**，`DEADLINE_EXCEEDED` 几乎不会按设计触发。

**这正是今天三次「后台卡死数小时」的结构性根因**，不是中转站单方面的毛病。我们一直在用「断点续跑」绕过它，但那是在评测层加兜底；运行时的约束本身是失效的。

同一组边界还有：
- preflight / `kb.context_for` / validate 抛出的异常没有兜底，会冒到 API 变 500（`pipeline.py:302,318,399`），与 `_error_result` 的「不抛 500」承诺相悖。
- repair 失败只记一条 trace 就 break，`status` 仍是 `ok`（`:437-439`）；async 持久化写失败只累加 `writer.failed`，调用方拿到成功（`storage.py:471-493`）。

---

## 附：我们自己引入的一处测量污染

`SHOTPLAN_PROMPT_ROUTER` 开启时，provider 按请求把系统提示切成 v3（`openai_compatible.py:168-172`），但 trace、DB 的 `prompt_version` 列、dashboard 分组、评测快照全部记构造时的全局 v2（`pipeline.py:280`、`storage.py:175`、`real_model_lab.py:211`）。逐请求实际版本只存在于 `GenerationResult.metadata`，**全仓没有消费者**。

后果：`router-holdout-on.json` 顶层写着 `planner-v2` + `prompt_router: true`，从快照本身无法复现哪一半用了 v3。按 prompt 版本做质量对比、A/B、回归归因全部失真。虽然路由最终未通过验证、保持关闭，但这个「改动留下了不可复现的数据」的模式值得记录。

---

## 优先级：先收敛真相源，不要再加功能

| 级别 | 问题 | 为什么现在做 | 验收标准 |
| --- | --- | --- | --- |
| **P0-1** | 逐请求有效 prompt 版本没有落库 | 任何版本对比都无法复现 | 每次运行的 trace / DB / 快照能回答「这一例实际用了哪版 prompt」 |
| **P0-2** | error / infeasible 混入质量分母 | 评测已修、生产未修，数字互相矛盾 | `hard_pass_rate` 只对 `status=ok` 计算，另给 `error_rate` / `infeasible_rate`，三处端点口径一致 |
| **P0-3** | deadline 不约束 generate | 今天卡死的根因 | 剩余预算传入 generate，每次 `_post` 用 `min(timeout, remaining)` 裁剪，ladder 共享同一 attempt 计数 |
| **P1-1** | 两个 Settings 对象 / 配置双路径 | 评测有效性 | `build_provider` 强制接收已解析的 `Settings`；`run_plan` 不再隐式 `from_env()`；快照记录生效配置 |
| **P1-2** | 指标命名与定义冲突 | 同名异义会误导 | `wall_ms` / `stage_sum_ms` / `llm_cost_cny` / `plan_cost_cny` 改名；`summary()` 复用 dashboard 口径 |
| **P2** | 进程级锁跨库串行化、窗口 tiebreak、异常 message 落库可能含 URL、接口归属 | 增长后才会痛 | 分片锁、稳定排序、脱敏、`ports` 模块 |

---

## 决策

**在 P0 修完之前，不花钱跑新的真实模型实验。**

理由不是省钱，是有效性：现在每一份新快照都无法逐例复现实际 prompt，评测与生产走不同配置路径，生产指标还把 provider 故障算成质量差。在这个状态下新增数据，只是把「问错的问题」答得更精确。

余额充足（约 ¥4.6）应留到 P0 完成后的**一次端到端复测**：同一批用例分别经 CLI/API 与 eval 两条路径跑，验证配置、指标、prompt 版本三者一致，再谈新的 A/B。

这也修正了路线图阶段的排序：阶段 9 的「故障注入 / SLO / 部署」都建立在「运行时数字可信」之上，应该让位于 P0 的真相源收敛。

---

## 这次评审的方法论

和评测同一套标准：**先说健康的部分，再指出分叉；每个结论给 file:line；标注哪些是推断而非事实。**

扫描中三处标为「需人工确认」：`exception_handler(StorageError)` 的可达性、SQLite 文件被外部删除后是否重建、以及 `llm_base_url` 历史上是否曾带凭据。它们不影响上述三个系统性结论。

---

## P0 修复记录（2026-11-14 当晚）

评审不是写完就放着。三项 P0 已全部落地，19/19 门禁通过，零 API 花费。

| 项 | 修复 | 验收 |
| --- | --- | --- |
| **P0-1 有效 prompt 版本** | `GenerationResult.prompt_version` 成为一等字段；`RunTrace.prompt_version_effective` 记录**被采纳的草稿**所用版本；DB 的 `prompt_version` 列改存有效值；评测快照新增 `prompt_version_distribution` 与逐例字段 | 单测证明：provider 构造值是 v2、逐请求切成 v3 时，trace / payload / DB 三处都记为 v3；快照能给出 `{v2: 1, v3: 2}` 这样的分布 |
| **P0-2 分母** | `_summarise()`、`RunStore.summary()` 的质量指标只统计 `status='ok'`；新增 `ok_runs` / `error_runs` / `infeasible_runs` / 两个比率与 `group.errors`；`/metrics` 暴露 `shotplan_error_rate` / `shotplan_infeasible_rate` | 单测钉死：2 个 ok（1 通过）+ 1 个 502 + 1 个预检拒绝 → 通过率 0.5 而非 0.25，P95 不含 99999ms 的断供，成本仍含失败调用 |
| **P0-3 墙钟预算** | 用 `accepts_deadline` 显式声明的可选参数把绝对 deadline 传给真实 HTTP provider，避免改动所有离线 planner / test double 的签名；每次 `_post` 用 `min(llm_timeout, remaining)` 裁剪，budget ladder、重试、退避都检查剩余预算；生成后仍超时则直接 error | 4 个单测：deadline 只传给声明支持的 provider；慢生成返回 error 而非方案；HTTP 超时被剩余预算裁剪；已过期 deadline 不发请求 |

### 刻意没做的改动

- **没有改 `PlanProvider` 协议**：全仓有 10+ 个离线 provider / test double，强行加参数会把「修一个架构问题」变成「改十几个文件」。用 opt-in 属性把兼容性成本压到零，同时在 `pipeline.py` 里写清原因。
- **没有加数据库列**：`prompt_version` 列本来就存「产生这次方案的 prompt」，把值改成有效版本是纠正语义，不需要 schema 迁移；旧行仍是构造值，含义不变。
- **没有顺手修 P1/P2**：配置双路径、指标命名、进程级锁都需要更大范围的重构，应当各自带一次完整回归，而不是塞进同一个提交。

### 下一笔钱怎么花

P0 完成前不跑新 A/B 的约束已解除。建议用余额做**一次端到端一致性复测**：同一批用例分别经 CLI 与 eval 两条路径，核对配置、指标、有效 prompt 版本三者一致。这比再加一组 A/B 更能回答「我们的数字可信吗」。

**P1-1（两个 Settings 对象 / 配置双路径）仍是最高优先级的剩余风险**：它决定评测结论能不能迁移到生产，建议在下一轮真实评测之前处理。
