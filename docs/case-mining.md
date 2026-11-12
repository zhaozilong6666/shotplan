# 盲测挑战集：从失败案例库挖出来的新用例

> 由 `python -m evals.case_miner --write` 生成，请勿手工编辑。

## 它回答什么问题

80 个回归用例是我自己写的，「80/80 全绿」只说明规划器通过了**见过的题**。
真正该追问的是：规则是不是只对着这 80 个 brief 调出来的？
所以这里用**失败案例库 + 知识库**生成一批主评测集从未见过的组合，再让同一条流水线、同一个校验器去跑，看结论是否还成立。

挖掘过程**不读取任何用例的期望输出**，只看到 brief 本身用于去重——不存在「照着答案写用例」的空间。详见 [ADR 0010](adr/0010-holdout-challenge-set.md)。

## 为什么不直接并进主评测集

`evals/baselines/v1..v6` 是六个时代的对照快照，可比性来自「用例集变动缓慢且可解释」。若把挖掘结果直接塞进 `evals/cases/`，所有历史基线会同时失去意义，评测报告里的分数变化也无法再区分「实现变好了」和「题目变简单了」。
因此挑战集独立存放在 `evals/challenge/`，`run_eval` 不会读它；它可以持续长大，而主集继续做纵向回归。

## 输入：失败案例库的聚类

来自 `evals/failures/cases.jsonl` 的 **517** 条记录、**4** 类硬约束违规。按「问题码 × 城市」统计命中次数：

| 问题码 | hangzhou | luoyang | nanjing | suzhou | xian |
| --- | ---: | ---: | ---: | ---: | ---: |
| `BUDGET_EXCEEDED` | 9 | 12 | 9 | 9 | 30 |
| `TIME_OVERRUN` | 8 | 11 | 8 | 8 | 29 |
| `TRANSFER_BUFFER` | 27 | 36 | 27 | 27 | 81 |
| `UNKNOWN_LOCATION` | 36 | 48 | 36 | 36 | 30 |

对照组的缺陷是构造出来的，因此这个矩阵不是真实模型的错误分布；它的用途是告诉挖掘器「哪些规则在哪些城市被验证过」，从而定位覆盖缺口。

## 现有评测集的覆盖缺口

主集 80 个用例在 25 个「城市 × 主风格」格子中只覆盖 **19** 个。
挑战集按知识库做完整交叉：5 城 × 5 风格 × 9 种压力配置 × 4 季轮换日期 = **225** 个候选 brief。
压力配置共 9 种：标准、3 小时压缩、600 元紧预算、排除两个户外热门地点、9 小时 3 人、双风格混搭、贴近理论下限的 400 元、排除全部户外地点、以及「同一地点既指定又排除」的矛盾输入。其中 `conflicting` 是故意放进来的**负向对照**，用来检验生成前可行性判定会不会把不可满足的 brief 挡在挑战集之外；`no_outdoor` 与 `micro_budget` 则用于复核两处历史修复。日期按 (城市, 风格, 压力) 索引在四季里轮换，全部使用固定值，不含随机数与时间戳。

## 筛选口径

每个候选都跑 `good` 与 `naive` 两个 provider，走完整的「可行性判定 → 检索 → 生成 → 校验 → 修复」流水线。只有同时满足以下五条才纳入：

1. brief 与主集 80 个用例规范化后**不重复**；
2. `good` 返回 `status=ok` 且硬约束全通过；
3. `good` 得分 ≥ `0.7`（写进用例的粗粒度下限）；
4. `naive` 必须被判定为不可执行；
5. 两者得分差 ≥ `0.3`，确保这条用例真有区分度。

## 结果

| 指标 | 值 |
| --- | ---: |
| 候选 brief | 225 |
| 纳入挑战集 | **200** |
| 被筛掉 | 25 |
| 城市 × 风格覆盖 | 19/25 → **25/25** |
| 与主集不重复的纳入 brief | **200** / 200 |
| 新覆盖的城市 × 风格格子 | **6** |
| good 硬约束通过率 | 100% |
| good 均分 / 最低分 | 0.9881 / 0.9587 |
| naive 硬约束通过率 | 0% |
| naive 均分 | 0.19 |
| 最小得分差 | 0.7687（要求 ≥ 0.3） |

