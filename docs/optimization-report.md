# shotplan 优化报告 · 交接给下一个 AI

> 日期：2026-11-13
> 范围：架构与长项目可维护性审查后的三批修复
> 状态：**改动已完成并验证，尚未提交到 git**
> 工作区：67 个已跟踪文件变更（60 修改 + 7 删除），另新增 3 份 ADR、`docs/portfolio/`、`src/shotplan/web/`、`src/shotplan/py.typed`、`requirements.lock`、`tests/test_docs_links.py`、`.github/workflows/kb-freshness.yml` 与本报告

---

## 0. 这份报告怎么用

给接手的 AI 的执行顺序：

1. 先读第 2 节「不要破坏的设计不变量」。
2. 再读第 3 节「三批改动清单」，了解哪些行为已经变了。
3. 用第 5 节的命令复跑验证，确认基线。
4. 按第 6 节「剩余工作」的优先级继续，不要回头重做已完成项。
5. 第 7 节是改规则/改数据时的正确重生成顺序，顺序错了 `--check` 门禁会失败。

---

## 1. 接手前的基线

| 项目 | 接手时状态 |
| --- | --- |
| 单元测试 | 264 个收集，全部通过 |
| 评测 | 80/80，good 0.9788 / naive 0.3014，差值 0.6774 |
| 变异门禁 | 8/8 拦截，旧门禁漏 4/8 |
| CI | GitHub Actions 四个 Python 版本全绿 |
| 主要缺口 | `must_include`/`avoid` 只当检索提示；`source_refs` 只计数不校验；trace 无知识库/规则版本与配置快照；所有 `ShotplanError` 统一 422；写库失败会否决方案；统计解析全量 payload；wheel 安装后 Web 与默认 DB 路径不可用；无部署保护；CI 重门禁跑 4 份矩阵；评测报告含时间戳导致每次生成都脏工作区 |

实测证据：`must_include=["qinglong_temple"]` 时方案里没有青龙寺，仍然 `hard_pass=True`、得分 0.975，评测用例 C18 照样计入 80/80；伪造 `source_refs` 仍能拿溯源分。

---

## 2. 不要破坏的设计不变量

1. **核心域零第三方依赖**：`domain/`、`knowledge/`、`agents/`、`pipeline.py`、`tracing.py`、`storage.py`、`cli.py` 只用标准库。
2. **两个判断不交给模型**：生成前可行性判定、生成后硬约束校验，都在模型之外。
3. **规则单一定义源**：`src/shotplan/domain/constraints.py` 是规则、权重、阈值的唯一来源；校验器、打分器、文档、`/api/rules` 共用。
4. **修复单调**：只有 `(hard_pass, score)` 严格变大才接受新草稿。
5. **评测报告可提交**：`docs/eval-report.md` 不含时间戳、机器相关耗时；机器相关数字只进 `evals/out/latest.json`。
6. **生成物不手改**：`docs/pipeline-contract.md`、`docs/failure-gallery.md`、`docs/case-mining.md`、`docs/examples.md`、`docs/index.html` 都由代码生成；改代码后按第 7 节重生成。
7. **历史快照不重写**：`CHANGELOG.md` 旧版本条目、`docs/adr/0009`、`docs/adr/0010` 里的旧数字是当时事实，不要改成当前值。
8. **结论措辞不夸大**：good provider 是规则实现，100% 是自洽性检验，不代表真实模型质量；真实模型对比仍是阶段 4 的未完成项。

---

## 3. 三批改动清单

### 3.1 请求约束闭环（P0）

**问题**：`must_include` / `avoid` 只影响检索，不进入校验；用户明确提出的条件可以被忽略，评测也测不出来。

**改动**：
- `src/shotplan/domain/constraints.py`
  - 新增硬规则 `MISSING_REQUIRED_LOCATION`：`must_include` 的每个 id 必须出现在至少一个 `shoot` 时间块。
  - 新增硬规则 `AVOIDED_LOCATION`：`avoid` 的 id 不得出现在任何时间块。
  - 新增 `rules_fingerprint()`：对规则表、评分权重与阈值做 SHA-256，前 12 位作为 `rules_version`。
- `src/shotplan/agents/validator.py`
  - 在 `UNKNOWN_LOCATION` 之后执行上述两条硬规则；`must_include` 用 `shoot` 块判断，`avoid` 检查所有槽位。
- `src/shotplan/domain/feasibility.py`
  - 新增 `UNKNOWN_REQUIRED_LOCATION`：必去地点不在当前城市知识库。
  - 新增 `ALL_LOCATIONS_EXCLUDED`：整座城市的地点都被 `avoid` 排除。
