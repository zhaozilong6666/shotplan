# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

### Changed

- **选点从硬编码启发式改为多目标候选搜索**（`agents/mock.py`）：旧逻辑固定「一个普通场景 + 一个黄金光线场景」，是一条写死的猜测，无法针对不同 brief 做取舍。现在枚举候选集合（≤56 组，穷举即可解释），按六项加权打分——风格匹配 0.30 / 光线 0.22 / 人流 0.18 / 转场 0.14 / 预算 0.10 / 多样性 0.06——先过滤付不起的组合，再取最优；最长的主拍时段留给最匹配偏好的光线。
  - **效果，如实说**：主集 80 例均分 0.9778 → **0.9787**，盲测 200 例 **0.9886 持平**，硬约束 100%，变异门禁仍 **8/8**。+0.0009 在噪声范围内，**不宣称质量大幅提升**；真正的收益是按偏好换点（南京「上午」换到中华门、杭州「人少」换到西溪、洛阳两档分别换点）以及权重可论可调。
  - 顺带修掉一个真 bug：旧的 `budget_fit` 把「免费地点」算成 0 分（目标花费 70% 预算），等于系统性偏好收费景点，与「控制预算」的偏好正好相反。
  - 归档同步重算：eval-report / case-mining / mutation-report / examples。`_choose_points` 的候选集枚举与权重的说明写在函数 docstring 里，后续用评测调参而不是拍脑袋。

- **代码已同步到 GitHub**：`https://github.com/zhaozilong6666/shotplan`，远端 `main` 与本地 `c8fab2d` 完全一致，34 个提交 SHA 一一对应。
  - 内置 sites 发布通道持续失败（`SITES_UPLOAD_FAILED`，平台每次分配的随机实例域名不响应，见下），因此改走 `api.github.com`（该链路可达）完成代码发布。
  - 新增 `scripts/push_range_via_api.py`：原有 `push_via_api.py` 只能推「远端正好是 HEAD 父提交」的单步；远端落后 34 个提交时会拒绝，`--force` 又会造成历史分叉。新脚本逐个重建 `remote..HEAD` 中的每个 commit（真实 parent / tree / author / committer / message），**每个远端 SHA 都与本地一致**，最后一次性更新 ref；并强制检查「远端是本地祖先且无远端独有提交」，非快进直接拒绝。`--dry-run` 可预览。
  - 诊断记录：sites 上传失败与包体积无关（27.15MB→6.13MB 后仍失败）；基础域 `e2b.<区域>.sandbox.cloudstudio.club` 可达（302），但实例子域 `<uuid>.e2b.<区域>...` TCP 连上后 10–11 秒零字节，属平台侧实例不响应。

- **同一个需求给三种取舍，而不是一个「标准答案」**（`shotplan/compare.py` + `POST /api/compare` + 「方案对比」标签页）：出片优先 / 省钱优先 / 轻松优先，三份都**实跑完整 `run_plan` 并经过同一套校验器**，展示硬约束、综合得分、方案费用、拍摄点数、转场次数、有效拍摄分钟，以及**实际算出来的差异**（「少 95 分钟拍摄」「得分低 0.025」），不是营销文案。策略通过调整时长/预算/排除地点真实改变候选集；检测到两份方案相同时会显式标注「与 X 产出相同方案」，不假装三个选项。推荐依据来自用户自己的偏好词（「控制预算」→ 省钱优先），没有偏好时按「先过硬约束、再比得分」并写明依据。6 个新单测。

- **让系统听懂一句话，并把推断摊开给你核对**（`shotplan/brief.py` + `POST /api/understand` + 前端一句话入口）：真实客户不再需要把意图翻译成 6 个表单字段。解析出 8 个字段，每条标注 `明说 / 推断 / 默认` + 证据片段 + 置信度；「温柔」「清冷」「氛围感」等美学词会低置信推断风格与光线偏好；关键时长来自推断时主动追问「我按 3 小时理解，对吗？」；年份不会被误读成预算；同一句话解析结果确定性一致（request_id 由输入派生）。
- **把「不可行」变成可点选的协商**（`shotplan/negotiation.py`）：预检拒绝时给 2–3 个最小改动方案（按最低条件调整 / 留点余量），**每项都实跑 `check_feasibility` 验证确实可行**才返回，并写清代价，点「采用」直接回填表单。
- [docs/intelligence-roadmap.md](docs/intelligence-roadmap.md)：回应「还是不智能」——诊断是**智能主要在判卷不在做题**，列出四个体感缺口的根因、本轮补了哪三层、以及按杠杆排序的 5 个下一步（多方案取舍 / 推荐理由 / 生成中协商 / 会话记忆 / 让模型真正参与规划）。明确写出这一层是确定性规则解析，不是大模型理解。
- 9 个新单测（`tests/test_brief.py`）。

- **产品能力：界面由知识库驱动，5 座城市全部真正可用**。此前前端城市下拉**写死只有「西安」**，知识库里另外 4 座城市用户根本选不到——数据加得再多也到不了产品里。新增 `/api/catalog`（城市 id / 名称 / 地点数 / 室内备选数 / 标签 / 最近核验日、风格列表与价格区间、知识库版本指纹），前端启动时动态重建两个下拉，并显示「知识库：5 城 · 40 地点 · 5 风格」与所选城市的资料核验日期。**以后新增城市只需改数据，前端自动出现**，不会再出现数据与界面分叉。3 个测试锁定：目录与知识库完全一致、每个城市都能真的产出可执行方案、前端不依赖硬编码列表。真实 uvicorn 下验证苏州方案 5 个时间块、硬约束通过。

- **知识库数据契约（`scripts/validate_knowledge.py`，阶段 8 扩城准备）**：校验 14 个地点字段 / 11 个风格字段、`id` 全库唯一、`best_light` 枚举、`indoor` 布尔、时长/交通/价格非负、客流与天气枚举、风格价格区间、`source` 非空、`last_verified_at` 合法非未来。现有 **5 城 40 地点 5 风格 0 问题**；故意构造的坏记录能被全部拦下（2 个单测）。`--check` 是第 25 道门禁。[docs/knowledge-contract.md](docs/knowledge-contract.md) 明确**不编造城市数据**：工具保证格式与可复核性，保证不了票价是真的，扩到 10 城仍需人工核验官方资料。

