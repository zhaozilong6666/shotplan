# 评测方法论

## 一句话

评测不是「跑一遍看看有没有报错」，而是**一个可复现的对照实验**：同一批输入、两个能力不同的 provider、同一套判定规则，然后比较分布差异。

## 运行方式

```bash
python -m evals.run_eval                         # 只跑，打印摘要
python -m evals.run_eval --write-report         # 生成 docs/eval-report.md
python -m evals.run_eval --compare evals/baselines/v3-naive.json
python -m evals.run_eval --save-baseline evals/baselines/v3-naive.json
python -m evals.run_eval --prompt-version planner-v1 --write-report
python -m evals.run_eval --min-score 0.85 --min-hard-pass-rate 0.95
```

产物：

- `evals/out/latest.json`：完整原始结果（每个用例的分数、问题码、耗时、修复轮次、token）
- `docs/eval-report.md`：代码生成的 Markdown 报告
- `evals/baselines/*.json`：对照组基线快照。`v1-naive.json` 是 12 用例时代的历史基线，**不能与 36 用例的结果直接比较**；当前使用 `v3-naive.json`

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
  → regression_check         ← 阈值 + 对照组有效性
  → exit code                ← CI 据此失败
```

三个独立的失败信号，不要混为一谈：

1. **硬约束未通过** — 方案不可执行。
2. **用例未通过** — 硬约束结论或质量下限不符合该用例预期。
3. **测量失效** — `good` 均分 ≤ `naive` 均分，说明打分器已经分不出好坏，此时即使前两项通过也必须让 CI 失败。

## 阈值

| 项 | 默认 | 来源 |
| --- | --- | --- |
| 平均分下限 | 0.80 | `SHOTPLAN_EVAL_MIN_SCORE` / `--min-score` |
| 硬约束通过率下限 | 0.90 | `--min-hard-pass-rate` |
| 用例失败数 | 0 | `regression_check` |

阈值写在环境变量而不是硬编码，是为了让阶段 4 接真实模型后可以逐步提高门槛，而不必改代码。

## 四类用例与「正确拒绝」

当前 50 个用例：`normal` 18、`conflict` 14、`edge` 10、`infeasible` 8。

`infeasible` 组描述的不是「难做」，而是**数学上不可能同时满足**：预算低于最低可执行成本、时长短于最短闭环、城市不在知识库、约束互相矛盾。用例带 `expect_infeasible: true`。这类用例的期望输出不是方案，而是 `status=infeasible` + 原因/缺口/建议；正确拒绝记 1.0 分，硬凑方案记 0.0 分（见 [ADR 0007](adr/0007-feasibility-before-generation.md)）。

因为 preflight 在生成之前且不确定于 provider，两个 provider 在这 8 个用例上必然同分。这不是评测漏洞，而是设计结果：**能力差异应由生成质量体现，不应由「会不会做算术」体现**。

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
python -m evals.run_eval --write-report --save-baseline evals/baselines/v3-naive.json
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
- **「不可满足用例两组同分，说明评测失效」** — 见上文：可行性判定是共享的确定性前置步骤，区分度由其余 42 个用例提供。

## 相关材料

- [修复循环实验](repair-experiment.md)：把「有修复循环」变成可量化的成功率与拦截次数。
- [失败案例库](failure-gallery.md)：校验器真实抓到过的错误模式，新增规则的证据来源。
- [可观测性说明](observability.md)：评测报告是实验，运行统计是实际使用记录，两者不能互相替代。