- `src/shotplan/knowledge/tools.py`
  - `context_for()` 的室内兜底也应用 `avoid` 过滤；此前兜底会忽略排除列表。
- `src/shotplan/agents/mock.py`
  - `good` provider 从全城市地点中优先挑选必去地点，再补足其余拍摄点。
  - 预算裁剪只裁非必去地点；裁不下就让 `BUDGET_EXCEEDED` 如实报错，不悄悄丢掉用户要求。
  - 室内棚被排除时，妆造改到第一个允许的外景集合点，并在方案假设里说明。
- `evals/cases/conflict.jsonl`
  - 修正 C09：原用例声称「排除全部外景、回退室内」，却把唯一的室内棚也排除了；现在只排除全部外景。

**后果**：规则 12 → 14（当时）；good 平均分从 0.9788 降到 0.9778（因为必去地点现在真的被排进行程，不是回归）；失败案例库 517 → 527 条，新增两类错误。

### 3.2 引用必须真实存在（P0，第二批）

**问题**：`source_refs` 只检查数量，不检查存在性；模型可以引用 `locations.json#invented_palace` 仍拿溯源分。

**改动**：
- `src/shotplan/domain/constraints.py`
  - 新增硬规则 `UNKNOWN_SOURCE_REF`：`plan.source_refs` 与每个槽位的 `source_refs` 必须解析到当前城市的地点或全局风格。
- `src/shotplan/agents/validator.py`
  - 新增 `_source_ref_exists()`：只接受 `locations.json#<id>` 与 `styles.json#<id>` 两种前缀；跨城市地点、空 id、未知前缀都算未知引用。
  - 未知引用直接硬失败，不只扣软分。
- 新增 [ADR 0016](adr/0016-source-refs-must-resolve.md)。

**后果**：规则 14 → 15（10 硬 + 5 软）；离线 good 的引用本来就来自知识库，分数不变；naive 没有引用，仍由 `NO_SOURCE_REF` 记录，失败案例库仍为 527 条。

### 3.3 运行记录可复现（P0）

**问题**：trace 只有 `provider` / `prompt_version`，改一次知识库或规则后，历史分数无法解释。

**改动**：
- `src/shotplan/domain/models.py`
  - `RunTrace` 新增 `kb_version`、`rules_version`、`wall_duration_ms`、`config`（请求级配置快照），并写入 `to_dict()`。
- `src/shotplan/knowledge/loader.py`
  - 新增 `knowledge_version()`：`locations.json + styles.json` 内容 SHA-256 前 12 位。
- `src/shotplan/tracing.py`
  - `TraceCollector` 创建时写入知识库与规则指纹；新增 `set_config()`。
  - `finish()` 记录端到端墙钟；`total_duration_ms` 仍是各阶段计时之和，两者语义分开。
- `src/shotplan/pipeline.py`
  - 每次运行记录 `max_repairs`、`pipeline_budget_s`、`llm_model`（mock 时不记录）、`persist_mode`。
- `src/shotplan/storage.py`
  - 新增 `_SCHEMA_VERSION = 2` 与 `PRAGMA user_version`。
  - v1 数据库自动 `ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'`；更高版本直接拒绝启动。
  - 运行历史的 `duration_ms` 列改为墙钟（优先 `wall_duration_ms`，旧记录回退 `total_duration_ms`）。
- `src/shotplan/observability.py`
  - 仪表盘耗时优先读 `wall_duration_ms`，兼容旧 payload。

### 3.4 统计查询与数据保留（P1，第二批）

**问题**：`/api/stats` 把最多 1000 条完整 JSON payload 读进内存再聚合，历史越大越慢；没有清理机制。

**改动**：
- `src/shotplan/storage.py`
  - 新增 `stats_rows()`：只 SELECT provider / prompt / status / score / hard_pass / repair_count / duration_ms / cost_cny，不解析 payload。
  - 新增 `prune(keep_last)`：只保留最近 N 条运行记录。
  - schema v2 增加 `status` 列，便于按状态聚合。
- `src/shotplan/observability.py`
  - `build_dashboard()` 改用 `stats_rows()`；删除 `_flatten()`。
- `src/shotplan/cli.py`
  - `shotplan history --prune N` 可显式清理旧记录。

### 3.5 错误语义与持久化隔离（P0）

**问题**：`StorageError`、`ProviderError` 都继承 `ShotplanError`，API 统一返回 422；写库失败还会让一份已经算好的方案变成错误响应。

