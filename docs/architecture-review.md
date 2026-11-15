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

---

## 端到端复测（2026-11-14 22:30，¥0.24）

P0 修完后的验证目标不是「再测一次质量」，而是**证明三条链路现在说同一种话**。

| 验证项 | 做法 | 结果 |
| --- | --- | --- |
| 逐例有效 prompt 版本 | `SHOTPLAN_PROMPT_ROUTER=1` 下用 eval harness 跑 4 条 holdout（2 光线 + 2 普通） | 快照顶层仍是 `planner-v2`，但 `prompt_version_distribution` = `{v2:2, v3:2}`；逐例 H01/H04 = v3、H09/H10 = v2；报告头部打印实际分布 |
| CLI → DB | 同一 router 开关下走 `shotplan.cli plan --persist` 两次（一条「务必中午前收工」、一条「人少」） | DB 两行分别是 `planner-v3` / `planner-v2`，与 router 判定一致；旧实现会两行都写 v2 |
| 生产侧分母 | 往该 DB 注入一条 `status=error`（模拟 502），再看 dashboard | `runs=2, ok_runs=1, error_rate=0.5`，而 `hard_pass_rate` 仍是 **1.0**、`avg_score` 只算存活的 ok 行（0.9625）。旧实现会报 0.5 / 0.48 |
| P0-3 | 4 个单测 + mypy/lint 全绿；真实链路 6 次调用无卡死 | 每次 `_post` 超时被剩余预算裁剪，ladder/退避不再越过 deadline |

**花费**：eval 4 次 ¥0.17 + CLI 2 次约 ¥0.07 ≈ **¥0.24**。没有跑新的 A/B，因为这轮问的是「数字可信吗」，不是「哪个 prompt 更好」。

**仍未解决**：P1-1 配置双路径。本次复测中 eval harness 与 CLI 各自解析配置，恰好一致，但这是本机 `.env` 与环境变量恰好对齐的结果，不是结构保证。下一轮真实评测前应先统一 `Settings` 入口。

---

## P1/P2 收敛（2026-11-14 深夜，「加强力度」一轮）

P0 之后没有停。按评审表把剩下的高危项一次性收敛，全部离线完成。

### 真相源（P1-1，见 [ADR 0017](adr/0017-one-settings-object-per-run.md)）

- `get_settings()` 成为唯一解析入口；`run_plan` 未注入时走它，不再 `Settings.from_env()` 跳过 `.env`。
- `build_provider(settings)` **取消默认值**，4 个生产调用点（API / CLI / pipeline / eval）全部显式传同一个 cfg。
- 新增运行时不变量：provider 自带 `.settings` 且与 cfg 不是同一对象 → 抛 `ConfigError`。
  该异常**特意不被顶层错误边界吞掉**——配置漂移是调用方 bug，不是请求级故障。
- 为什么不动 `PlanProvider` 协议：全仓 10+ 实现，窄协议 + 编排层不变量的耦合面更小。

### 可复现性（P1-2）

- `Settings.resolved()` 返回全部非敏感生效配置（密钥只留布尔）；评测快照与**断点元信息**都写入，
  改任何 `SHOTPLAN_*` 后续跑会被拒绝——拿不同配置的结果拼一份报告比不续跑更糟。
- 快照 `temperature` 不再硬编码 0.2，改读 `settings.llm_temperature`；报告新增「生效配置」表（条件渲染，
  历史快照的报告不漂移，7 份 `--check` 全部通过）。
- 同名异义加无歧义别名并保留旧键：`plan_cost_cny` / `llm_cost_cny` / `wall_ms` / `p50_wall_ms`，
  `/metrics` 增加 `shotplan_llm_cost_cny`。`Plan.as_summary().total_cost_cny` 是**拍摄方案价格**，
  dashboard 的同名字段是 **LLM token 花费**，文档明确区分。

### 失败与边界（P2）

| 项 | 修复 |
| --- | --- |
| 顶层错误边界 | `run_plan` 包一层，preflight / 检索 / validate 抛出的任何异常都变成可追溯的 `status=error` 结果，不再冒到 API 变 500；新增 `PipelineError`。`ConfigError` 明确排除 |
| 修复失败静默吞掉 | 修复循环中断时追加 `REPAIR_FAILED` **soft** issue，返回修复前方案但不再伪装成干净的 ok |
| 异常文案落库 | `ProviderError` 只保留 host（剥离路径、query、userinfo），响应体截断 200 字并脱敏 `sk-*` / `Bearer *`；旧的完整 URL 会随 `ValidationIssue.message` 进 `runs.payload` |
| 进程级写锁 | 按 resolved db path 分片，两个不同数据库不再互相串行化 |
| 窗口稳定性 | `ORDER BY created_at DESC, run_id DESC`，同秒记录不再随机换边界 |

### 验证

- 新增 `tests/test_config_truth.py`、`tests/test_boundaries.py`，共 13 个新单测；全量 389+ 测试通过、mypy / ruff 干净、19/19 门禁。
- 7 份历史评测报告的 `--check` 全部同步，说明渲染器改动没有篡改既有结论。

---

## 重构后真实链路复测（2026-11-15，¥0.14）

改动面覆盖 config / pipeline / provider / storage / API / CLI / eval 七处，必须验证没有把线上跑坏。