注意：这里的 `good` 高分**不是**新的效果声明，它和主集一样来自离线规则 provider；价值在于「换了没见过的组合，判定链仍然一致」，而不是分数本身。

## 被筛掉的候选

| 原因 | 次数 |
| --- | ---: |
| good hard constraints failed: INFEASIBLE | 25 |
| good score 0.0 below 0.7 | 25 |
| good status=infeasible | 25 |
| good/naive gap 0.0 below 0.3 | 25 |
| no comparable pair of plans (a provider refused) | 25 |

前几条明细（用于人工复核筛选口径是否过松或过严）：

- `hangzhou` / `tang` / `conflicting`：good status=infeasible；good hard constraints failed: INFEASIBLE；good score 0.0 below 0.7；no comparable pair of plans (a provider refused)；good/naive gap 0.0 below 0.3（good 0.0、naive 0.0）
- `hangzhou` / `song` / `conflicting`：good status=infeasible；good hard constraints failed: INFEASIBLE；good score 0.0 below 0.7；no comparable pair of plans (a provider refused)；good/naive gap 0.0 below 0.3（good 0.0、naive 0.0）
- `hangzhou` / `ming` / `conflicting`：good status=infeasible；good hard constraints failed: INFEASIBLE；good score 0.0 below 0.7；no comparable pair of plans (a provider refused)；good/naive gap 0.0 below 0.3（good 0.0、naive 0.0）
- `hangzhou` / `weijin` / `conflicting`：good status=infeasible；good hard constraints failed: INFEASIBLE；good score 0.0 below 0.7；no comparable pair of plans (a provider refused)；good/naive gap 0.0 below 0.3（good 0.0、naive 0.0）
- `hangzhou` / `republic` / `conflicting`：good status=infeasible；good hard constraints failed: INFEASIBLE；good score 0.0 below 0.7；no comparable pair of plans (a provider refused)；good/naive gap 0.0 below 0.3（good 0.0、naive 0.0）

## 负向对照：可行性判定挡住了什么

被筛掉的 **25** 个候选全部来自 `conflicting` 配置（5 城 × 5 风格 = 25）。它们不是「规划器写不出来」，而是生成前的 `check_feasibility` 直接返回 `CONFLICTING_CONSTRAINTS`，两个 provider 都停止生成、得 0 分，因此不构成可比较的一对方案。
如果哪天这批候选突然被纳入挑战集，说明前置可行性判定被削弱了——CI 会把「负向对照消失」当成回归，而不是当成覆盖率提升。

## 顺带复核的两处历史修复

`no_outdoor`（排除全部户外地点）与 `micro_budget`（贴近理论下限 400 元）两类共 50 个候选全部通过硬约束。
前者对应「新增城市时室内备选地点被硬编码」的旧缺陷，后者对应 C24「紧预算仍选收费景点导致超支」的旧缺陷。这两类在主集里只有零星用例覆盖，这里在全部 25 个城市 × 风格组合上重跑一遍，说明修复不是对着单条用例打补丁。

## CI 检查什么

`python -m evals.case_miner --check` 会**重新挖掘一遍**，并逐字节比对`evals/challenge/cases.jsonl`、`evals/case_mining.json` 与本文件；同时断言纳入数、新组合数、覆盖格数、最小得分差没有退化。
也就是说，改动校验器或知识库导致某个组合不再被正确判定时，门禁会在「挑战集内容变了」这一层直接失败，而不是悄悄把用例删掉了事。

## 边界

- 候选由固定网格生成，覆盖的是**组合多样性**，不是真实客户 brief 的分布。
- 对照组缺陷是构造的，所以「naive 全挂」只证明判定链一致，不证明真实模型会更差。
- 挑战集不参与 `run_eval`，也不影响 v1–v6 基线；它是独立的一层证据。

## 复现

```bash
python -m evals.case_miner --write   # 重新生成三份产物
python -m evals.case_miner --check  # CI 门禁
```