**改动**：
- `src/shotplan/api.py`
  - 新增 `_HTTP_STATUS` 映射：`DeadlineExceeded → 504`、`ProviderError → 502`、`StorageError → 503`、`ConfigError/KnowledgeError → 500`、`ParseError → 422`。
  - 注册 `StorageError` 异常处理器，`/api/runs` 等非 plan 接口也返回 503。
- `src/shotplan/pipeline.py`
  - 新增 `_safe_save()`：写库失败时记录日志，并在校验报告追加软问题 `PERSISTENCE_FAILED`，方案照常返回。
  - 预检不再重复计算 feasibility；`_infeasible_result` 直接接收已算出的报告。

### 3.6 部署保护（P1，第二批）

**问题**：公开部署无鉴权、无限流、无请求体限制；`mock_mode` 允许客户端绕过已配置的真实 provider。

**改动**：
- `src/shotplan/config.py`
  - 新增 `api_key`（`SHOTPLAN_API_KEY`）、`rate_limit_per_minute`、`max_body_bytes`，默认全部关闭。
  - `masked()` 暴露 `deploy_api_key_set`、限流与体积上限，不泄露 key。
- `src/shotplan/api.py`
  - `require_api_key` 依赖：设置 key 后，`/api/plan`、`/api/runs`、`/api/runs/{id}` 需要 `X-API-Key`；统计/配置接口保持公开，避免 Web 面板在受保护部署下不可用。
  - 进程内限流：`SHOTPLAN_RATE_LIMIT_PER_MINUTE > 0` 时只限制 `/api/plan`；多 worker 不共享计数，属于尽力而为。
  - `create_plan` 先读原始 body，超过 `SHOTPLAN_MAX_BODY_BYTES` 返回 413；JSON 解析失败返回 422。
  - 只有 provider 配置为 `mock` 时才接受 `mock_mode`，真实 provider 下客户端不能切回离线 planner。
- `.env.example` 增加三个变量示例。

### 3.7 打包与运行（P1）

**问题**：`pip install` 成 wheel 后 `web/` 不在包里、默认 DB 指向解释器目录、`shotplan eval` 直接 import 未打包的 `evals`。

**改动**：
- `web/app.js`、`web/index.html`、`web/styles.css` → `src/shotplan/web/`。
- `src/shotplan/api.py`：`WEB_DIR = Path(__file__).parent / "web"`。
- `pyproject.toml`：`"shotplan" = ["web/*", "py.typed"]`。
- `src/shotplan/config.py`
  - 新增 `_default_db_path()`：源码树用 `ROOT/data`，wheel 安装回退到 `Path.cwd()/data`。
  - `Settings.db_path` 改用 `field(default_factory=...)`。
  - 新增 `configure_logging()`，让 `SHOTPLAN_LOG_LEVEL` 真正生效。
- `src/shotplan/cli.py`
  - `main()` 启动时配置日志。
  - `cmd_eval` 在 `evals` 不可导入时给出明确提示，不再抛裸 `ImportError`。
- `Dockerfile`：只复制 `src`；基础镜像改为 `python:3.12-slim-bookworm`；新增非 root 用户 `appuser`。
- `docker-compose.yml`：运行历史改用命名卷 `shotplan-data`，避免 bind mount 的权限问题。
- `src/shotplan/storage.py`：写后置队列批次失败时写 warning 日志。

### 3.8 知识库治理与类型标记（P1/P2，第二批）

- `src/shotplan/knowledge/data/locations.json` / `styles.json`
  - 顶层增加 `version: "1.1"`、`updated_at: "2026-11-13"`。
- `tests/test_knowledge.py`
  - 断言顶层元数据存在。
  - 断言所有风格的 `suitable_tags` 都能在地点标签集合中找到（防止风格引用了不存在的标签）。
  - 断言每座城市至少有一个室内兜底地点。
  - 断言所有评测用例与挑战集的 `must_include` / `avoid` 都指向该城市知识库中真实存在的 id。
- `src/shotplan/py.typed` + `pyproject` package-data：包声明为 typed，消费者不会把类型当 `Any`。

### 3.9 CI 与文档（P1）

- `.github/workflows/ci.yml` 拆成两个 job：
  - `test`：lint + 单元测试 + 覆盖率，保留 Python 3.10–3.13 矩阵。
  - `gates`：15 道评测/变异/压测/对照实验门禁，只在 3.12 执行一次。
  - 新增 `permissions: contents: read`、`concurrency`、`timeout-minutes`。
