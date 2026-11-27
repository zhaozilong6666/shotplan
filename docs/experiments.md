# 实验台账

由 `evals/experiment_log.py` 生成，数据源 `evals/experiments.jsonl`。
每条实验在动手**之前**写下预期，做完填实测与判定。先写预测是为了让事后无法改口——
叙事型 changelog 的通病就是每条改动都「效果显著」。

## 摘要

| 指标 | 值 |
| --- | --- |
| 记录实验 | 33 条 |
| 确认 | 20 条（61%） |
| 推翻 | 7 条（21%） |
| 部分成立 | 6 条 |
| 待结 | 0 条 |
| 台账内累计花费 | ¥12.09 |

## 校准：预测有多准

推翻率 **21%**（7/33）。这个数字偏低通常不是因为判断准，而是因为大部分实验只测「改动有没有效果」，没测「方向对不对」——前者容易成立，后者才可能被推翻。

如果台账里长期没有方向性的否决，说明它在记录执行，不在检验判断。下一次应该写一条**自己不确定、且可能被证伪**的假设。

被推翻的假设（最值得重读的几条）：

- **E22** 把 feasibility 里的 MIN_TRANSFER_COST_CNY 改成 0（最低转场成本失效）后，现有门禁能不能拦住？ — 原以为 能：最低预算被低估，原本判 infeasible 的 brief 进入排期，最终撞 BUDGET_EXCEEDED，good hard_pass 下降
- **E20** 主集里的可行 brief 给的时间余量有多大？如果余量普遍超过一小时，参数扰动被吸收就说得通了 — 原以为 69 条可行 brief 的时间余量中位数不少于 90 分钟
- **E19** 哪个规则常量的扰动会真正改变评测结果？转场是不是最不敏感的那个？ — 原以为 MIN_SHOOT_BLOCK_MINUTES 上调 50% 会让至少 10 条 brief 从通过变成失败，而 MIN_TRANSFER_MINUTES 同样扰动的影响不超过 2 条
- **E18** 把转场改成 rms 后，半径跨度大的城市（洛阳、杭州）少排地点的条数是否明显多于西安？ — 原以为 洛阳与杭州合计少排地点的条数不少于 6 条，且高于西安
- **E17** 把转场从固定 20 分钟改成 sqrt(a²+b²)（均方根估计），会让多少条 brief 少排一个地点？ — 原以为 主集 80 条里有 10 到 25 条会从两个地点降到一地点
- **E09** 这套规则里有多少能迁移出旅拍领域？ — 原以为 我原估：9 条通用、6 条需改
- **E03** planner-v3 prompt 是否优于 v2？ — 原以为 均分提升

## 按方向的产出

| 方向 | 实验数 | 确认 | 推翻 | 部分 | 读法 |
| --- | ---: | ---: | ---: | ---: | --- |
| 约束规则 | 10 | 4 | 4 | 2 | 多数成立，但有明确反例 |
| 运行参数 | 6 | 5 | 0 | 1 | 部分成立：1 条未完全达预期 |
| 测量方法 | 5 | 2 | 2 | 1 | 多数成立，但有明确反例 |
| 多轮交互 | 4 | 3 | 0 | 1 | 部分成立：1 条未完全达预期 |
| model | 2 | 2 | 0 | 0 | 方向成立：全部达预期 |
| 成本与延迟 | 1 | 1 | 0 | 0 | 单条，不下结论 |
| 知识库数据 | 1 | 0 | 0 | 1 | 单条，不下结论 |
| 文档治理 | 1 | 1 | 0 | 0 | 单条，不下结论 |
| 光线排程 | 1 | 1 | 0 | 0 | 单条，不下结论 |
| 提示词 | 1 | 0 | 1 | 0 | 单条，不下结论 |
| 模型通道 | 1 | 1 | 0 | 0 | 单条，不下结论 |

## 全部实验（按日期倒序）