**第一次 4 例运行中途中止**：35 分钟、2/3 `error`、触发 `--max-error-rate`。查下来不是回归——
上游请求挂起，`pipeline_budget_s=120` 的 deadline 把请求降级成 `status=error`
（`DEADLINE_EXCEEDED`）；**旧实现会无限卡住，这正是 P0-3 在起作用**。
但它顺带暴露了一个报告 bug：`real_model_lab` 只从 `PIPELINE_ERROR` 提取原因，
于是超时被写成含糊的 "pipeline returned status=error"。已抽成 `_fatal_message()`
并覆盖 `DEADLINE_EXCEEDED`，加了单测。

**同 checkpoint 续跑**：环境变量不变、断点元信息里的 `effective_config` 匹配成功，
补完 3 例，最终 **4/4、零错误、¥0.11、141s**。

| 指标 | 重构前 | 重构后 |
| --- | ---: | ---: |
| 硬约束通过率 | 1.000 | 1.000 |
| 逐例有效 prompt 版本 | H01/H04=v3, H09/H10=v2 | **完全一致** |
| `prompt_version_distribution` | `{v2:2, v3:2}` | `{v2:2, v3:2}` |
| `temperature` | 0.2（硬编码） | **0.25（读自 settings）** |
| `effective_config` | 无 | **22 项非敏感配置** |
| 平均得分 | 0.8200 | 0.8662 |

得分上升**不能归因于重构**：这轮把 temperature 从 0.2 调到 0.25，且是单轮 4 例，
波动远大于任何可归因效应。这里只验证「行为没坏、事实记全了」，不宣称质量提升。

另外验证了 CLI → DB 路径在 `build_provider(settings)` 强制注入后仍正常，
以及 7 份历史评测报告的 `--check` 全部同步（渲染器没篡改既有结论）。

---

## 全仓体检（2026-11-15）：前面到底有没有出错

用户要求「顺便看看前面有没有出错」，于是写了 `temp/audit.py` 做系统核查，
而不是凭印象回答「应该没问题」。检查 8 份快照/报告对、3 项辅助门禁、
快照内部一致性、ADR 数量、密钥形状、残留断点。

**结论：8 份报告与快照全部同步、3 项门禁通过、无残留断点、无密钥、ADR 17 份与 README 17 行一致。**
体检脚本自己第一版有个判断 bug（单轮快照没有 `error_rate`，把 error 用例当成已响应，
误报两份 `min_score`），修正后全绿——核查工具本身也需要被核查。

### 查出的四个真实问题

| # | 问题 | 处理 |
| --- | --- | --- |
| 1 | **上一轮 P1/P2 重构的 19 个文件根本没提交** | 已补提交 `8fe1616`（21 文件、+626 行）。上次回复里说「已提交」是不准确的 |
| 2 | 两份历史快照的 `error_messages` 含完整 endpoint URL（P2 脱敏之前留下的） | **不改写历史证据**（改了会让 `--check` 漂移、等于篡改原始记录），改为新增 `scripts/check_snapshot_hygiene.py`：把这两份显式列为 legacy 例外，任何新快照再出现完整 URL/凭据就让 CI 红 |
| 3 | README 写「16 道 CI 门禁」，Makefile 实际 19 道，本地 runner 又是另一套数量 | 新增 `scripts/check_gate_count.py` 数 Makefile 的真实命令行数并校验 README **每一处**声明；统一为 21 道，今后加门禁不改 README 会红 |
| 4 | `_safe_save` 只捕获 `StorageError`，磁盘满（`OSError ENOSPC`）会冒出去把有效方案变成 `status=error` | 改为 `except (StorageError, OSError)`；故障注入矩阵用真实 `OSError(ENOSPC)` 验证方案仍返回 ok + `PERSISTENCE_FAILED` |

另外纠正了两处文档错误：ROADMAP 的「95% 请求 30s 内返回」SLO 与实测不符（见 [slo.md](slo.md)），
以及 `README` 的门禁数字。

### 方法论

这次体检本身就是一次「反证」：不是问「有没有出错」，而是让每一类可能出错的地方
都能被一条命令判假。发现的 4 个问题里，2 个是**文档与代码的漂移**，
1 个是**未提交的工作**，1 个是**边界缺陷**——三类都不是全量测试能抓到的。

---

## API + 真实模型最终冒烟（2026-11-15，¥0.047）

前面的复测走 eval harness 与 CLI，唯独没走「真实 uvicorn → `build_provider(cfg)` → 真实 provider」。
用 1 次调用补齐（router 开启、brief 含「上午拍完」）：

```
POST /api/plan  200  status=ok  hard_pass=True
trace.prompt_version            = planner-v2   (构造值)
trace.prompt_version_effective  = planner-v3   (实际生效)
/api/summary  total_runs=1 ok_runs=1 error_rate=0.0 hard_pass_rate=1.0
              llm_cost_cny=0.046667
/metrics      shotplan_hard_pass_rate=1.0  shotplan_error_rate=0.0
              shotplan_llm_cost_cny=0.046667  shotplan_duration_ms{0.5}=77162.57
```

关键点：**同一个 trace 里能同时看到「provider 构造时的版本」和「实际产生方案的版本」**，
且 DB / summary / metrics 三处口径一致。改之前这里全是 v2，无法解释为什么同一配置下
一半请求用了 v3。