- `.github/workflows/eval.yml`：新增 `timeout-minutes` 与只读权限。
- `evals/run_eval.py`
  - 移除报告里的时间戳与机器相关 P50/P95，改为「亚毫秒级，原始数字见 `evals/out/latest.json`」。
  - 连续两次 `--write-report` 生成的文件哈希一致。
- `scripts/build_showcase.py`
  - 失败标签补充 `MISSING_REQUIRED_LOCATION`、`AVOIDED_LOCATION`、`UNKNOWN_SOURCE_REF`。
- 新增 ADR：
  - `docs/adr/0014-request-constraints-are-hard-rules.md`
  - `docs/adr/0015-reproducible-run-trace.md`
  - `docs/adr/0016-source-refs-must-resolve.md`
- `CHANGELOG.md`：新增 Unreleased Changed/Fixed，删除 0.10.1 的重复条目。
- 同步更新：`README.md`、`README.en.md`、`docs/architecture.md`、`docs/evaluation.md`、`docs/problem-and-metrics.md`、`docs/observability.md`、`docs/ROADMAP.md`、`docs/runbook.md`、`docs/portfolio/interview-notes.md`、`docs/portfolio/interview-onepager.md`、`docs/portfolio/resume.md`、`docs/portfolio/USER_TASKS.md`、`CONTRIBUTING.md`（line-length 100 → 120，ruff 范围加 `scripts`）。

### 3.10 测试

新增 24 个测试，总数 264 → 288：

- `test_validator.py`：必去地点缺失/满足、排除地点出现/未出现、伪造引用/真实引用，共 6 个。
- `test_feasibility.py`：未知必去地点、全部地点被排除，共 2 个。
- `test_knowledge.py`：元数据与交叉引用、用例地点引用完整性，共 2 个。
- `test_storage.py`：schema v2、v1 原地迁移、stats_rows 不含 payload、prune，共 4 个（另更新 schema 断言）。
- `test_api.py`：provider → 502、写库失败仍返回 200 + `PERSISTENCE_FAILED`、历史接口 storage → 503、API key 401、请求体 413、限流 429、真实 provider 下忽略 `mock_mode`，共 7 个。
- `test_pipeline.py`：trace 版本指纹、墙钟、配置快照、写库失败不否决方案，共 2 个新增。
- `test_cli.py`：`serve` 相关测试在缺少 uvicorn 时 `importorskip`。

### 3.11 第三批：工程化收尾（P1/P2）

**依赖锁**：新增 `requirements.lock`，由干净 CPython 3.12 虚拟环境安装 `.[api,dev]` 后 `pip freeze` 生成，记录 FastAPI、pytest、mypy 等 31 个精确版本；文件头写明用途（复现本地环境）与边界（CI 仍按 `pyproject` 区间解析，保持 3.10–3.13 矩阵真实）。`pytest-cov` 补进 `dev` extras，`make install` 后 `make test` 不再缺依赖。

**类型检查**：`pyproject.toml` 增加 `[tool.mypy]`（只检查 `src/shotplan`，开启 `check_untyped_defs` / `no_implicit_optional`，可选依赖缺 stubs 时忽略）；修掉 3 处真实类型错误：

- `validator.py` 的循环变量 `required` 与后面的集合重名；
- `cli.py` 的 `provider` 被推断成 `MockProvider`，显式标注 `PlanProvider`；
- `evals/run_eval.py` 的 `payload` 显式标注 `dict[str, Any]`。

`python -m mypy` 在 23 个模块上零错误；CI `gates` job 增加 `Type check` 步骤，本地可用 `make typecheck`。

**请求 ID 与访问日志**：`api.py` 新增最外层 `request_context` 中间件：

- 接受客户端/代理传入的 `X-Request-ID`，只允许 `[A-Za-z0-9._-]{1,64}`，否则重新生成 16 位十六进制串（防日志伪造 / 响应头注入）；
- 响应头始终回传，429 与 503 的 JSON body 也带 `request_id`；
- 每个请求写一行 `request_id=... method=... path=... status=... duration_ms=...` 到 `shotplan.api` logger，未捕获异常也会记录；
- 测试覆盖：回传、非法值替换、日志行字段、错误响应 id。

**CORS 决策**：不加 CORS 中间件。自带界面与 API 同源，通配符会让任意页面调用未设 key 的本地实例；接独立前端时加显式白名单。`SECURITY.md`、`docs/observability.md`、README 与新增测试（带 `Origin` 的请求不返回 `access-control-allow-origin`）一起固定这个决策，避免以后有人“顺手”加 `allow_origins=["*"]`。

