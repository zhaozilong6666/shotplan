# 评测方法论

## 一句话

评测不是「跑一遍看看有没有报错」，而是**一个可复现的对照实验**：同一批输入、两个能力不同的 provider、同一套判定规则，然后比较分布差异。

## 运行方式

```bash
python -m evals.run_eval                         # 只跑，打印摘要
python -m evals.run_eval --write-report         # 生成 docs/eval-report.md
python -m evals.run_eval --compare evals/baselines/v6-naive.json
python -m evals.run_eval --save-baseline evals/baselines/v6-naive.json
python -m evals.run_eval --prompt-version planner-v1 --write-report
python -m evals.run_eval --min-score 0.85 --min-hard-pass-rate 0.95
```

产物：

- `evals/out/latest.json`：完整原始结果（每个用例的分数、问题码、耗时、修复轮次、token）
- `docs/eval-report.md`：代码生成的 Markdown 报告
- `evals/baselines/*.json`：对照组基线快照。`v1-naive.json` 是 12 用例时代的历史基线，**不能与当前 80 用例的结果直接比较**；当前使用 `v6-naive.json`

`--write-report` 生成的文件不要手工编辑；改内容请改 `evals/run_eval.py::build_report` 后重新生成。`docs/eval-report.md` **会随仓库提交**，让访客不必运行代码就能看到结论；`evals/out/latest.json` 才是被 `.gitignore` 忽略的原始产物。

## 判定链

```
brief (jsonl)
  → PlanRequest.from_dict + normalized
  → check_feasibility        ← 生成前的确定性判定，不经过模型
  → provider.generate (good / naive)   ← 不可行时整段跳过
  → parse_plan               ← 解析失败记作用例失败，不中断整批
  → validate                 ← 12 条规则 + 5 维度评分
  → evaluate_case            ← 硬约束结论符合预期 AND 分数 >= 用例下限
                               （不可满足用例：正确拒绝才通过）
  → aggregate                ← 通过率、均分、分组、P50/P95、token
  → eval_gate_problems       ← 阈值 + 对照组有效性 + 规则级覆盖（唯一门禁实现）
  → mutation_lab --check     ← 反向验证：注入故障后上述门禁必须失败
  → exit code                ← CI 据此失败
```

四类独立的失败信号，不要混为一谈：

1. **硬约束未通过** — 方案不可执行。
2. **用例未通过** — 硬约束结论或质量下限不符合该用例预期。
3. **测量失效** — `good` 均分 ≤ `naive` 均分，说明打分器已经分不出好坏，此时即使前两项通过也必须让 CI 失败。
4. **测量被削弱** — 对照组突然不再命中某条已知缺陷、分数异常升高，或两组差值过小。这一条由 `control_group_check` 负责：对照组是一份**已知缺陷清单**，不只是「低分样本」。少了它，删掉单条校验规则后 CI 仍会全绿（实测 4/8 变异漏网，见 [ADR 0009](adr/0009-gate-sensitivity.md)）。

门禁实现收敛在 `evals.scorers.eval_gate_problems`，`run_eval` 与变异实验室共用同一个函数。

## 阈值

| 项 | 默认 | 来源 |
| --- | --- | --- |
| 平均分下限 | 0.80 | `SHOTPLAN_EVAL_MIN_SCORE` / `--min-score` |
| 硬约束通过率下限 | 0.90 | `--min-hard-pass-rate` |
| 用例失败数 | 0 | `regression_check` |
| good − naive 差值下限 | 0.45 | `--min-delta` |
| 对照组均分上限 | 0.45 | `MAX_CONTROL_AVG_SCORE` |
| 对照组硬约束通过率上限 | 0.30 | `MAX_CONTROL_HARD_PASS_RATE` |
| 对照组规则覆盖下限 | 见 `CONTROL_RULE_MIN_COUNTS` | 实测次数的一半左右 |

阈值写在环境变量而不是硬编码，是为了让阶段 4 接真实模型后可以逐步提高门槛，而不必改代码。

## 四类用例与「正确拒绝」

当前 80 个用例：`normal` 30、`conflict` 23、`edge` 16、`infeasible` 11。

`infeasible` 组描述的不是「难做」，而是**数学上不可能同时满足**：预算低于最低可执行成本、时长短于最短闭环、城市不在知识库、约束互相矛盾。用例带 `expect_infeasible: true`。这类用例的期望输出不是方案，而是 `status=infeasible` + 原因/缺口/建议；正确拒绝记 1.0 分，硬凑方案记 0.0 分（见 [ADR 0007](adr/0007-feasibility-before-generation.md)）。

因为 preflight 在生成之前且不确定于 provider，两个 provider 在这 11 个用例上必然同分。这不是评测漏洞，而是设计结果：**能力差异应由生成质量体现，不应由「会不会做算术」体现**。

## 如何新增用例

在 `evals/cases/` 下任意 `.jsonl` 文件加一行：

```json
{"id":"C37","group":"conflict","description":"...","request":{"hours":4,"budget_cny":600,"styles":["tang"]},"expect_hard_pass":true,"expect_min_score":0.65}
```