| ID | 日期 | 方向 | 问题 | 预期 | 实测（指标：起 → 止） | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| E33 | 2026-11-27 | model | 在 30 例 iterate 样本上，deepseek-v4.1-flash-1017 低 reasoning 能否保持硬约束通过，成本是否可控？ | 硬通过率不低于 0.90，总成本低于 ¥3，P95 不超过 180s 预算 | 硬通过率、均分、首次输出可用率、成本、P50/P95：— → 1.0 | 确认 |
| E32 | 2026-11-27 | model | wujie-api 的 deepseek-v4.1-flash-1017，在低 reasoning、2000 输出 token 下，8 例 smoke 能否保持硬约束通过且成本可控？ | 硬通过率不低于 0.85，成本低于 ¥1，单次 P95 不超过 180s 预算 | 硬通过率、均分、成本、P50/P95：— → 1.0 | 确认 |
| E31 | 2026-11-27 | 运行参数 | 4 个独立端口 + 单一边缘代理，在 batch 下能否保持 0 错误、无 30s 尾延迟？ | 如果共享 accept socket 是根因，独立后端 + 单代理应 0 错误、最大延迟远低于 30s | 请求数、错误数、最大延迟、P99.9：— → 0 | 确认 |
| E30 | 2026-11-27 | 运行参数 | 4 worker 的 30s 尾延迟，根因是多进程共享 SQLite 写竞争，还是 uvicorn 共享 accept socket？ | 若根因是共享 accept socket，独立端口应 0 错误、无 30s 最大延迟；若仍有错误，则 SQLite 仍有独立作用 | 错误数、最大延迟、P99.9：— → 0 | 确认 |
| E29 | 2026-11-23 | 运行参数 | 4 worker 的 20 个 ReadTimeout 是时间漂移，还是多进程共享 SQLite 的长尾？ | 4 worker 的异常与超 5s 次数多于 1 worker；1 worker 出现 0 次超 5s | 4 worker 的慢请求（>5s，含超时）总数与最大延迟：— → 31 | 确认 |
| E28 | 2026-11-22 | 测量方法 | batch 模式承诺「返回前已 commit」；进程被硬杀后，已返回的请求是否一行不少？ | 3 轮都是 20/20 行，0 丢写；与 async 的硬杀丢行形成对照 | 每轮硬杀后库里的行数：— → 0 | 确认 |
| E27 | 2026-11-21 | 测量方法 | 把并发请求的 SQLite 提交合并成一次、但每个请求仍等到 commit 再返回（batch 模式），能比 sync 快多少？ | batch 吞吐是 sync 的 2.5–3.5×，P95 降到一半以下，0 丢写；单请求仍等到 commit | 三组吞吐、P95、丢写：— → 4 | 确认 |
| E26 | 2026-11-21 | 测量方法 | 单 worker 30–40/s 的成本分布在哪：pipeline 阶段、HTTP/JSON，还是同步 SQLite 提交？ | 同步 SQLite 提交占大头（sync 比 off 慢 2–3×）；HTTP/JSON 开销 < 10ms；preflight 是最大的 pipeline 阶段 | 三种路径的均值耗时与各阶段均值：— → 20 | 部分 |
| E25 | 2026-11-20 | 运行参数 | 给 RunStore 初始化加重试后，4 worker 冷启动连续 20 轮还会不会出现 503 或丢写？ | 0 启动失败、0 个 503、0 丢写；启动中位数约 3 秒 | 20 轮的启动失败数、503 数、丢写数：— → 0 | 确认 |
| E24 | 2026-11-20 | 运行参数 | 当前提交下，1/2/4 worker 同写同一 SQLite 库，是否仍 0 错误、0 丢写？4 worker 相对 1 worker 是否仍回落？ | 0 错误、0 丢写；4 worker 吞吐仍低于 1 worker（≤0.8×），最好一档仍是 2 worker | 错误数、丢写数、4 worker 相对 1 worker 的吞吐倍数：— → 0 | 部分 |
| E23 | 2026-11-20 | 约束规则 | 把 styling 比例从 int 截断改成四舍五入，3 小时档从 62 分钟变 63 分钟，会影响主集评分吗？ | 不会：3 小时档只多 1 分钟妆造，主集 hard_pass 仍 80/80，avg_score 变化不超过 0.005 | 80 条主集的 hard_pass 与 avg_score：— → 1 | 确认 |
| E22 | 2026-11-19 | 约束规则 | 把 feasibility 里的 MIN_TRANSFER_COST_CNY 改成 0（最低转场成本失效）后，现有门禁能不能拦住？ | 能：最低预算被低估，原本判 infeasible 的 brief 进入排期，最终撞 BUDGET_EXCEEDED，good hard_pass 下降 | mutation_lab caught（0/1）与 good hard_pass_rate：— → 0 | **推翻** |
| E21 | 2026-11-19 | 约束规则 | 取消 MIN_FEASIBLE_HOURS 的 1.5 小时下限后，现有变异门禁能不能拦住？ | 能：原本判 infeasible 的 brief 会进入排期并撞硬规则，good hard_pass 下降，mutation_lab 判 caught | mutation_lab caught（0/1）与 good hard_pass_rate：— → 1 | 确认 |
| E20 | 2026-11-18 | 测量方法 | 主集里的可行 brief 给的时间余量有多大？如果余量普遍超过一小时，参数扰动被吸收就说得通了 | 69 条可行 brief 的时间余量中位数不少于 90 分钟 | 可行 brief 的时间余量中位数（分钟）：— → 0 | **推翻** |
| E19 | 2026-11-18 | 约束规则 | 哪个规则常量的扰动会真正改变评测结果？转场是不是最不敏感的那个？ | MIN_SHOOT_BLOCK_MINUTES 上调 50% 会让至少 10 条 brief 从通过变成失败，而 MIN_TRANSFER_MINUTES 同样扰动的影响不超过 2 条 | MIN_SHOOT_BLOCK_MINUTES 上调 50% 后从通过变失败的 brief 条数：— → 0 | **推翻** |
| E18 | 2026-11-18 | 约束规则 | 把转场改成 rms 后，半径跨度大的城市（洛阳、杭州）少排地点的条数是否明显多于西安？ | 洛阳与杭州合计少排地点的条数不少于 6 条，且高于西安 | 洛阳与杭州合计少排一个地点的条数（每城 40 条盲测）：— → 0 | **推翻** |
| E17 | 2026-11-18 | 约束规则 | 把转场从固定 20 分钟改成 sqrt(a²+b²)（均方根估计），会让多少条 brief 少排一个地点？ | 主集 80 条里有 10 到 25 条会从两个地点降到一地点 | rms 策略比固定 20 分钟少排一个地点的 brief 条数：— → 2 | **推翻** |
| E16 | 2026-11-18 | 约束规则 | MIN_TRANSFER_MINUTES=20 这个固定值，是否覆盖知识库里大多数同城地点对的转场？ | 同城地点对中至少 80% 的 20 分钟落在「到中心时间之差」与「之和」之间 | 20 分钟落在估算区间的同城地点对占比（%）：— → 92 | 确认 |
| E15 | 2026-11-18 | 约束规则 | 新增的 TIME_BELOW_REQUIRED_LOCATIONS 会不会把本来可执行的 brief 判成不可行？ | 盲测集 320 条里被这条规则拒绝的条数为 0 | 盲测集里触发该规则的条数：— → 0 | 确认 |
| E14 | 2026-11-17 | 多轮交互 | 「赶五点高铁」识别成结束时刻，并真的约束排程——一步做不到？ | 识别能过 M07（12/12），但约束力存疑：软规则可能解析不到模型写法（实测：识别通过、边际 1 条；软规则未在真实模型上验证） | 通过用例数（/12）：11 → 12 | 部分 |
| E13 | 2026-11-17 | 多轮交互 | 风格否定替换（不要唐制，换宋制）能靠词表+位置判定做好吗？ | M04 通过，10/12 -> 11/12（实测：全开去掉风格 = 11/12，边际 1 条） | 通过用例数（/12）：10 → 12 | 确认 |
| E12 | 2026-11-17 | 多轮交互 | 补上中文数字与增量/绝对量解析，能让多轮通过数涨多少？ | M01/M02/M06/M08/M10 五条通过，5/12 -> 10/12（实测精确命中：只开数字时恰好 10/12，剩失败为 M04 风格、M07 时刻） | 通过用例数（/12）：5 → 10 | 确认 |
| E11 | 2026-11-17 | 文档治理 | 61 份报告里，冗余和失效链接到底有多少？ | 预计有若干重复，具体不知 | 人写报告冗余对：— → 3 | 确认 |
| E10 | 2026-11-17 | 多轮交互 | followup 能听懂多少真实的多轮调整？ | 探索性：预计会有缺口，但不知多大 | 通过用例数（/12）：— → 5 | 确认 |
| E06 | 2026-11-17 | 约束规则 | 把妆造时间还给被压到 5 分钟的拍摄块，能否消掉 SHOOT_TOO_SHORT？ | SHOOT_TOO_SHORT 4 例 → 0 | 触发 SHOOT_TOO_SHORT 的用例数：4 → 0 | 部分 |
| E05 | 2026-11-17 | 运行参数 | 全量里 2 例异常，是模型失败还是墙钟预算切断？ | 两例恢复，异常归零 | 硬通过例数（/80）：78 → 80 | 确认 |
| E09 | 2026-11-16 | 测量方法 | 这套规则里有多少能迁移出旅拍领域？ | 我原估：9 条通用、6 条需改 | 逻辑可迁移规则数（/15）：9 → 14 | **推翻** |
| E07 | 2026-11-16 | 模型通道 | 换到 wujie-api 中转，能否消掉硬约束层面的失败？（与 E01 叠加，不可单独归因） | 硬约束 80/80 | 见证据文档 | 确认 |
| E04 | 2026-11-16 | 约束规则 | SHOOT_TOO_SHORT 的固定 60 分钟门槛，对短预约是否合理？ | 短预约的误报减少 | 触发 SHOOT_TOO_SHORT 的用例数：4 → 2 | 部分 |
| E03 | 2026-11-16 | 提示词 | planner-v3 prompt 是否优于 v2？ | 均分提升 | 见证据文档 | **推翻** |
| E02 | 2026-11-16 | 成本与延迟 | reasoning_effort=low 会不会牺牲方案质量？ | 输出 token 降 50% 以上，均分不降 | 见证据文档 | 确认 |
| E01 | 2026-11-16 | 光线排程 | 把光线窗口对齐从 prompt 移到代码，能否消掉 LIGHT_MISUSE？ | LIGHT_MISUSE 31 例 → 0，硬通过率不降 | 触发 LIGHT_MISUSE 的用例数：31 → 0 | 确认 |
| E08 | 2026-11-15 | 知识库数据 | 城市从 5 个扩到 8 个，评测质量会提升吗？ | 通过率或覆盖度提升 | 盲测集覆盖的城市数：5 → 8 | 部分 |