**Settings 启动快照**：`lifespan` 里解析一次并写入 `app.state.settings`，所有路由通过 `Annotated[Settings, Depends(app_settings)]` 注入；`_cached_settings()` 在没有 lifespan（直接调用 / 单测）时回退到环境变量，测试可用 `app.dependency_overrides` 替换。副作用：**启动后再改环境变量不会改变行为**，因此 4 个原本在 client 启动后 `monkeypatch.setenv` 的测试改为 `client_for` fixture，在启动前注入环境。

**知识库 per-entry 来源**：每个地点/风格条目新增 `source`、`last_verified_at`（数据版本 1.1 → 1.2），文件顶层加 `provenance_note` 说明语义（仓库内整理来源与复核日期，不是实时门票 / 价格确认）。加载器强制校验 `source` 非空、日期可被 `date.fromisoformat` 解析。新增测试：条目完整性、未来日期、超过一年未复核即失败、空来源与坏日期被加载器拒绝。**刻意不做**生成式「数据新鲜度报告」：那会再造一条非确定性产物链，测试 + 顶层字段已能强制复核。

**机器相关数字的读法**：`docs/evaluation.md` 增加一段统一说明，把六份单机快照（benchmark / loadtest / http-load / scaling / persistence / worker-bottleneck）的用途限定为「同批次相对关系」，避免跨机器比较绝对值。没有改六个生成器的头部：那会强制重跑全部压测并同步 `--check` 产物，收益小于风险；说明集中放在评测文档里。

**文档同步**：测试数 288 → 294（新增 6 个），覆盖率 90.71% → 90.72%；README / README.en / ROADMAP / USER_TASKS / interview 材料 / resume / CHANGELOG 同步；`docs/observability.md` 增加日志与 CORS 一节。

### 3.12 第三批补遗：追踪闭环与保留策略

**request_id 贯通到运行记录**：
- `run_plan(..., request_id=None)` 新增可选参数；传了就写进 `trace.config.request_id`，CLI / 评测不传，因此离线产物保持确定性。
- `/api/plan` 把 `request.state.request_id` 传进去；现在从一条历史运行（`/api/runs/<id>`）可以直接反查访问日志行。
- 测试：API 创建的运行在响应与落库 payload 里都带同一个 id；pipeline 层断言「不传就不出现、传了就记录」。

**自动保留策略**：
- `Settings` 新增 `retention_days`（`SHOTPLAN_RETENTION_DAYS`，默认 0 = 永久保留），并在 `/api/config` 暴露。
- `RunStore.prune_older_than(days)` 按 `created_at` 删除过期记录；`created_at` 是固定精度 UTC ISO 串，字符串比较即时间比较。
- API 在 lifespan 启动时执行清理；清理失败只记 warning，不阻止服务启动。
- CLI 新增 `history --prune-days N`。
- 测试：只删过期行、0 不删；启动时清理真的生效，且 `/api/config` 显示保留天数。

**文档同步**：测试数 294 → 299，覆盖率 90.72% → 90.62%（新增分支的错误路径未全部覆盖，仍高于 85% 门槛）；README / README.en / observability / runbook / `.env.example` / CHANGELOG 同步。

### 3.13 第三批补遗：文档分层、链接校验与复核提醒

**个人材料下沉到 `docs/portfolio/`**：`USER_TASKS.md`、`resume.md`、`interview-notes.md`、`interview-onepager.md` 从 `docs/` 移到 `docs/portfolio/`，项目文档与个人求职材料分开。同步修正了全部外部引用（README / README.en / ROADMAP / runbook / CHANGELOG）与文件内部的相对链接（`../runbook.md`、`../evaluation.md`、`../persistence.md`）。

**文档链接测试**：新增 `tests/test_docs_links.py`，扫描根目录与 `docs/**` 的所有 Markdown 相对链接（跳过外链、锚点与代码块），链接目标不存在即失败。第一次运行就抓到一处历史断链：ADR 0013 指向不存在的 `0007-preflight-feasibility.md`（实际文件是 `0007-feasibility-before-generation.md`），已修正。测试数 299 → 301（另含限流桶上限测试）。

**限流桶内存上限**：`_RATE_BUCKETS` 此前为每个客户端保留一个列表且永不清理；连接数很多时是一处无界增长。新增 `_purge_idle_rate_buckets()`，超过 4096 个桶时清掉 60 秒内没有记录的桶，并补测试。

**知识库复核提醒**：新增 `.github/workflows/kb-freshness.yml`，每月 1 日与手动触发时安装 `.[dev]` 并运行 `tests/test_knowledge.py`。超过一年未复核时测试失败，把「数据过期」从事后事故变成定时提醒。