规则：

- `id` 全局唯一（`load_cases()` 会检查）。
- `group` 只能是 `normal` / `conflict` / `edge` / `infeasible`。
- `expect_min_score` 先跑一次看实际得分再定，不要为了让用例通过而把阈值设成 0。阈值应当反映「这个场景下合理的质量水平」。
- 不要写期望输出。用例描述的是**问题和约束**，不是答案；写死答案会让评测退化成字符串比对。

新增用例后必须重刷基线，否则 `--compare` 会把用例集变化误读成质量变化：

```bash
python -m evals.run_eval --write-report --save-baseline evals/baselines/v6-naive.json
python -m pytest tests/test_scorers.py -q
```

## 如何做 prompt 版本对比（阶段 4）

框架已经就绪：`agents/prompts.py` 维护 `SYSTEM_PROMPTS` 的 v1/v2 与 `PROMPT_CHANGELOG`，`get_system_prompt()` 按版本解析，真实模型 provider 与评测 trace 都记录 `prompt_version`；`--prompt-version` 可把指定版本写进报告。离线 mock 不消费系统提示词，因此离线跑两个版本分数相同是正常的。

接真实模型后：

1. `SHOTPLAN_PROVIDER=openai_compatible`，用 `--save-baseline evals/baselines/before-v3.json` 保存当前结果。
2. 分别用 `--prompt-version planner-v1` 与 `planner-v2` 跑 `--write-report`，对比硬约束通过率、均分、修复轮次与成本。
3. 把实测差异写回 `PROMPT_CHANGELOG`；若均分下降超过阈值，CI 会拦下。

因为校验器不依赖模型，两个版本、两个模型之间的分数是**可比**的——这是 [ADR 0002](adr/0002-validator-outside-model.md) 的直接收益。

## 常见误读

- **「均分 0.98 说明系统完美」** — 不。它说明当前离线 provider 严格按规则生成。真实模型接入后这个数字大概率下降，那才是评测开始发挥作用的时候。
- **「对照组分数低是理所当然的，没必要跑」** — 恰恰相反。控制组检验的是评测工具，不是被测系统。去掉它，整套分数就失去意义。
- **「用例全通过就不用看明细」** — 要看。C10 的 `SHOOT_TOO_SHORT` 是有价值的信号，说明极短预约下质量确实在下降。
- **「不可满足用例两组同分，说明评测失效」** — 见上文：可行性判定是共享的确定性前置步骤，区分度由其余 69 个用例提供。

## 另外两层：模糊测试与性能基准

评测集回答的是「在真实 brief 上够不够好」。它天生回答不了另外两个问题，所以各有一层专门负责：

| 层 | 问题 | 判定 | 报告 |
| --- | --- | --- | --- |
| 评测集 | 方案质量够不够？ | 分数、硬约束通过率、对照组差值 | [eval-report.md](eval-report.md) |
| 模糊测试 | 垃圾输入会不会让服务崩？ | 0 未捕获异常、0 不可序列化结果 | [fuzz-report.md](fuzz-report.md) |
| 性能基准 | 有没有性能回归？ | p95、吞吐、堆峰值相对绝对上限 | [benchmark.md](benchmark.md) |
| 并发负载 | 并发下会不会丢写或报错？ | 全部有应答、全部落库、0 错误 | [loadtest.md](loadtest.md) |

模糊测试的输入由真实 brief 变异而来（错类型、`NaN`、`1e400`、emoji 洪水、NULL 字段、不存在的日期），每个用例带独立种子，失败可用 `--only-seed` 单条复现。它守住的不变量是「无论输入是什么，都返回结构化结果」——这条契约能成立，是因为归一化是全域函数、非法日期走结构化拒绝、provider 与解析失败返回 `status=error`。

并发负载测的是「同时来 8 个请求会怎样」：既验证正确性（不丢写、不报错），也暴露饱和点。

性能基准测的是**不含模型延迟**的常数成本，用 `tracemalloc` 单独一轮统计堆峰值。门禁设在实测值的十倍以上（p95 < 50ms、吞吐 > 50/s、堆 < 64MB），目的是抓「意外的 O(n²)」「每次调用重载知识库」这类回归，而不是卡 CI 抖动。

## 相关材料

- [修复循环实验](repair-experiment.md)：把「有修复循环」变成可量化的成功率与拦截次数。
- [失败案例库](failure-gallery.md)：校验器真实抓到过的错误模式，新增规则的证据来源。
- [输入模糊测试](fuzz-report.md)：任意输入不崩的契约与可复现种子。
- [性能基准](benchmark.md)：延迟分位、吞吐与内存画像。
- [并发负载测试](loadtest.md)：并发下的错误数、丢写与饱和点。
- [线程 vs 进程](scaling.md)：同一 workload 两种并行方式，说明瓶颈在 GIL。
- [真实输出示例](examples.md)：四份由代码生成、可比对的结果，含一次结构化拒绝。
- [可观测性说明](observability.md)：评测报告是实验，运行统计是实际使用记录，两者不能互相替代。