- **人类一致性标注工具链（`evals/annotation_kit.py`，阶段 8 收尾准备）**：生成 30 份**盲标注**方案（good/naive 各半随机打乱，校验器判定不出现在标注表以避免锚定）、两张空白 CSV、无序 Cohen's κ 与二次加权 κ 计算器、`--score` 输出逐题 κ 与所有分歧案例。计算器用已知答案自检（完全一致=1、完全相反=−1、相邻分歧加权 κ 高于极端分歧），4 个单测 + `--check` 门禁。**报告明确不含 κ 结论**——没有两位真人标注就写数字是伪造证据；ROADMAP 该项保持未勾选，只是把验收条件变成可执行的一步。见 [docs/annotation-kappa.md](docs/annotation-kappa.md)。
- **README 散文数字纳入门禁**（`scripts/check_doc_claims.py`）：从代码算出 10 个指标（80 用例 / 69 需生成 / 11 不可行 / 200 盲测 / 527 失败记录 / 15 规则 = 10 硬 + 5 软 / 5 城市 / 17 ADR），逐条比对 README 的 13 处声明。**又抓到 3 处真实问题并修正**：ADR「16 份」→ 17；测试数「301」实则已 400，判定为**自指数字**，改为不在散文里写死并由门禁禁止手写；面试稿把「15 条规则」写成「15 条硬约束」（与早先修过的口径错误同源）。门禁 23 道。详见 [architecture-review.md](docs/architecture-review.md) 末节。
- **HTTP 全链路冒烟 + 上线就绪度评估**（`evals/http_smoke.py`，真实 uvicorn 子进程、10/10 端点契约、已进门禁）：覆盖 health/config/rules/plan(可行+不可行)/runs/summary/stats/metrics/首页，重点验证「预检拒绝不算质量分母」「summary 与 stats 口径一致」「metrics 是状态感知 gauge」。单测走 TestClient 会跳过 lifespan 配置快照与 loopback 网络栈，这一层补上。
- **API + 真实模型最终冒烟（¥0.047）**：真实 uvicorn → `build_provider(cfg)` → real provider 一次调用，trace 同时出现构造值 `planner-v2` 与实际生效 `planner-v3`，`/api/summary` 与 `/metrics` 口径一致（pass=1.0、error=0.0、llm_cost=0.046667）。之前的复测只走了 eval harness 与 CLI，这一条补齐。
- [docs/release-readiness.md](docs/release-readiness.md)：明确「代码可部署、证据未到生产可用」，列出 6 项上线前必做（抓取端、黑盒探针、密钥注入、代理注入 key、SLO 重校准、多进程写锁复核）与 5 类只有真实流量才暴露的未知；**阶段 9 验收项「一周真实流量分布图」仍为空，不靠门禁勾掉**。
- 门禁 22 道（新增 http_smoke），README 数字由 `check_gate_count.py` 自动校验。
- **全仓体检 + 阶段 9 两项落地**：8 份快照/报告对全部同步、3 项辅助门禁通过、ADR 17/17、无残留断点。查出并修掉 4 个真实问题：① 上一轮 P1/P2 重构 19 个文件**未提交**（已补 `8fe1616`）；② 两份历史快照含完整 endpoint URL → 新增 `check_snapshot_hygiene.py`，历史显式列为例外、新快照再犯即红；③ README「16 道门禁」vs Makefile 实际 21 道 → 新增 `check_gate_count.py` 自动校验 README 每一处声明；④ `_safe_save` 漏捕获 `OSError ENOSPC`，会把有效方案变成 error，已修并在故障注入里验证。详见 [architecture-review.md](docs/architecture-review.md) 末节。
- **故障注入矩阵**（`evals/fault_injection.py`，12/12，纯离线、进门禁）：provider 超时/429/502、解析失败、SQLite 锁、磁盘满、无权限、知识库损坏、校验器异常、断供不污染分母、API 502/503 映射。绿色标准是「失败被分类、可追溯、不污染质量指标」，不只是「没抛异常」。见 [docs/fault-injection.md](docs/fault-injection.md)。
- **SLO 从实测推出**（`evals/slo_baseline.py` + [docs/slo.md](docs/slo.md)）：基线表由 7 份快照生成并纳入门禁。**纠正原假设**「95% 请求 30s 内返回」——实测 P50 40.5–91.5s、P95 63.9–171.8s，该目标从写下起就不可能达成。改为 P50 ≤95s / P95 ≤180s / 通过率 ≥90% / 上游错误率 ≤5% / 单次 ≤¥0.08，附 PromQL、告警规则、误差预算，并写清 gauge 不能用 `rate()`、可用性必须靠黑盒探针。
- **P1/P2 重构后的端到端复测（¥0.14）**：同批 4 例，重构前后通过率均 1.0、逐例 prompt 版本分布 `{v2:2,v3:2}` 完全一致；新快照带 22 项 `effective_config`，`temperature` 从 settings 读取（0.25）而非硬编码。首次运行上游挂起触发 deadline 降级（旧实现会无限卡住），并暴露 `real_model_lab` 只认 `PIPELINE_ERROR` 导致超时原因写成含糊文案——已抽成 `_fatal_message()` 覆盖 `DEADLINE_EXCEEDED` 并加单测；断点续跑 4/4 零错误。得分变化明确标注为**不可归因**（temperature 变动 + 单轮 4 例）。
- **架构收敛第二轮（P1/P2，13 个新单测，零 API 成本）**：见 [ADR 0017](docs/adr/0017-one-settings-object-per-run.md) 与 [架构评审末节](docs/architecture-review.md)。
  - **一个请求一个 Settings**：`get_settings()` 唯一入口、`build_provider(settings)` 强制传参、双对象触发 `ConfigError`（且不降级）；`run_plan` 顶层错误边界把 preflight/检索/validate 异常变成 `status=error` 而非 500。
  - **快照可复现**：`Settings.resolved()` 写入完整非敏感生效配置（含断点元信息，换配置续跑会被拒绝）；`temperature` 不再硬编码；报告新增「生效配置」表，7 份历史报告 `--check` 全部同步。
  - **指标改名**：`plan_cost_cny` / `llm_cost_cny` / `wall_ms` 别名，消除「同名字段指两种钱」；`/metrics` 加 `shotplan_llm_cost_cny`。
  - **边界**：`REPAIR_FAILED` soft issue 让被吞掉的修复可见；`ProviderError` 只留 host 并脱敏；写锁按库分片；`stats_rows` 加 `run_id` tiebreak。
- **P0 架构修复的端到端复测（¥0.24）**：router 开启下真实跑 4 条 holdout，快照给出 `{planner-v2: 2, planner-v3: 2}` 的逐例分布；CLI 两次 `--persist` 在 DB 分别落 v3 / v2；往 DB 注入一条 `status=error` 后 `hard_pass_rate` 仍为 1.0、`error_rate=0.5`（旧实现会报 0.5）。证据与剩余风险见 [docs/architecture-review.md](docs/architecture-review.md) 末节。**P1-1 配置双路径仍未解决，下一轮真实评测前处理。**
- **宏观架构评审 + 三项 P0 修复**：全仓只读扫描后写下 [docs/architecture-review.md](docs/architecture-review.md)——项目最强的「测量层」与「运行时层」已经分叉：一条请求有两个 Settings 对象、逐请求 prompt 版本没落库、我们在评测报告里修过的「断供不算质量差」在生产指标里原样保留、墙钟预算管不住 `generate`（今天三次卡死的根因）。全部 P0 已修，19/19 门禁，零 API 花费：
  - `GenerationResult.prompt_version` / `RunTrace.prompt_version_effective` / DB 列 / 评测快照分布，逐例可复现实际用的 prompt；
  - 质量指标只统计 `status=ok`，新增 error/infeasible 计数与比率、`/metrics` 两个新 gauge；
  - 用 `accepts_deadline` opt-in 把绝对 deadline 传给 HTTP provider，裁剪每次 `_post` 超时、ladder、重试与退避，慢生成直接返回 error。
  - 刻意不改 `PlanProvider` 协议（10+ 实现）、不加数据库列，理由写在文档里。