**Docker 基础镜像固定 digest**：`Dockerfile` 改为 `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`。digest 先由 Docker Hub 标签 API 取得，再用 `mirror.gcr.io` 的 `HEAD /v2/library/python/manifests/3.12-slim-bookworm` 独立校验（返回同一个 `Docker-Content-Digest`），确认它是多架构索引而不是单平台 manifest。本机没有 Docker，因此没有做实际 `docker build`；注释里写了后续用 `docker buildx imagetools inspect` 升级的方法。

**打包元数据现代化**：`pyproject.toml` 的 `license` 从 TOML 表改为 SPDX 字符串 `"MIT"`，新增 `license-files = ["LICENSE"]`，构建要求 `setuptools>=77`，删除已废弃的 `License :: OSI Approved :: MIT License` classifier。`pip wheel` 构建不再打印 setuptools 弃用告警；wheel 内 `shotplan/web/*`、`shotplan/py.typed`、知识库 JSON 与 `licenses/LICENSE` 齐全，元数据为 `License-Expression: MIT`。

**阈值与容量策略文档化**：
- `docs/evaluation.md` 写明用例级 `expect_min_score` 的定法（表达场景可接受的最低质量，按难度分档，与 `description` 一起审阅，不许为变绿下调）。
- `docs/observability.md` 说明 SQLite `DELETE` 不归还磁盘、需要停服 `VACUUM`，并解释为什么不做「按文件体积自动清理」——按时间/条数删除可控，按体积删除依赖一次昂贵的重写。
- `SECURITY.md` 明确进程内限流在多 worker 下各算一份，公开部署应在网关做真正的限流。

---

## 4. 重新生成的产物

| 产物 | 变化 |
| --- | --- |
| `evals/failures/cases.jsonl` | 527 条，6 类；新增 `MISSING_REQUIRED_LOCATION` 6 条、`AVOIDED_LOCATION` 4 条；`UNKNOWN_SOURCE_REF` 未新增记录（naive 无引用） |
| `docs/failure-gallery.md` | 同步为 527 条 / 6 类 |
| `evals/challenge/cases.jsonl` + `docs/case-mining.md` | 仍为 200 条，覆盖 19/25 → 25/25，最小差值 0.7625 |
| `evals/case_mining.json` | 同步 |
| `docs/eval-report.md` | good 0.9778 / naive 0.3014 / 差值 0.6764，80/80 |
| `docs/mutation-report.md` + `evals/mutation.json` | 8/8 拦截，旧门禁仍漏 4/8 |
| `docs/repair-experiment.md` | 69/69 一次修复，单调保护仍生效 |
| `docs/examples.md` | 重新生成并通过 `--check` |
| `docs/pipeline-contract.md` | 节点说明 15 条规则（10 硬 + 5 软），通过 `--check` |
| `docs/cost-latency-simulation.md` | 重新生成 |
| `docs/index.html` | 重新生成；静态文字同步 15 条规则与 0.9778 |

第三批及其补遗只改了 API 中间件、配置读取、保留策略、知识库元数据与文档，不改变评分：重跑 `run_eval` 仍为 80/80、0.9778 / 0.3014，`docs/index.html` 连续两次生成哈希一致（`F3FB94F6…`），`examples` / `pipeline-doc` 的 `--check` 仍通过。`requirements.lock` 是新增文件，不属于生成物。

---

## 5. 验证结果与命令

### 已实测

| 检查 | 结果 |
| --- | --- |
| `pytest` | 301 个测试全部通过 |
| 覆盖率 | **90.67%** |
| 评测 | 80/80，good **0.9778** / naive **0.3014**，差值 **0.6764** |
| 变异门禁 | 8/8 拦截，0 存活，旧门禁仍漏 4/8 |
| repair gate | 通过（repairable 一次修复率 1.0，regressing 69 次全拒绝） |
| failure / case-mining / fuzz / benchmark / loadtest | 全部通过 |
| scaling / http_load / worker_lab / persist_lab | 全部通过（schema v2 下复跑） |
| pipeline-doc / examples | `--check` 通过 |
| `ruff check src tests evals scripts` | 通过 |
| `python -m mypy` | 23 个模块 0 错误 |
| 打包依赖 | `pip install -r requirements.lock` 可复现 dev/API 环境；`pytest-cov` 已进 dev extras |
| Docker 基础镜像 | digest 与 Docker Hub 标签一致（`mirror.gcr.io` 交叉校验）；本机无 Docker，未实际 build |
| wheel 打包 | `shotplan/web/*`、`shotplan/py.typed`、知识库 JSON 与 `licenses/LICENSE` 均已包含；元数据 `License-Expression: MIT`；非源码安装下 `WEB_DIR` 存在、默认 DB 落在当前工作目录 |
| 报告确定性 | 连续两次 `run_eval --write-report` / `build_showcase.py` 文件哈希一致 |
| CLI 冒烟 | `plan` 正常路径退出 0；`plan --budget 100` 退出 3 并返回结构化拒绝 |

