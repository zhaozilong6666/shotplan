# 报告治理审计

由 `scripts/audit_reports.py` 生成，勿手改。

先分两类再问问题：脚本生成的报告问「同一实验有几个实例」，人写的报告问「是否互相重复」。混在一起比较会把模板相同误判成内容重复。

## 摘要

| 指标 | 值 |
| --- | --- |
| 扫描报告 | 103 份（553 KB） |
| 脚本生成 | 61 份，来自 36 个生成器 |
| 人写报告 | 42 份 |
| 人写报告冗余对（≥ 0.55） | 3 对 |
| 同名前缀家族（≥ 3 份） | 5 个 |
| 失效引用 | 0 处 |
| 孤儿报告（无入链） | 9 份 |
| 人写报告平均边际信息量 | 0.745 |

## 1. 同源实例：报告为什么会堆积

`evals.soak` 一个脚本产出 **16 份**，占全部脚本生成报告的 26%。

- `soak-45m.md`
- `soak-5h.md`
- `soak-batch-120.md`
- `soak-batch-60.md`
- `soak-batch-nokeepalive-30.md`
- `soak-high-120-1w.md`
- `soak-high-120.md`
- `soak-high-60-2w.md`
- `soak-high-60.md`
- `soak-index.md`
- `soak-off-4w-20m.md`
- `soak-restart-30.md`
- `soak-sync-30.md`
- `soak-writer-30.md`
- `soak-writer-nk-20m.md`
- `soak.md`

`evals.real_model_lab` 一个脚本产出 **9 份**，占全部脚本生成报告的 15%。

- `real-model-full-2026-11-16.md`
- `real-model-repeat3.md`
- `real-model-replication.md`
- `real-model.md`
- `redteam-soft-v3.md`
- `redteam-soft.md`
- `redteam.md`
- `router-holdout-off.md`
- `router-holdout-on.md`

`scripts/kb.py` 一个脚本产出 **3 份**，占全部脚本生成报告的 5%。

- `HANDOFF.md`
- `kb-health.md`
- `next.md`

这些文件的高相似度是模板的必然结果，**不是冗余**——它们记录了不同的数字。真正的问题是读者无法从文件名判断哪份是当前结论。

**命名即契约**：`X.md` 是默认输出路径，每次覆盖，代表当前结论；`X-<日期>.md` 是一次快照，代表当时的状态。同源实例超过 2 份时，新增的一份要能回答「这次与上次的假设有什么不同」，否则它只是又一个需要归档的文件。

其余 33 个生成器各产出 1 份，属正常。

## 2. 人写报告的冗余对：每一行是一次决定

不聚类。单链接聚类会把 A~B、B~C 连成 A~C，制造出根本不相似的「一簇」；每一对独立呈现，读者可以逐行否决。

| 报告 A | 报告 B | 相似度 | 建议 |
| --- | --- | ---: | --- |
| `real-model-light-fix-2026-11-16.md` | `real-model-smoke-2026-11-16.md` | 0.684 | 部分重叠：只核对数字是否互相矛盾 |
| `real-model-baseline-2026-11-16.md` | `real-model-light-fix-2026-11-16.md` | 0.667 | 部分重叠：只核对数字是否互相矛盾 |
| `real-model-baseline-2026-11-16.md` | `real-model-smoke-2026-11-16.md` | 0.591 | 部分重叠：只核对数字是否互相矛盾 |

**不要直接删**：这些文件记录的是「当时为什么那么判断」。保留边际信息量最高的一份作为当前结论，其余在首行加一行`> 已被 <新文件> 取代（<日期>）；本文保留为历史记录`。

## 3. 同主题家族：命名层面的堆积

| 前缀 | 份数 | 文件 |
| --- | ---: | --- |
| soak | 18 | `soak-45m.md`、`soak-5h.md`、`soak-ab.md`、`soak-batch-120.md`、`soak-batch-60.md`、`soak-batch-nokeepalive-30.md`、`soak-high-120-1w.md`、`soak-high-120.md`、`soak-high-60-2w.md`、`soak-high-60.md`、`soak-index.md`、`soak-off-4w-20m.md`、`soak-partial-4h24.md`、`soak-restart-30.md`、`soak-sync-30.md`、`soak-writer-30.md`、`soak-writer-nk-20m.md`、`soak.md` |
| real | 8 | `real-model-baseline-2026-11-16.md`、`real-model-comparison.md`、`real-model-full-2026-11-16.md`、`real-model-light-fix-2026-11-16.md`、`real-model-repeat3.md`、`real-model-replication.md`、`real-model-smoke-2026-11-16.md`、`real-model.md` |
| redteam | 4 | `redteam-analysis.md`、`redteam-soft-v3.md`、`redteam-soft.md`、`redteam.md` |
| router | 3 | `router-holdout-off.md`、`router-holdout-on.md`、`router-validation.md` |
| transfer | 3 | `transfer-budget.md`、`transfer-by-city.md`、`transfer-model.md` |