## 待结

无。

## 证据索引

每条实验的 `evidence` 字段必须指向一个真实文件，脚本会校验；点不动的结论不写进台账。

- E33 → [docs/real-model-iterate-2026-11-27.md](../docs/real-model-iterate-2026-11-27.md)
- E32 → [docs/real-model-2026-11-27.md](../docs/real-model-2026-11-27.md)
- E31 → [docs/soak-proxy-30m.md](../docs/soak-proxy-30m.md)
- E30 → [docs/soak-independent-20m.md](../docs/soak-independent-20m.md)
- E29 → [docs/soak-ab.md](../docs/soak-ab.md)
- E28 → [docs/batch-kill.md](../docs/batch-kill.md)
- E27 → [docs/batch-write.md](../docs/batch-write.md)
- E26 → [docs/stage-profile.md](../docs/stage-profile.md)
- E25 → [docs/startup-race.md](../docs/startup-race.md)
- E24 → [docs/write-lock-retest.md](../docs/write-lock-retest.md)
- E23 → [docs/eval-report.md](../docs/eval-report.md)
- E22 → [tests/test_feasibility.py](../tests/test_feasibility.py)
- E21 → [docs/mutation-report.md](../docs/mutation-report.md)
- E20 → [docs/eval-margin.md](../docs/eval-margin.md)
- E19 → [docs/sensitivity.md](../docs/sensitivity.md)
- E18 → [docs/transfer-by-city.md](../docs/transfer-by-city.md)
- E17 → [docs/transfer-model.md](../docs/transfer-model.md)
- E16 → [docs/transfer-budget.md](../docs/transfer-budget.md)
- E15 → [docs/feasibility-sweep.md](../docs/feasibility-sweep.md)
- E14 → [docs/multi-turn-attribution.md](../docs/multi-turn-attribution.md)
- E13 → [docs/multi-turn-attribution.md](../docs/multi-turn-attribution.md)
- E12 → [docs/multi-turn-attribution.md](../docs/multi-turn-attribution.md)
- E11 → [docs/report-audit.md](../docs/report-audit.md)
- E10 → [docs/multi-turn.md](../docs/multi-turn.md)
- E06 → [docs/styling-budget.md](../docs/styling-budget.md)
- E05 → [docs/budget-vs-concurrency.md](../docs/budget-vs-concurrency.md)
- E09 → [docs/generality.md](../docs/generality.md)
- E07 → [docs/real-model-baseline-2026-11-16.md](../docs/real-model-baseline-2026-11-16.md)
- E04 → [docs/styling-budget.md](../docs/styling-budget.md)
- E03 → [docs/prompt-ab.md](../docs/prompt-ab.md)
- E02 → [docs/real-model-baseline-2026-11-16.md](../docs/real-model-baseline-2026-11-16.md)
- E01 → [docs/real-model-light-fix-2026-11-16.md](../docs/real-model-light-fix-2026-11-16.md)
- E08 → [docs/city-coverage.md](../docs/city-coverage.md)

## 口径与已知偏差

- **判定是人事后填的**，不是算法算的。算法只负责校验证据存在、汇总比例、以及指出「推翻率长期为零」这种结构性问题。
- **样本少时比例无意义**：少于 5 条只记录，不给结论（脚本会自动降级措辞）。
- **成本只记台账内实测花费**，不含离线实验与本次对话。
- **`from`/`to` 是单值对比**，不含置信区间；有区间的实验以 evidence 文档为准。