- **Prometheus 指标导出（离线）**：`GET /metrics` 返回 `text/plain; version=0.0.4`，覆盖运行数、硬约束通过率、P95/P50、修复数、花费、按状态分类与 provider × prompt_version 下钻，零新增依赖。刻意全部标为 **gauge**——数据来自最近 N 条的滑动窗口，标成 counter 会让 `rate()` 算出假增速；标签值做了转义，空库也输出完整 HELP/TYPE。5 个单测覆盖格式、转义、窗口语义与端点。见 [docs/observability.md](docs/observability.md)。
- **知识库新鲜度门禁（离线）**：`scripts/check_knowledge_freshness.py` 检查全部 45 条地点/风格记录的 `source` 与 `last_verified_at`，列出超过 N 天未核验、缺来源、日期非法（含未来日期）的条目；接入 `make gates`（阈值 180 天），过期会让 CI 变红，逼迫重新核对票价与光线条件。三个单测覆盖边界（恰好 180 天不算过期）。
- **修复循环归因（离线，零成本）**：`evals/repair_attribution.py` 汇总 8 份快照、315 条逐例记录，生成 `docs/repair-attribution.md` 并纳入门禁。结论：未触发修复的用例通过率 96.6%，修过 1 次的只有 80%；`MISSING_REQUIRED_LOCATION` 修复成功率 71%（5/7），`UNKNOWN_SOURCE_REF` 3/3、UNKNOWN_LOCATION 2/2，但样本 <10 一律标注「不下结论」——**没有任何一种失败被证明救不了**。未来快照的 `per_case` 新增 `initial_hard_codes` / `final_issue_codes`，可逐例追溯。
- **prompt 路由的前瞻验证（未通过）**：另造 16 条 holdout（8 光线敏感 + 8 普通，与调参集无重合），两臂各跑一轮。整体得分 +0.0007（95% CI [−0.0343, +0.0338]，测不出差异），光线组仅 +0.0025，`LIGHT_MISUSE` 7→8、软问题用例 43.8%→50%。**调参集上的 +0.0431 没迁移，是选择偏差**。`SHOTPLAN_PROMPT_ROUTER` 保持默认关闭。holdout 集、两臂快照与报告均已归档。见 [docs/router-validation.md](docs/router-validation.md)。
- **按 brief 选 prompt（默认关闭）**：`SHOTPLAN_PROMPT_ROUTER=1` 时，偏好出现「上午 / 正午 / 不拍傍晚 / 室内为主」或必去晨光、夜景限定地点，且时长 ≤10 小时，就改用 `planner-v3`；其余仍用 v2。实际使用的版本写入该次调用的 `metadata.prompt_version`，可逐例审计。**这还只是实现，不是结论**：必须在一批新用例上前瞻验证，不能用挑出这 12 条的同一批数据自证（选择偏差）。
- **红队 v2 与一次结论翻案**：12 条光线敏感 brief 让 `LIGHT_MISUSE` 触发 9/12（75%，主集 38%）。用这批题重测此前被否决的 `planner-v3`：得分 0.8279 → 0.8710，配对差值 **+0.0431（95% CI [+0.0127, +0.0763]，6 胜 0 负）**，`LIGHT_MISUSE` 9 → 7。结论修正为「**v3 对光线敏感 brief 有效、对普通 brief 无效且更贵**」，仍不设默认。方法论收获：A/B 样本必须让被检验的机制有机会失败。见 [docs/redteam-analysis.md](docs/redteam-analysis.md) 与 [docs/prompt-ab.md](docs/prompt-ab.md)。
- **红队集 v1 与一个反直觉结论**：30 条更难用例（长白天光线、多地点挤压、预算贴底、排除冲突、输入噪声 + 6 条预检拒绝），离线先验证「naive 0/24 全败、规则 Agent 21/24 可解」再花钱。真实模型 **23/24（95.8%）**，高于主集——**硬约束层已饱和**，真正的弱点是 `LIGHT_MISUSE`（红队 8 次、主集 26 次，比例一致）。报告新增 `soft_issue_rate` 指标，下一版红队将瞄准软问题。见 [docs/redteam-analysis.md](docs/redteam-analysis.md)。
- **首份三轮重复测量**：80 例 × 3 轮（240 次调用，¥4.36，86 分钟）。通过率 94.2%（65/69，95% CI 88.4–98.6%），三轮极差 0.0pp、得分极差 1.38pp。关键发现：4 个用例三轮全部 502 断供、从未响应，把通过率从「已响应用例 100%」拉到 94.2%——**分母里有什么，比极差多大更重要**。见 [docs/real-model-repeat3.md](docs/real-model-repeat3.md) 与 [measurement.md](docs/measurement.md)。
- **prompt A/B 与一个被否决的版本**：新增 `planner-v3`（枚举、60 分钟下限、光线时段对齐、输出前自检）与运行时开关 `SHOTPLAN_PROMPT_VERSION`；`make ab-prompt` 一条命令跑两臂并做配对比较。结果：22 个可生成用例上得分 +0.0115（95% CI [−0.020, +0.043]，测不出差异），成本却高 53%、输出长 56%、P50 慢 31%，**故不设为默认**。分析见 [docs/prompt-ab.md](docs/prompt-ab.md)。
- **成本杠杆与分档跑法**：新增 `SHOTPLAN_LLM_MAX_TOKENS` / `SHOTPLAN_LLM_TEMPERATURE` / `SHOTPLAN_LLM_REASONING_EFFORT`（默认关闭，端点不支持时自动丢弃字段并重试一次，设置写入 `trace.config`）；评测新增 `--profile smoke|iterate|full`（8×1 / 30×1 / 80×3）。实测账单显示输出 token 占九成，详见 [docs/cost.md](docs/cost.md)。
- **重复测量与可检测下限**：`real_model_lab --repeat N` 每例跑 N 轮，报告同时给「用例级 95% CI」与「轮次间极差」两类不确定性；新增 `evals/power.py` 计算样本量（69 例只能检测 8–11pp，检测 3pp 需 492–984 例）与 `evals/pair_compare.py` 做配对比较（区间跨 0 即拒绝下结论，退出码可直接当门禁）。方法见 [docs/measurement.md](docs/measurement.md)。
- **解析失败的一次有界修复**：`parse_plan` 抛错时不再直接返回 `error`，而是消耗一次修复预算、带着 schema 约束重生成（`repair_parse`，最多一次，仍失败才降级）。单元测试覆盖“修好”“预算为 0 不尝试”“一直坏也只试一次”三种路径。注意：三个真实 error 复跑未复现，说明它们是采样长尾，不能据此声称通过率提升——见 `docs/failure-taxonomy.md`。
- **报告自带不确定性**：真实模型报告对通过率与平均分输出 95% 自助法区间（固定种子，可复现）。n=69 时通过率区间约 ±5 个百分点，比多数人想声称的模型差异还宽。
- **[失败模式与测量不确定性](docs/failure-taxonomy.md)**：区分“构造失败”与“真实失败”，量化 527 条记录的规则分布，并对唯一的硬失败与三个 schema error 做根因分析。

### Changed

- **请求约束闭环**：`must_include` / `avoid` 从检索提示升级为硬规则（`MISSING_REQUIRED_LOCATION` / `AVOIDED_LOCATION`）；不可行判定新增 `UNKNOWN_REQUIRED_LOCATION` 与 `ALL_LOCATIONS_EXCLUDED`；`source_refs` 新增存在性硬规则 `UNKNOWN_SOURCE_REF`；规则 12 → 15（10 硬 + 5 软）。离线 planner 现在会优先安排必去地点，且不再用被排除的室内棚做妆造。见 [ADR 0014](docs/adr/0014-request-constraints-are-hard-rules.md)、[ADR 0016](docs/adr/0016-source-refs-must-resolve.md)。
- **可复现运行记录**：trace 增加 `kb_version` / `rules_version` / `wall_duration_ms` / 请求级配置快照；SQLite schema v2 增加 `status` 列与 `user_version` 迁移。见 [ADR 0015](docs/adr/0015-reproducible-run-trace.md)。
- **统计与保留**：`/api/stats` 改为读取轻量 SQL 列，不再解析完整 payload；`shotplan history --prune N` 可清理旧记录。
- **部署保护**：可选 `SHOTPLAN_API_KEY`、进程内限流、请求体大小限制；配置真实 provider 后客户端不能再通过 `mock_mode` 绕过；Docker 改为非 root + 命名卷。
- **错误语义与持久化隔离**：provider → 502、storage → 503、配置/知识库 → 500、解析/输入 → 422；写库失败返回方案 + `PERSISTENCE_FAILED` 软问题，不再否决结果。
- **打包修正**：`web/` 移入 `src/shotplan/web/` 并随包发布；默认数据库路径在非源码安装时回退到工作目录；补 `py.typed`；`shotplan eval` 在 wheel 环境下给出明确提示。
- **CI 拆分**：lint + 单元测试保留 3.10–3.13 矩阵；评测/变异/压测/对照实验等重门禁只在 3.12 执行一次，并加 `permissions` / `concurrency` / `timeout-minutes`。
- **配置与可观测性**：`Settings` 改为启动时解析一次（`app.state.settings`），可用依赖覆盖替换；新增请求 ID 中间件与单行结构化访问日志（`X-Request-ID` 回传，非法 id 会被替换），429 / 503 响应带 `request_id`；明确不启用 CORS，并补测试防止误加通配符。
- **知识库来源字段**：每个地点/风格条目新增 `source` 与 `last_verified_at`，加载时校验；新增「知识库超过一年未复核」测试。数据版本 1.1 → 1.2。
- **追踪闭环与保留策略**：`/api/plan` 把 HTTP `request_id` 写进 `trace.config`，历史运行可反查日志行；新增 `SHOTPLAN_RETENTION_DAYS`（启动时清理过期记录，默认 0 = 永久保留）与 `history --prune-days N`。
- **Docker 基础镜像固定 digest**：`python:3.12-slim-bookworm@sha256:935c3c5b…`（多架构索引，2026-11-13 通过 mirror.gcr.io 校验），避免重建时基础镜像悄悄漂移。
- **打包元数据现代化**：`license` 改为 SPDX 字符串 + `license-files`，构建要求 `setuptools>=77`，移除已废弃的 License classifier；构建不再出现 setuptools 弃用告警，wheel 元数据写出 `License-Expression: MIT`。
- **类型检查与依赖锁**：新增 mypy 配置（`src/shotplan`，23 个模块零错误）、`pytest-cov` 进入 dev extras、`requirements.lock` 记录一份可复现的 API + 开发环境；CI 增加类型检查步骤。