### 复跑命令

```bash
python -m pip install -e ".[api,dev]"

python -m pytest --cov=src/shotplan --cov-report=term --cov-fail-under=85
python -m ruff check src tests evals scripts
python -m mypy

python -m evals.run_eval --write-report
python -m evals.mutation_lab --check
python -m evals.repair_lab --check
python -m evals.collect_failures --check
python -m evals.case_miner --check
python -m evals.fuzz --check --iterations 500
python -m evals.benchmark --check
python -m evals.loadtest --check --requests 30
python -m evals.scaling --check --requests 300 --workers 1 2
python -m evals.http_load --check
python -m evals.worker_lab --check
python -m evals.persist_lab --check
python scripts/build_pipeline_doc.py --check
python -m evals.examples --check
```

---

## 6. 剩余工作（建议下一批）

前两批的 P0 项与第三批的 `requirements.lock` / mypy / request-id / CORS / Settings 快照 / 知识库来源 / request_id 落 trace / 保留策略 / 个人材料下沉 / 链接校验 / 月度复核提醒都已落地，不要重做。

### P1 · 生产化

1. **限流的跨进程一致性**
   - 当前限流仍是进程内内存计数，多 worker 下每个进程各算一份。
   - 建议：部署层用网关 / Redis 做共享限流；或将服务固定为单 worker（当前 SQLite 写模型也倾向单 worker）。

2. **依赖锁与 CI 的关系**
   - `requirements.lock` 是参考环境，CI 仍按 `pyproject` 区间解析，以保留 3.10–3.13 矩阵的真实性。
   - 若要「CI 也锁」，应每个 Python 版本各生成一份锁，或在 3.12 使用锁、其余版本继续用区间；不要用一个 Windows 快照去锁 Linux 矩阵。

3. **Docker 构建验证**
   - 镜像 digest 已固定并通过两个 registry 交叉校验，但本机没有 Docker，未做 `docker build` 冒烟。
   - 建议第一次在有 Docker 的机器上跑 `docker compose up --build`，确认非 root 用户、命名卷与健康检查都正常，再把 digest 升级方式写进发布流程。

### P2 · 工程卫生

4. `.github/workflows/kb-freshness.yml` 依赖 GitHub 的定时任务通知；如果仓库不使用 Actions，需要换成本地 calendar / 定时脚本。

### 阶段 4 · 真实模型

- 仍未用真实 OpenAI 兼容端点跑评测；`good` provider 是规则实现，当前 100% 是自洽性检验。
- 下一步应优先跑 10–20 个真实模型用例，记录 prompt v1/v2 差异与真实失败，再把失败案例追加进 failure gallery。

---

## 7. 改规则 / 改数据时的重生成顺序

顺序错了，`--check` 门禁会因为产物过期而失败。推荐：

```bash
# 1) 失败案例库（会同时写 evals/failures/cases.jsonl 与 docs/failure-gallery.md）
python -m evals.collect_failures --write-report

# 2) 盲测挑战集（依赖失败案例库）
python -m evals.case_miner --write

# 3) 评测报告
python -m evals.run_eval --write-report

# 4) 修复实验
python -m evals.repair_lab --write-report

# 5) 真实输出示例
python -m evals.examples --write

# 6) 流水线契约（改过 STAGES / 规则说明时）
python scripts/build_pipeline_doc.py --write

# 7) 变异报告（改过门禁/校验器时）
python -m evals.mutation_lab --write
python -m evals.run_eval --write-report

# 8) 展示页（依赖 cost-latency / benchmark / eval / failure / challenge 产物）
python -m evals.cost_latency --write-report
python scripts/build_showcase.py
```

最后统一跑第 5 节的门禁。

只改 `source` / `last_verified_at` 不会改变评分，但会改变 `knowledge_version` 指纹；改价格、标签、地点或时长时，必须按上面 1 → 8 的顺序重生成，否则 `--check` 门禁会指出哪份产物过期。`docs/evaluation.md` 里的「机器相关数字怎么读」是手写文档，不需要生成。

---

## 8. 已知限制（不要过度承诺）

