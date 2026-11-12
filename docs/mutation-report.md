# 门禁有效性：变异测试

> 由 `python -m evals.mutation_lab --write` 生成，请勿手工编辑。
> 所有改动都在**进程内注入并回滚**，不会修改仓库里的任何源文件。

## 为什么需要这一层

评测集 80/80 全绿、均分 0.9788，只能说明「当前代码通过了当前检查」，不能说明**检查本身有效**。一个永远返回满分的校验器，同样能让 CI 全绿。

本实验注入 8 处受控故障，用同一套用例、同一条流水线重跑，要求真实门禁 `evals.scorers.eval_gate_problems` 每一次都拒绝：**8/8 被拦截，存活 0 处**。
作为对照，修复前的旧门禁（均分下限 + 对照组不能更好）只能拦住 **4/8** 处。

盲点很具体：故意犯错的对照组同时违反多条硬规则，`constraints` 维度早就被 clamp 到 0；此时悄悄删掉一条规则，对照组均分几乎不动，好方案也没变化——旧门禁看不出任何异常。所以 ADR 0009 给对照组加了**规则级覆盖下限**：每条已知缺陷都必须被报出来。

## 方法

1. 从 `evals/cases/*.jsonl` 读入全部用例，good / naive 两个 provider 各跑一遍。
2. 每处变异只改一个模块级常量或一个方法，注入前后共用同一批用例与同一份门禁函数。
3. 门禁函数不是本实验的复制品，而是 `run_eval` 直接调用的那个，所以改门禁而忘了改实验不会发生。
4. 每轮结束后用 `ExitStack` 回滚，再跑一次基线并逐字段比对；只有确定性字段参与比对（不含耗时、id）。

## 结果

| 变异体 | 改了什么 | good 均分（较基线） | 硬约束通过率 | 失败用例 | naive 均分 | 差值 | 触发的门禁 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `planner/no-transfer` | 生成端不再插入转场时间块（校验端仍要求 20 分钟） | 0.1375 (-0.8413) | 14% | 69 | 0.3014 | -0.1639 | 拦截：avg_score 0.1375 is below threshold 0.8 等 5 项 |
| `planner/transfer-cost-5000` | 转场费从 50 元抬到 5000 元 | 0.7318 (-0.2470) | 14% | 69 | 0.3014 | 0.4304 | 拦截：avg_score 0.7318 is below threshold 0.8 等 4 项 |
| `planner/no-makeup` | 生成端删掉必需的妆造时间块 | 0.8886 (-0.0902) | 14% | 69 | 0.3014 | 0.5872 | 拦截：hard_pass_rate 0.1375 is below 0.9 等 2 项 |
| `validator/budget-tolerance` | 预算容差改成 10^9，BUDGET_EXCEEDED 永不触发 | 0.9788 (+0.0000) | 100% | 0 | 0.4744 | 0.5044 | 拦截：control avg_score 0.4744 exceeds 0.45: the validator may have been weakened 等 2 项 |
| `validator/no-transfer-rule` | 转场下限改成 0，TRANSFER_BUFFER 永不触发 | 0.9788 (+0.0000) | 100% | 0 | 0.3176 | 0.6612 | 拦截：control rule TRANSFER_BUFFER fired 0 times, expected >= 100: a validator rule appears to be disabled |
| `validator/no-time-limit` | 时长宽限改成 10^6 分钟，TIME_OVERRUN 永不触发 | 0.9788 (+0.0000) | 100% | 0 | 0.3014 | 0.6774 | 拦截：control rule TIME_OVERRUN fired 0 times, expected >= 30: a validator rule appears to be disabled |
| `validator/no-grounding` | 所有地点 id 都视为存在，UNKNOWN_LOCATION 永不触发 | 0.9788 (+0.0000) | 100% | 0 | 0.3019 | 0.6769 | 拦截：control rule UNKNOWN_LOCATION fired 0 times, expected >= 90: a validator rule appears to be disabled |
| `validator/always-pass` | 校验器直接返回满分且无问题 | 1.0000 (+0.0212) | 100% | 0 | 1.0000 | 0.0000 | 拦截：control group scored at or above the planner: measurement is broken 等 11 项 |

前三行是**生成端**变坏：分数、通过率、失败用例数直接掉下来，任何像样的阈值都能拦住。后五行是**校验端**被悄悄削弱：好方案的指标几乎不变，只有对照组的规则覆盖发生了变化。

## 新增的检查

| 检查 | 阈值 | 基准实测 | 抓到哪类变异 |
| --- | ---: | ---: | --- |
| 两组均分差值下限 | 0.45 | 0.6774 | 校验器整体失效 |
| 对照组均分上限 | 0.45 | 0.3014 | 预算规则被放宽 |
| 对照组硬约束通过率上限 | 0.3 | 0.1375 | 校验器整体失效 |
| 对照组规则覆盖下限 | 见下表 | — | 单条规则被删 |

| 规则码 | 覆盖下限 | 基准实测命中 |
| --- | ---: | ---: |
| `TRANSFER_BUFFER` | 100 | 198 |
| `UNKNOWN_LOCATION` | 90 | 186 |
| `BUDGET_EXCEEDED` | 30 | 69 |
| `TIME_OVERRUN` | 30 | 64 |
| `SHOOT_TOO_SHORT` | 30 | 69 |
| `NO_ALTERNATIVE` | 30 | 69 |
| `NO_SOURCE_REF` | 30 | 69 |

下限约为实测次数的一半：新增用例不会让门槛漂移，但删掉一条规则必然让命中次数归零。

## 回滚验证

每次变异退出后都重跑基线并逐字段比对，共 8 次，结论：**全部与基线一致**。最终 good 均分 0.9788、硬约束通过率 100%、失败用例 0。

这也顺带在 [eval-report.md](eval-report.md) 里留下了一个可复核的「改动 → 分数下降 → 门禁拦截 → 回滚复原」实例，而不是一句口头承诺。

## 边界

变异体是人为构造的，覆盖不了所有可能的实现缺陷；它证明的是**这八类回归一定会被发现**，不是「门禁万无一失」。另外实验只使用离线 provider，真实模型接入后应把新的失败模式补成新的变异体。

## 复现

```bash
python -m evals.mutation_lab --write
python -m evals.mutation_lab --check      # CI：报告过期或出现存活变异即失败
```

`docs/mutation-report.md` 与 `evals/mutation.json` 均由代码生成，请勿手工编辑。