### Fixed

- **重复模式的三个聚合错误**：`min_score` 把「从未响应」（502）的用例按 0 分算进最低分；`preflight_refused` 把 3 轮累加成 33 次（实际 11 个用例）；只有不可行用例时通过率会除零崩溃。三处均已修正并有单测。
- `must_include` 被忽略仍能拿高分：C18 等 9 个用例此前是空转，现已真正覆盖。
- `source_refs` 只计数不校验：伪造引用此前仍能拿溯源分，现在直接硬失败。
- `SHOTPLAN_LOG_LEVEL` 是死配置；现在 CLI 与 API 启动时生效。
- **聚合接口的鉴权边界不一致**：`/api/summary` 与 `/api/stats` 此前在设置 `SHOTPLAN_API_KEY` 后仍可匿名访问，等于绕过了 `/api/runs` 的保护；现已一并要求 `X-API-Key`，公开接口只剩 `/api/health`、`/api/config`、`/api/rules`。内置面板不发送该头，受保护部署需由反向代理注入或改用独立前端。
- **文档口径错误**：简历与面试稿把「15 条规则（10 硬 + 5 软）」写成「15 条硬约束」，会把软评分说成硬失败；测试数与覆盖率同步为 304 / 90.9%。`PERSISTENCE_FAILED` 属于运维码而非方案规则，已在可观测性文档中单列说明，不进入 `RULES`。
- `pipeline.py` 两个 `__all__` 导致 `STAGES` / `Stage` 不在通配导出里；预检被重复计算的开销也一并去掉。
- 文档数字漂移：263 → 301 个测试、517 → 527 条失败记录、12 → 15 条规则、README「十四份/十二块」不一致；CHANGELOG 0.10.1 的重复条目。

### Added

- **`serve --persist-mode off|sync|async`**：演示异步写库不再需要改环境变量；CLI 会在启动时打印当前模式。配套测试断言该值真的进入应用进程环境。
- **[docs/portfolio/interview-onepager.md](docs/portfolio/interview-onepager.md)**：面试一页纸（30 秒开场、数字卡片、5 个必考问答、边界与现场可跑命令），由面试笔记压缩而来，每个数字带出处链接。
- **[docs/portfolio/resume.md](docs/portfolio/resume.md)**：按 AI 应用/Agent 开发方向写好的整段简历项目经历 + 技能清单 + 与 JD 要求的对应表；USER_TASKS 补充真实模型 key 的分步配置说明（含 `.env` 上传风险警告）。

阶段 4 剩余项：接真实模型跑 `openai_compatible`、记录 prompt v1/v2 的实测差异。详见 `docs/ROADMAP.md`。

## [0.10.1] - 2026-11-13

**Web 统计页接上写入队列的可视化，并补上 0.10.0 忘记更新的包版本号。**

### Added

- 「运行统计」标签页新增**持久化面板**：显示 `persist_mode`；`async` 模式下显示 `pending / written / failed / sync_fallbacks / batches / max_batch`，`pending > 0` 黄色告警、`failed > 0` 红色；附刷新按钮，每次切到该页自动重取。`/api/config` 缺失或不可达时不影响统计主视图。
- 演示路径：`SHOTPLAN_PERSIST_MODE=async python -m shotplan.cli serve` → 打开「运行统计」→ 连点几次生成，可看到 `pending` 从非 0 归零。
- `evals/persist_lab.py --report`：从已提交的快照重排 `docs/persistence.md`，不必重跑矩阵；边界段落里的「本批倍数」改为由快照计算——此前那句话是手改的，重跑矩阵后出现过结论段 5.13× 与边界段 11× 并存的内部矛盾。

### Fixed

- **`__version__` 仍为 0.9.0**：v0.10.0 的 Release 与 CHANGELOG 都写了 0.10.0，但包版本字符串漏改，`/api/health` 与 `pip` 元数据因此报旧版本。修复并在此记录——发布清单里应该有一行「版本字符串与 tag 一致」。



## [0.10.0] - 2026-11-13

**把上一版留下的建议实现出来，并给「解析信任边界」补上真实缺陷的修复**：v0.9.0 的结论是「同步写库是最大开销，应异步化而不是删掉历史」——这条建议当时既没实现也没验证。这一版实现三模式写入、量出收益与丢失窗口；同时把流水线从「顺序代码 + 手绘图」改成代码生成的状态表，并加墙钟预算。

### Added

- **`SHOTPLAN_PERSIST_MODE=off|sync|async`**（`Settings.persist_mode`，默认 `sync`）：`off` 完全不写；`sync` 沿用 v0.9.0 行为；`async` 交给进程内写线程批量提交，退出时排空。旧的 `SHOTPLAN_PERSIST_RUNS=0` 继续有效（映射到 `off`）。
- **`RunWriter`**（`storage.py`）：有界队列 + 单写线程 + `save_many` 批量事务；队列满时**退化为同步写**并计入 `sync_fallbacks`（不丢行）；批次失败重试一次、再失败计入 `failed` 而不杀线程；`flush()` 是真正的屏障。`get_writer()` 按数据库路径做进程级单例。
- **`/api/config` 暴露写队列状态**（`submitted / written / pending / failed / batches / max_batch / sync_fallbacks`），`lifespan` 在 shutdown 时排空队列。
- **三组对照实验**（`evals/persist_lab.py`）：`off / sync / async` × `1 / 4 worker` × 2 轮同批交替；压测框架新增 drain 轮询（响应返回 ≠ 行已提交），产物 [docs/persistence.md](docs/persistence.md) 与 `evals/persist_lab.json`。
- **`--check` 作为 CI 第 15 道门禁**：断言三组 0 错误、`off` 落库增量为 0、`sync`/`async` 增量等于成功请求数、服务端回报的模式与分组一致。吞吐与排空时间不进门禁。
- **`STAGES` 状态表 + 生成式契约文档**（`scripts/build_pipeline_doc.py` → [docs/pipeline-contract.md](docs/pipeline-contract.md)）：6 个节点的输入/输出/失败转移由代码生成，Mermaid 图同源；`--check` 比对文档漂移，是 CI 第 16 道门禁。
- **`SHOTPLAN_PIPELINE_BUDGET_S`**（默认 60s，0 关闭）：墙钟预算。超预算时停止开启新的修复轮次，**返回当前最优草稿**并追加 `REPAIR_BUDGET_EXCEEDED` 软问题；只有连第一次生成都没开始才返回 `status=error` + `DEADLINE_EXCEEDED`。
- **解析器硬化**（`planner.py`）：括号配平扫描替代 `find/rfind`；支持多个 JSON 对象时逐个尝试构建、跳过示例对象；末尾逗号与全角引号做**有记录的**修复（写进 trace 的 `notes`）；BOM / 零宽字符 / bytes 载荷；数字只接受无歧义形式（`300` / `"¥300"` / `"免费"`），读不出就 `ParseError`。
- [ADR 0012 写后置队列与可测量的持久性边界](docs/adr/0012-write-behind-persistence.md)、[ADR 0013 状态表与墙钟预算](docs/adr/0013-explicit-stage-table-and-budget.md)。
- 新测试 50 个（共 **263** 个，覆盖率 90%）：写后置队列 11 个（含子进程 `os._exit` 硬杀两条）、解析对抗 25 个（含 300 次确定性变异）、流水线契约 12 个、API 异步/关闭模式 2 个。