- 离线 `good` provider 是按规则写的确定性实现；80/80 与 0.9778 是「规则自洽性 + 对照组区分度」，不是真实模型质量。
- 200 个盲测 brief 来自固定网格，不是真实客户分布。
- 墙钟预算只能在节点之间检查；已经发出的 HTTP 请求不能被中断。
- `async` 持久化在进程被硬杀时，队列里的行会丢；默认仍是 `sync`。
- 限流是进程内计数，多 worker 不共享；公开部署应在网关限流。
- 运行历史按时间/条数删除，SQLite 不会自动归还磁盘；需要回收空间时停服 `VACUUM`。
- 性能/并发数字来自单机，不是生产 QPS。

---

## 9. 关键文件索引

- 规则与指纹：`src/shotplan/domain/constraints.py`
- 校验器：`src/shotplan/agents/validator.py`
- 可行性：`src/shotplan/domain/feasibility.py`
- 离线 planner：`src/shotplan/agents/mock.py`
- 检索：`src/shotplan/knowledge/tools.py`
- trace：`src/shotplan/tracing.py`、`src/shotplan/domain/models.py`
- 持久化、schema v2、stats/prune：`src/shotplan/storage.py`
- 编排与写库隔离：`src/shotplan/pipeline.py`
- API 错误映射、鉴权、限流、body 限制、请求 ID、Settings 注入：`src/shotplan/api.py`
- 配置/日志/DB 路径：`src/shotplan/config.py`
- 知识库加载与来源校验：`src/shotplan/knowledge/loader.py`、`src/shotplan/knowledge/data/*.json`
- 依赖与类型检查：`requirements.lock`、`pyproject.toml`（`[tool.mypy]`）
- 打包：`pyproject.toml`、`Dockerfile`、`docker-compose.yml`
- CI：`.github/workflows/ci.yml`、`.github/workflows/eval.yml`、`.github/workflows/kb-freshness.yml`
- 个人材料：`docs/portfolio/`；链接校验：`tests/test_docs_links.py`
- 新增 ADR：`docs/adr/0014-request-constraints-are-hard-rules.md`、`docs/adr/0015-reproducible-run-trace.md`、`docs/adr/0016-source-refs-must-resolve.md`
- 本批变更记录：`CHANGELOG.md` 的 `[Unreleased]`

---

## 10. 交接注意事项

1. 当前改动**未提交**；不要 `git reset --hard` 或 `git checkout -- .`。
2. 不要把 `web/` 再移回根目录：`api.py`、`pyproject.toml`、Dockerfile、README 都已按 `src/shotplan/web/` 更新。
3. SQLite schema 已是 v2；新增列请写迁移并 bump `_SCHEMA_VERSION`，不要直接改 `_SCHEMA` 后指望旧库自动升级。
4. 不要手工修改 `docs/eval-report.md` 的数字；改 `evals/run_eval.py` 后重跑。
5. 不要重写 `CHANGELOG.md` 的历史版本条目与 ADR 里的历史数字。
6. 再改规则时，记得同步：`constraints.py`、`validator.py`、测试、README/文档里的规则数、失败案例库、挑战集、评测报告、展示页。
7. 新增 API 路由如果应受保护，记得挂 `Depends(require_api_key)`；`/api/health`、`/api/rules`、统计/配置接口保持公开。
8. Settings 只在 lifespan 读取一次；新增路由用 `cfg: SettingsDep` 注入，不要在每个请求里重新 `get_settings()`。测试要改环境变量，必须在 `TestClient` 启动前设置（参考 `client_for` fixture），或用 `app.dependency_overrides[app_settings]`。
9. 知识库新增条目必须带 `source` / `last_verified_at`，否则加载即 `KnowledgeError`；日期用 ISO `YYYY-MM-DD`，且不能晚于今天。
10. `requirements.lock` 是参考环境，不是 CI 约束；改依赖后按文件头三条命令重新生成，不要手改版本号。
11. 请求 ID 的合法字符集是 `[A-Za-z0-9._-]{1,64}`；放宽之前先想清楚日志伪造与响应头注入。
12. 保留策略默认关闭（`SHOTPLAN_RETENTION_DAYS=0`）；打开后会在每次 API 启动时删除过期运行记录，演示环境不要随手设置。`prune_older_than` 只按 `created_at` 删除，不区分 provider 或状态。
13. 新增或移动 Markdown 文件后跑 `pytest tests/test_docs_links.py`；个人简历 / 面试稿放 `docs/portfolio/`，不要把个人材料放回 `docs/` 根目录。