家族大不一定是坏事（脚本生成的就该成组），但人写的部分若也在膨胀，通常说明同一问题被反复回答。

## 4. 边际信息量排序：人写报告里谁值得先读

`1 - 最近邻相似度`，只在 42 份人写报告内比较（< 120 token 的 0 份只查引用）。接近 1 表示这份讲的东西别处没有；接近 0 表示它是别人的复述。

| 报告 | 边际信息量 | 最近邻 | 相似度 |
| --- | ---: | --- | ---: |
| `multi-turn-attribution.md` | 0.898 | `ROADMAP.md` | 0.102 |
| `ingest.md` | 0.893 | `knowledge-contract.md` | 0.107 |
| `generality.md` | 0.877 | `problem-and-metrics.md` | 0.123 |
| `repair-experiment.md` | 0.871 | `failure-taxonomy.md` | 0.129 |
| `cost-latency-simulation.md` | 0.868 | `real-model-light-fix-2026-11-16.md` | 0.132 |
| `intelligence-roadmap.md` | 0.866 | `architecture.md` | 0.134 |
| `city-coverage.md` | 0.862 | `evaluation.md` | 0.138 |
| `knowledge-contract.md` | 0.856 | `problem-and-metrics.md` | 0.144 |
| `budget-vs-concurrency.md` | 0.834 | `real-model-baseline-2026-11-16.md` | 0.166 |
| `soak-partial-4h24.md` | 0.829 | `lessons.md` | 0.171 |

末位（信息量最低）：

- `ROADMAP.md` — 0.662，最近邻 `evaluation.md`（0.338）
- `evaluation.md` — 0.662，最近邻 `ROADMAP.md`（0.338）
- `real-model-baseline-2026-11-16.md` — 0.333，最近邻 `real-model-light-fix-2026-11-16.md`（0.667）
- `real-model-light-fix-2026-11-16.md` — 0.316，最近邻 `real-model-smoke-2026-11-16.md`（0.684）
- `real-model-smoke-2026-11-16.md` — 0.316，最近邻 `real-model-light-fix-2026-11-16.md`（0.684）

## 5. 失效引用：读者点开就是 404

无。

## 6. 孤儿报告：没人链接，等于没人读

**人写但没有入链（1 份，需要处理）**

- `thresholds.md`

处理方式二选一：在 `ROADMAP` 或相关报告里加入**可点击链接**（反引号里的路径不算，读者点不到）；或承认它不是给读者看的，移出 `docs/`。

**脚本生成且无入链（8 份，属正常）**

- `gate-stability.md`
- `probe-set.md`
- `redteam-soft-v3.md`
- `redteam-soft.md`
- `redteam.md`
- `router-holdout-off.md`
- `router-holdout-on.md`
- `soak-writer-30.md`

同一实验的历史实例不需要人工索引，它们的问题在第 1 节的实例数量，不在链接。

## 口径与已知偏差

- **token 化**：中文按字符 bigram，英文按 3 字母以上的整词。不用分词器是为了零依赖，代价是「拍摄/摄影」这类近义词不会被归并，相似度整体偏低（保守，不会误报冗余）。
- **剥离代码块**：比较的是论述而非示例代码。两份文档共享一段代码不算重复。
- **生成器识别**：读前 8 行找 `python -m X --write` 或「由 `X` 生成」。识别失败会把脚本报告误当人写报告，因此识别到的实例一律不参与相似度比较。
- **路径解析**：只检查 `docs/`、`src/`、`scripts/`、`tests/`、`evals/` 开头的显式路径。裸文件名（`rooms.json`）多为假设或叙述，`evals/out/` 是被清理的产物，含 `..` 或 `*` 的写法是简写——这三类报出来只会训练读者忽略本节。可点击的 Markdown 链接由 `tests/test_docs_links.py` 负责，本节查的是正文提到的路径。
- **入链统计**：`README.md` 与 `CHANGELOG.md` 也算链接来源，否则它们提到的报告会全部被误判为孤儿。
- **不纳入 ADR**（`docs/adr/`）：决策记录刻意共用模板，相似度不构成冗余信号。
- **交叉引用会推高相似度**：为了让读者不困惑而补的导航句（「同日还有两份更小的采样」）本身增加了共同词汇——实测中一份报告补完导航后，与另外两份的相似度反而从 0.60 升到 0.64。所以 0.55–0.70 更像「同一批实验的不同切面」，而不是「两篇在讲同一件事」：先核对数字，再决定是否合并。
- **本文件自身不参与**审计，否则每次运行都会把自己报成 100% 重复。
- **提交动作会改变语料**：语料取的是 git 索引，`git add` 一份新文档就会让统计数 +1。新增文档后要**先 add、再 `--write`**，否则本地算的是旧份数，CI 算的是新份数——这个陷阱已经踩过两次（一次是未跟踪的草稿被算进来，一次是刚 add 的新文档没重新生成）。

_语料指纹：`e9fd0c8c47a08a30`（内容变化时此值必变）_