### Fixed

- **信任边界漏成 500**：`parse_plan` 里 `int(item.get("cost_cny") or 0)` 在模型返回 `"免费"` 时抛 `ValueError`；流水线只 catch `ShotplanError`，于是单个脏数字让 `/api/plan` 返回 500。现在解析层把一切异常收敛为 `ParseError` → `status=error` 的结构化失败。
- **JSON 定位错误**：`text.find("{")` + `text.rfind("}")` 在字符串值含 `}`、或 JSON 之后的说明文字含 `}` 时会切错；改为字符串与转义感知的括号配平扫描。

### Measured

- 异步相对同步：1 worker **2.96×**、4 worker **1.95×**；P95 186ms → 93ms；拿回约 **47%** 的写库开销；三组 **0 丢写**，排空窗口 0.04–0.13s。另一批独立运行得到 5.5× / 45%：**拿回比例稳定，绝对倍数随机器负载变化**。
- 同批内关掉写库比同步写快 **5.13×**。这个绝对值在三次测量里分别是 2.48× / 11× / 5.13×，说明差异来自磁盘、临时目录与后台负载而非代码。报告因此只做同批内相对比较。
- 硬杀窗口：子进程先 flush 再 `os._exit` → 父进程读到全部 N 条；不 flush 直接退出 → 读到 N−pending 条，与 `pending` 一致。

### Changed

- CI 新增 write-behind gate 与 pipeline-doc gate（共 16 道）；Makefile 新增 `persistlab` / `pipelinedoc` 并纳入 `gates`。
- 展示页新增「写入异步化」区块，数据全部读自 `evals/persist_lab.json`；`README` / `README.en` / `ROADMAP` / `runbook` / 面试笔记同步更新。


## [0.9.0] - 2026-11-13

**给上一版的性能归因补上对照实验**：v0.8.0 从一次测量推断「4 worker 变慢是共享 SQLite 写锁」，那是机制解释，不是受控实验。这一版加开关、做干预，并允许实验判定「不确定」。

### Added

- **`SHOTPLAN_PERSIST_RUNS`**（`Settings.persist_runs`，默认 `True`）：为 `0` 时 `/api/plan` 把 `store=None` 传给 `run_plan`，跳过运行历史写入；同一份产品代码路径，HTTP / ASGI / JSON / provider / 校验 / 修复全部不变。也有独立产品价值：无状态部署可关掉历史写入。
- **对照实验**（`evals/worker_lab.py`）：1/2/4 worker × 持久化开/关 × 2 轮、每轮 200 请求，两组在同一批内交替跑，取中位数并列出每轮原始值。产物 [docs/worker-bottleneck.md](docs/worker-bottleneck.md) 与 `evals/worker_lab.json`（标注 `machine_dependent`）。
- **判定逻辑先检验现象是否复现**：`interpret()` 检查有写入组逐轮 `4w/1w` 比值，可输出 `confirmed` / `partially_confirmed` / `not_reproduced` / `inconclusive` / `refuted`；`relation_text()` 随判定动态生成「与上一版报告的关系」，绝不写死「已证实」或「已证伪」。
- `--check` 作为 CI 第 **14** 道门禁：只跑 1 worker 开/关各 20 请求，断言 0 错误、**开 = 全部落库**、**关 = 0 落库**（证明开关真的生效）。吞吐与结论文字不进门禁。
- [ADR 0011 对照实验优先于归因推断](docs/adr/0011-controlled-experiment-over-inference.md)；9 个新测试覆盖 5 种判定、中位数聚合、开关语义与环境变量解析。

### Measured

- 关掉同步写库后 1 worker 吞吐 **2.48×**、2 worker **3.59×**：持久化是关键路径上最大的单项开销，这一条在所有轮次稳定。
- 有写入组 4w/1w 逐轮 **0.86 / 0.92**（中位数 0.89），关掉写入后 **1.59×**；本批数据**支持** v0.8.0 的写锁归因，不做推翻。
- **但结论是有条件的**：另一批独立探针里有写入组 4 worker 反而比 1 worker 快 2.42×，「回落」本身没复现，按同一套逻辑会得到 `inconclusive` / `not_reproduced`。因此 `docs/http-load.md`、展示页 note 与中英文 README 都改为标注「归因经对照实验支持但依赖负载稳定复现」，并公开逐轮原始值。
- 工程建议：把运行历史写入**异步化 / 批量化**移出同步关键路径（不是删掉历史——它是可观测性与回归追溯的基础）；worker 数实测找拐点，不是越多越好。

### Changed

- CI 新增 Worker/persistence gate；Makefile 新增 `workerlab` 目标并纳入 `gates`。
- 展示页新增「对照实验」区块；中英文 README、路线图、任务手册、排错手册、评测方法论、面试讲稿 Q&A、贡献指南按 v0.9.0 同步；版本 **0.9.0**。

### 边界

单机 loopback、离线 provider、小样本；绝对值跨运行可差数倍，只能看同一批内相对关系。主评测集仍 80/80、0.9788 / 0.3014，v1–v6 基线不受影响，默认持久化行为不变。

## [0.8.0] - 2026-11-13

**把性能证据从「调函数」推进到「跑服务」**：之前的吞吐全部来自进程内微基准，没有 HTTP 解析、ASGI 中间件、完整 plan + trace 的 JSON 序列化、loopback 网络栈，更没有**多个 OS 进程同时写同一个 SQLite 文件**。

### Added

- **真实 HTTP 压测**（`evals/http_load.py`）：启动真正的 `uvicorn` 子进程（1 / 2 / 4 worker），用 `httpx` 走 `127.0.0.1` 发 `POST /api/plan`，测并发 1 / 4 / 8 的吞吐与 P50/P95，并用 `GET /api/stats` 的 `total_runs` 增量校验每个成功请求都落库。产物 `evals/http_load.json`（明确标注 `machine_dependent`）与 [docs/http-load.md](docs/http-load.md)。
- `--check` 作为 CI 第 **13** 道门禁：只跑一组稳定场景（1 worker / 并发 4 / 40 请求），断言 0 错误、全部 `status=ok`、**0 丢写**与 P95 < 3000ms 的防挂上限。吞吐、延迟绝对值、worker 加速比**都不做断言**，沿用 [ADR 0008](docs/adr/0008-fuzz-and-benchmark-gates.md) 的原则。
- 10 个新测试（`tests/test_http_load.py`）：门禁契约（含「高 P95 在非门禁模式不该失败」）、快照标注、报告边界、brief 可执行性，以及一次真实 uvicorn 往返，用来锁住下面的代理回归。

### Measured

- 单 worker 并发 1 / 4 / 8：**116 / 238 / 251 /s**，P95 12 / 22 / 40 ms；1000 次真实请求 **0 错误、1000/1000 落库**。
- worker 扩展曲线（并发固定 8）：1 → 2 → 4 worker 为 **251 / 280 / 130 /s**，最佳是 2 worker（1.12×），4 worker 反而掉到 **0.52×**、P95 涨到 150 ms。
- 结论：进程内进程池里 worker 不共享状态，真实服务里每个请求都要写一条 run 记录，WAL 同时只允许一个写者，**瓶颈从 GIL 变成了共享 SQLite 写锁**。要横向扩展得先把共享状态移出关键路径，而不是只加 worker。

### Fixed

- **本机代理改写 loopback 请求**：首次运行时除 `/api/health` 外所有端点都返回 404，uvicorn 日志显示请求行是 `GET http%3A//127.0.0.1.../api/stats`（absolute-form）。原因是 `HTTP_PROXY` 让 httpx 把相对路径拼成完整 URL 发给代理。压测客户端改用 `trust_env=False`，并加真实往返测试防止回归。
- **展示页一致性门禁偶发红**：成本模拟的 P50/P95 之前用实测值取整到毫秒，实测值含本机流水线开销，`round(1200.6)` 会在某次运行变成 `1201`，导致 `git diff --exit-code docs/index.html` 无端失败。既然延迟本来就是价格卡配置值，报告与展示页改为直接显示该配置值（两列相同），实测值只留在 `evals/out/cost-latency.json`；新增两个「不同实测开销产生完全相同输出」的确定性回归测试。

### Changed

- CI 新增 Real HTTP load gate；`Makefile` 新增 `httpload` 目标并纳入 `gates`。
- 展示页新增「真实 HTTP 服务压测」区块（数据来自快照）、复现入口与 ADR 0008 补充说明；中英文 README、路线图、任务手册、评测方法论、排错手册、面试讲稿、贡献指南按 v0.8.0 同步。

### 边界

单机 loopback、无 TLS / 反向代理 / 真实模型延迟；同一台机器两次运行 4 worker 的吞吐能差近一倍，这些数字只是量级参考，不是生产 QPS。主评测集仍 80/80、0.9788 / 0.3014，v1–v6 基线不受影响。

## [0.7.0] - 2026-11-12

**把失败案例库从展示材料变成新用例的输入**：80/80 全绿也可能只是对着 80 道题调规则。新增一层独立的盲测挑战集，检验判定链在没见过的组合上是否一致。

### Added

- **盲测挑战集挖掘器**（`evals/case_miner.py`）：从 517 条失败记录按「问题码 × 城市」聚类，结合知识库构造确定性网格（5 城 × 5 风格 × 9 种压力配置 × 4 季轮换日期 = 225 个候选）。每个候选跑 `good` / `naive` 双 provider，需同时满足「不与主集重复、good 可执行且 ≥ 0.70、naive 被拒、得分差 ≥ 0.30」才纳入。**挖掘器不读取任何用例的期望输出**。
- `evals/challenge/cases.jsonl`（200 个盲测 brief）、`evals/case_mining.json`、[docs/case-mining.md](docs/case-mining.md) 三份产物由代码生成、逐字节可复现；`--check` 作为 CI 第 12 道门禁，会重新挖掘并比对产物。
- **负向对照**：25 个「同一地点既指定又排除」的不可满足 brief 被生成前可行性判定挡下；门禁要求它们**继续被筛掉**，否则判定为前置检查被削弱的回归，而不是覆盖率提升。
- [ADR 0010](docs/adr/0010-holdout-challenge-set.md)：为什么挑战集独立存放、不并入主集、不污染 v1–v6 基线。
- 8 个新测试（`tests/test_case_miner.py`）：网格确定性、指纹规范化、与主集不重复、产物一致性、负向对照与覆盖不变量、以及「手工篡改产物 / 负向对照消失 / 挑战集缩水」都会被门禁抓住。

### Changed

- CI 新增 Case-mining gate（12 道门禁）；`Makefile` 新增 `mine` 目标并纳入 `gates`。
- 展示页新增「盲测挑战集」区块（数据来自 `case_mining.json`），hero 说明、复现入口与 ADR 列表同步；中英文 README、路线图、任务手册、评测方法论、排错手册、面试讲稿、贡献指南按 v0.7.0 同步。

### Fixed

- **扩展性门禁会在小 workload 下误报失败**：CI 用 `--requests 300 --workers 1 2`，此时进程池 IPC / 调度开销会盖过并行收益，实测 2 进程吞吐（约 3599/s）低于 2 线程（约 4256/s），旧门禁把「进程不比线程慢」当硬断言，`SCALING GATE FAILED` 会让 CI 偶发红。速度是**机器与 workload 相关**的结果，不该进门禁（ADR 0008）。改为只检查机器无关的契约：0 失败、每个 arm 有耗时、多 worker 进程 arm 确实持有 ≥2 个真实 OS 进程 PID；速度对比降级为控制台与报告里的 informational note。连续三次 `--check` 退出码均为 0。`tests/test_scaling.py` 重写为验证这套新契约（含「进程慢 10 倍不应让门禁失败」的回归测试）。

### Measured

- 225 候选 → **200 纳入 / 25 筛掉**；城市 × 风格覆盖 **19/25 → 25/25**，主集未覆盖的新格子 6 个。
- 盲测集 good 硬约束通过率 **100%**、均分 0.9881；naive **0%**、均分 0.19；最小得分差 **0.7687**。
- `no_outdoor` 与 `micro_budget` 共 50 个候选全部通过，复核了室内备选硬编码与 C24 紧预算超支两处旧修复。
- 测试 **191 个**、覆盖率 **89%**；主评测集仍 80/80、0.9788 / 0.3014，历史基线不受影响。

## [0.6.0] - 2026-11-12

**给门禁本身做回归测试**：80/80 全绿不等于检查有效。注入 8 处受控故障后发现旧门禁漏掉 4 处「悄悄删掉一条校验规则」的改动，补齐对照组规则覆盖后 8/8 全部拦截。

### Added

- **变异测试实验室**（`evals/mutation_lab.py`）：3 处改生成端、5 处改校验端，全部在进程内注入并回滚，**不修改任何源文件**；跑同一批 80 用例、调用与 CI 完全相同的门禁函数。产物 [docs/mutation-report.md](docs/mutation-report.md) 与 `evals/mutation.json` 由代码生成、逐字节可复现，`--check` 作为 CI 第 11 道门禁。
- **对照组规则覆盖门禁**（`evals/scorers.py`）：`naive` 必须继续命中每条已知缺陷（`TRANSFER_BUFFER ≥ 100`、`UNKNOWN_LOCATION ≥ 90`、`BUDGET_EXCEEDED ≥ 30`、`TIME_OVERRUN ≥ 30` 及三条软规则各 ≥ 30），另加差值下限 0.45、对照组均分上限 0.45、硬约束通过率上限 0.30。门禁实现收敛到唯一函数 `eval_gate_problems`，实验室与 `run_eval` 共用。
- [ADR 0009](docs/adr/0009-gate-sensitivity.md)：记录盲点、决策、阈值取舍与备选方案。
- [docs/eval-report.md](docs/eval-report.md) 新增「对照组规则覆盖」与一处由代码生成的「改动 → 分数下降 → 门禁拦截 → 回滚复原」实例（good 均分 0.9788 → 0.1375 → 0.9788），满足阶段 4 的该项验收，无需 API key。
- 14 个新测试（`tests/test_mutation_lab.py` 13 个 + `tests/test_simulated_provider.py` 1 个），含「实验室与 CI 使用同一个门禁函数」的对象身份断言，以及成本报告延迟取整的确定性回归测试。

### Changed

- 展示页新增「门禁有效性」区块，且把原先手写的分组说明、对照组说明改为脚本生成（修掉页面仍写着「60 个用例 / 109 个单元测试」的漂移）。
- 中英文 README、路线图、任务手册、面试讲稿按 v0.6.0 同步；英文 README 补上此前遗漏的五座城市与 80 用例口径。
- `Makefile` 新增 `mutation` 目标并纳入 `gates`；CI 新增 Mutation gate。

### Measured

- 变异体 **8/8 被拦截、0 存活**；旧门禁只能拦住 **4/8**（漏掉的 4 个全是校验端削弱，good 均分保持 0.9788 不变）。
- 8 次回滚后与基线逐字段一致；测试 **180 个**、覆盖率 **89%**、评测 80/80、CI 11 道门禁 × 4 个 Python 版本。

## [0.5.0] - 2026-11-12

补上「不用装环境也能看输出」和「瓶颈到底在哪」两块：可复现的真实输出示例，以及线程 vs 进程的扩展性对比。

### Added

- **真实输出示例**（`evals/examples.py` + [docs/examples.md](docs/examples.md)）：四份实际运行结果——正常需求、带排除约束、12 小时上限、**不可满足预算**。每份含时间轴、花费、备选方案、假设、校验结论与运行步骤顺序。
- **线程 vs 进程扩展性实验**（`evals/scaling.py` + [docs/scaling.md](docs/scaling.md)）：同一 workload 下线程最好 **1.04×**、进程最好 **1.72×**（18 逻辑核），把「瓶颈是 GIL」从常识变成可复现数据。
- CI 增加两道门禁：示例文件必须与代码生成结果一致（`evals.examples --check`）、扩展性实验不得出现失败或「进程比线程慢」的异常。

### Changed

- 示例与扩展性报告只输出**确定性字段**：`plan_id` / `run_id` / 耗时一律不写入，否则文件在不同机器上无法逐字节一致，CI 比对只会变成噪声。

### Measured

- 线程：1 / 2 / 4 并行分别约 1.00× / 1.04× / 1.03×（GIL 上限）
- 进程：1 进程约 0.94×（启动与调度开销）、2 进程约 1.66×、4 进程约 1.72×
- 测试 **166 个**，覆盖率 **89%**

## [0.4.0] - 2026-11-12

第五座城市南京、评测扩到 80 用例，并补上并发这一层：**并发下不丢写、不报错**，顺手修掉两个只有压测才会暴露的存储缺陷。

### Added

- **第五座城市南京**（8 个地点：中华门瓮城、老门东、秦淮河夫子庙、中山陵、南京博物院、玄武湖、鸡鸣寺、室内影棚）；评测集扩到 **80 个用例**，`normal` 30、`conflict` 23、`edge` 16、`infeasible` 11，新增 `v6-naive.json` 基线。
- **并发负载测试**（`evals/loadtest.py` + [docs/loadtest.md](docs/loadtest.md)）：并发 1 / 4 / 8 各 100 次请求，断言「全部有应答、全部落到数据库」；同时给出吞吐、p50/p95 与饱和点。CI 增加第 8 道门禁。
- API 端点改为 `await run_in_threadpool(...)`：同步流水线不再阻塞事件循环。离线 provider 下只是「能同时处理第二个请求」，接真实模型后这是服务能否并发的分水岭。

### Fixed

- **并发写库丢数据 / 422**：API 每请求新建 `RunStore`，多个线程同时执行 `CREATE TABLE` + `PRAGMA journal_mode=WAL` 会互相抢锁，偶发 `attempt to write a readonly database`（表现为 HTTP 422）。改为进程级初始化锁 + 写锁 + 对 `locked` / `readonly` 的有限重试。
- **SQLite 写放大**：`journal_mode=WAL` 之后，并发 8 的 p95 从约 200ms 降到约 27ms，吞吐从约 100/s 提到约 310/s（同一台机器、同一批 brief）。
- 分位计算统一到 `evals.benchmark.percentile`，避免负载测试与基准各写一份、边界行为不一致。

### Measured

- 评测：80/80 通过，硬约束 100%，均分 **0.9788**；对照组 **0.3014**（13.8%），差值 **0.6774**。
- 修复实验：69 个可满足 brief，`repairable` 一次修复 **69/69**；`naive` 与 `regressing` 各 69 次候选全部被拒绝。
- 失败案例库：**517 条**（转场 198 / 编造地点 186 / 预算 69 / 超时 64）。
- 并发：600 次请求 0 错误、600/600 写入；p95 从约 200ms（修复前）降到约 27ms。
- 测试 **155 个**，覆盖率 **89%**。

## [0.3.0] - 2026-11-12

从「三城市 60 用例」扩展到「四城市 70 用例」，并把质量工程补上两层：**任意输入不崩**的模糊测试与**可门禁的性能基准**。同时让 malformed brief 不再可能触发 500。

### Added

- **第四座城市苏州**（8 个地点：平江路、拙政园、苏州博物馆、虎丘、山塘街、沧浪亭、金鸡湖、室内影棚）；评测集扩到 **70 个用例**，`normal` 26、`conflict` 20、`edge` 14、`infeasible` 10，新增 `v5-naive.json` 基线。
- **输入模糊测试**（`evals/fuzz.py` + [docs/fuzz-report.md](docs/fuzz-report.md)）：从真实 brief 变异出 1000 个垃圾输入（错类型、`NaN`、`1e400`、emoji 洪水、NULL 字段、不存在的日期、注入片段），断言 0 未捕获异常、0 不可序列化结果。每个用例带独立种子，`--only-seed` 可单条复现。
- **性能基准**（`evals/benchmark.py` + [docs/benchmark.md](docs/benchmark.md)）：延迟分位、吞吐、Python 堆峰值与分步耗时；延迟与内存**分两轮测量**，避免 `tracemalloc` 的开销污染延迟数字。实测 p50 ≈0.2ms / p95 ≈0.6ms、单核数千次/秒、堆峰值 0.02MB（数字随机器变化，门禁看绝对上限）。
- **`status="error"` 语义**：provider 抛错或输出不可解析时返回结构化错误结果（`PIPELINE_ERROR`），不再让异常穿透成 500。
- **`DATE_INVALID` 预检原因**：非法日期（如 `2026-02-30`）在生成前结构化拒绝，而不是让 `light_window` 抛 `KnowledgeError`。
- 新增两个 CI 门禁：`evals.fuzz --check`、`evals.benchmark --check`；展示页新增性能块，并把两处手写计数改为脚本生成。
- `Makefile` 新增 `fuzz`、`bench`、`gates`、`showcase` 目标，`report` 的基线修正为 `v5`。
- [ADR 0008](docs/adr/0008-fuzz-and-benchmark-gates.md)：记录两项门禁的决策、测量偏差（`tracemalloc` 污染延迟）与备选方案（Hypothesis、pytest-benchmark）被拒的理由。

### Fixed

- `PlanRequest.normalized()` 改为**全域函数**：`hours="abc"`、`budget_cny=None`、`styles` 传字符串或字典、`NaN`/`inf` 都不再抛异常，统一退化为文档化默认值并夹紧区间。此前这些输入会让 API 返回 500。
- `styles="tang"` 曾被按字符拆成 `["t","a","n","g"]`，现按单值处理。

### Measured

- 评测：70/70 通过，硬约束 100%，均分 **0.9778**；对照组 **0.3057**（14.3%），差值 **0.6721**。
- 修复实验：60 个可满足 brief，`repairable` 一次修复 **60/60**；`naive` 与 `regressing` 各 60 次候选全部被拒绝。
- 失败案例库：**437 条**（转场 171 / 编造地点 150 / 预算 60 / 超时 56）。
- 模糊测试：1000 次迭代，0 崩溃、0 不可序列化。
- 性能：p50 ≈0.2ms、p95 ≈0.6ms、p99 ≈2ms、单核数千次/秒、堆峰值 0.02MB。
- 测试 **148 个**，覆盖率 **89%**。

## [0.2.0] - 2026-11-12

从「单城市 12 用例」扩展到「三城市 60 用例」，并补齐生成前判定、修复实验、失败案例库与运行统计。核心主张不变：**质量必须可测量**。

### Added

- 生成前的**可行性判定**（`domain/feasibility.py`）：预算低于最低可执行成本、时长过短、未知城市、约束互相矛盾时，返回 `status=infeasible` + 原因/缺口/建议/理论下限，不调用 provider、不生成硬凑方案。CLI 退出码 3，API 与 Web 同步展示，见 ADR 0007。
- 评测集扩展到 **60 个用例**，覆盖西安 / 洛阳 / 杭州三座城市：`normal` 22、`conflict` 17、`edge` 12、`infeasible` 9；新增 `v4-naive.json` 基线（`v1`–`v3` 保留为历史快照）。
- **修复循环实验**（`evals/repair_lab.py` + [docs/repair-experiment.md](docs/repair-experiment.md)）：三种刻意构造的 provider 在 51 个可满足 brief 上对照；trace 新增 `accepted` 字段，让「单调保护拦截了几次」成为可测量数据。实测一次修复成功率 51/51，51 次更差修复全部被拒绝。
- **结构化失败案例库**（`evals/collect_failures.py` + `evals/failures/cases.jsonl` + [docs/failure-gallery.md](docs/failure-gallery.md)）：357 条记录、4 类硬约束违规，每条含输入 brief、输出片段、期望规则与修复建议。
- **可观测聚合面板**（`src/shotplan/observability.py` + [docs/observability.md](docs/observability.md)）：按 provider / prompt 版本聚合通过率、均分、P50/P95 延迟、成本、修复轮次与不可行数量；接入 `cli stats`、`/api/stats` 与 Web「运行统计」标签页。
- **成本/延迟模拟**（`evals/simulated_provider.py` + `evals/cost_latency.py` + [docs/cost-latency-simulation.md](docs/cost-latency-simulation.md)）：用可配置价格卡验证 token → trace → 报告 → 预算换算链路，并明确标注为模拟值。
- **CI 门禁**：修复实验（一次修复成功率、更差修复不得被接受）、失败案例库（数量、类别、字段完整性）、**覆盖率 ≥ 85%**、**展示页与评测产物一致**。
- **GitHub Pages 展示页**（`docs/index.html`）：指标、分组结果、修复实验、失败案例与 ADR 入口一页呈现；数据块由 `scripts/build_showcase.py` 从评测产物生成，杜绝手写数字漂移。
- prompt v1/v2 版本表、`get_system_prompt()`、真实模型 provider 的版本参数与 `--prompt-version` 评测参数，为接真实模型后的对比实验备好框架。
- 英文 [README.en.md](README.en.md) 与中文版互链；新增 `SECURITY.md` 与 Dependabot 配置。
- CLI 契约测试（`tests/test_cli.py`）覆盖退出码、JSON 输出、持久化、统计与命令转发。

### Fixed

- 紧预算用例 C24 暴露的实现缺陷：规则 planner 会挑收费景点导致 `BUDGET_EXCEEDED`。现在按剩余预算裁剪地点组合，并让 `best_style_for()` 先为转场预留 50 元、必要时自动降级妆造档位。修的是实现，不是调低用例期望。
- 多城市用例暴露的两处硬编码：妆造地点写死为西安 `indoor_studio`、客流备选写死为 `qinglong_temple`。已改为按当前城市的 `indoor_fallback` 与低客流地点动态选择。
- 评分聚合改用 `math.fsum` 并统一规整到 6 位小数：修复 Python 3.10/3.11 下满分方案得到 `1.0000000000000002`、导致 CI 两个矩阵失败的问题（3.12 起 `sum()` 使用补偿求和）。新增固定精度契约测试与 ADR 0006。
- trace 现在采用 provider 上报的 `duration_ms`，真实网络耗时与模拟延迟都能进入 P50/P95 统计。

### Changed

- `docs/portfolio/USER_TASKS.md` 改为按当前状态记录已完成/待办事项；GitHub 建仓、推送、描述与 topics 已由本机凭证完成。
- 新增 `scripts/push_via_api.py`：代理只放行 `api.github.com` 时，通过 Git Data API 完成等价推送（逐对象校验 SHA、密钥不落盘），并在排错手册记录故障处置。
- GitHub Pages 源设为 `main` 的 `/docs` 目录，附 `.nojekyll`。

### Measured

- 单元测试：116 passed，覆盖率 88%（`pytest --cov=src/shotplan`）。
- 评测：规则 Agent 硬约束通过率 100%、均分 0.9762、60/60 用例通过；对照基线 15.0% / 0.3115；差值 0.6647。
- 修复实验：51/51 一次修复成功；51 次更差修复全部拦截。
- CI：Python 3.10 / 3.11 / 3.12 / 3.13 四版本矩阵全绿。

### Known limitations

- 知识库为静态小样本（三城 24 个地点），无实时客流与票价。
- 离线 `good` provider 是当前规则下的上限参考，不是真实模型效果；评测体系的压力测试待接入真实模型。
- 成本/延迟报告是模拟值，不是厂商实测。
- 无用户鉴权、无并发写入、无真实预约/支付。

## [0.1.0] - 2026-11-12

阶段 1–3 完成：一个可评测、带硬约束校验的旅拍方案 Agent。

### Added

- 核心域：`PlanRequest` / `Plan` / `TimeSlot` / `ValidationReport` / `RunTrace`，仅用标准库实现。
- 12 条硬/软约束及其单一定义源 `domain/constraints.py`。
- 不依赖模型的确定性校验器，覆盖时间、流程、转场、预算、地点溯源与质量信号。
- 人工整理的知识库（西安 8 个地点 / 5 种妆造风格）与精确检索工具。
- 离线 provider（`good` 规则实现 + `naive` 对照组）、OpenAI 兼容 provider（urllib + 重试 + 降级）。
- 编排层：检索 → 生成 → 解析 → 校验 → 修复，修复结果单调不降保护。
- 运行追踪（耗时 / token / 估算成本）与 SQLite 运行历史。
- CLI（`plan` / `rules` / `config` / `history` / `eval` / `serve`）、可选 FastAPI 层、无构建原生单页界面。
- 评测体系：12 个用例（normal / conflict / edge）、对照基线、自动 Markdown 报告、回归阈值与「测量失效」检查。
- 76 个单元测试，逐条覆盖规则与边界。
- 文档：README、架构说明、问题与指标、评测方法论、5 份 ADR、路线图、排错手册、面试笔记、用户操作手册。
- CI 工作流、Docker / compose、pre-commit 配置、Issue 模板、一键初始化脚本。

### Measured

- 单元测试：76 passed。
- 评测：规则 Agent 硬约束通过率 100%、均分 0.9734、12/12 用例通过；对照基线 0% / 0.1900；差值 0.7834。
- 核心链路在零 API key、零第三方依赖环境下完整可运行。

### Known limitations

- 知识库为静态小样本，无实时客流与票价。
- 离线 `good` provider 是当前规则下的上限参考，评测体系的压力测试待阶段 4 接入真实模型。
- 未显式处理预算低于最低妆造成本的不可满足需求。
- 无用户鉴权、无并发写入、无真实预约/支付。
