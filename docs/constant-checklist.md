> 由 `python scripts/build_constant_checklist.py --write` 生成，请勿手工编辑。

# 改一个规则常量之前，先看这张表

`docs/thresholds.md` 回答「这个数是量出来的还是拍的」；这张表回答下一个问题，也是真正会出事的问题：**改它的时候，哪些地方必须跟着动，漏了会被谁抓到。**

答案分散在五处：定义、按名绑定它的模块、卡在它边界上的探针组、专门抓「只改一侧」的变异实验，以及把数字渲染进规则文本的 `RULES` 表。手写清单一周就会过期——这个项目已经抓到过一次复制出来的 `20`，所以这里全部从代码读。

## 怎么用

1. 只改 `constraints.py` 里的定义。下表里的模块都是**按名绑定**，改源即生效。
2. 新模块要用这个数，就 `from shotplan.domain.constraints import ...`，不要写字面量。
3. 改完跑 `make gates`。有探针的常量会在对应组里被检查：`transfer` / `budget` 用one-sided 组模拟「只改校验端、忘了生成端」，`time` 则直接伪造一个超时方案。
4. 如果改了值，`RULES` 里的规则文本会跟着变（f-string），但 `docs/` 下的解释文字不会——`scripts/check_doc_claims.py` 会盯住其中一部分。

## 总览

| 常量 | 值 | 依据 | 按名绑定 | 规则 | 探针 | 变异 |
| --- | --- | --- | --- | --- | --- | --- |
| `BUDGET_TOLERANCE_CNY` | 0 | 设计选择 | 1 src | `—` | budget | validator/budget-tolerance |
| `MAX_OVERRUN_MINUTES` | 15 | 设计选择 | 1 src | `hard:TIME_OVERRUN` | time | validator/no-time-limit |
| `MIN_ALTERNATIVES` | 1 | 设计选择 | 1 src | `soft:NO_ALTERNATIVE` | — | — |
| `MIN_FEASIBLE_HOURS` | 1.5 | 设计选择 | 1 src | `gate:feasibility` | hours-floor | feasibility/no-min-hours |
| `MIN_SHOOT_BLOCK_FLOOR` | 30 | 实测 | 0 | `soft:SHOOT_TOO_SHORT` | — | — |
| `MIN_SHOOT_BLOCK_MINUTES` | 60 | 设计选择 | 2 src | `soft:SHOOT_TOO_SHORT` | — | — |
| `MIN_SOURCE_REFS` | 2 | 设计选择 | 1 src | `soft:NO_SOURCE_REF` | — | — |
| `MIN_TRANSFER_COST_CNY` | 50 | 设计选择 | 3 src | `—` | budget-floor | — |
| `MIN_TRANSFER_MINUTES` | 20 | 设计选择 | 5 src / 2 tests | `hard:TRANSFER_BUFFER` | transfer | planner/no-transfer、validator/no-transfer-rule |
| `STYLING_FLOOR_MINUTES` | 60 | 设计选择 | 1 src | `indirect` | — | — |
| `STYLING_SHARE` | 0.35 | 实测 | 0 | `indirect` | — | — |

## 逐个常量

### `BUDGET_TOLERANCE_CNY = 0`

- 定义：`src/shotplan/domain/constraints.py:101`
- 注释：Hard ceiling: no plan may exceed the stated budget.
- 按名绑定（1 个模块 / 1 处使用）：`src/shotplan/agents/validator.py`（L166）
- 同文件内使用：L262
- 规则：`—`
- 探针：`budget`（4 条 brief）
- 变异：`validator/budget-tolerance`
- 漏改会怎样：只改一侧（比如只收紧校验端）时，`probe_lab` 的 one-sided 组会掉通过率——这正是「漏改一处」的典型症状。

### `MAX_OVERRUN_MINUTES = 15`

- 定义：`src/shotplan/domain/constraints.py:15`
- 注释：A plan may not run longer than the booked time plus this grace period.
- 按名绑定（1 个模块 / 1 处使用）：`src/shotplan/agents/validator.py`（L115）
- 同文件内使用：L152, L256
- 规则：`hard:TIME_OVERRUN`
- 探针：`time`（4 条 brief）
- 变异：`validator/no-time-limit`
- 漏改会怎样：只改一侧（比如只收紧校验端）时，`probe_lab` 的 one-sided 组会掉通过率——这正是「漏改一处」的典型症状。

### `MIN_ALTERNATIVES = 1`

- 定义：`src/shotplan/domain/constraints.py:113`
- 注释：A credible plan offers at least one fallback when weather or crowds interfere.
- 按名绑定（1 个模块 / 2 处使用）：`src/shotplan/agents/validator.py`（L284, L287）
- 同文件内使用：L221, L264
- 规则：`soft:NO_ALTERNATIVE`
- 探针：无
- 变异：无
- 漏改会怎样：没有专门的探针或变异；改动只能靠全量评测与测试兜底。

### `MIN_FEASIBLE_HOURS = 1.5`

- 定义：`src/shotplan/domain/constraints.py:56`
- 注释：Below this, makeup + one transfer + one shoot cannot fit at all.
- 按名绑定（1 个模块 / 4 处使用）：`src/shotplan/domain/feasibility.py`（L97, L103, L104, L192）
- 同文件内使用：L259
- 规则：`gate:feasibility`
- 探针：`hours-floor`（3 条 brief）
- 变异：`feasibility/no-min-hours`
- 漏改会怎样：只改一侧（比如只收紧校验端）时，`probe_lab` 的 one-sided 组会掉通过率——这正是「漏改一处」的典型症状。

### `MIN_SHOOT_BLOCK_FLOOR = 30`

- 定义：`src/shotplan/domain/constraints.py:28`
- 注释：But a 1.5-hour booking is makeup (90 min) + one transfer (20 min) + whatever is left. Demanding a 60-minute block there flagged 7 of 8 real-model failures that were physically impossible, not badly planned. The floor now scales with the booking, capped at the ceiling above.
- 按名绑定：无（只在本文件内使用）
- 同文件内使用：L34, L207
- 规则：`soft:SHOOT_TOO_SHORT`
- 探针：无
- 变异：无
- 漏改会怎样：没有专门的探针或变异；改动只能靠全量评测与测试兜底。

### `MIN_SHOOT_BLOCK_MINUTES = 60`

- 定义：`src/shotplan/domain/constraints.py:22`
- 注释：A shoot block shorter than this is not worth setting up lights for. This is the CEILING: with time to spare, a block should be at least this long.
- 按名绑定（2 个模块 / 2 处使用）：`src/shotplan/domain/feasibility.py`（L171）；`src/shotplan/light.py`（L94）
- 同文件内使用：L34, L208, L258
- 规则：`soft:SHOOT_TOO_SHORT`
- 探针：无
- 变异：无
- 漏改会怎样：没有专门的探针或变异；改动只能靠全量评测与测试兜底。

### `MIN_SOURCE_REFS = 2`

- 定义：`src/shotplan/domain/constraints.py:116`
- 注释：Every generated plan must cite the knowledge entries it used.
- 按名绑定（1 个模块 / 3 处使用）：`src/shotplan/agents/validator.py`（L291, L295, L341）
- 同文件内使用：L227, L265
- 规则：`soft:NO_SOURCE_REF`
- 探针：无
- 变异：无
- 漏改会怎样：没有专门的探针或变异；改动只能靠全量评测与测试兜底。

### `MIN_TRANSFER_COST_CNY = 50`

- 定义：`src/shotplan/domain/constraints.py:59`
- 注释：Minimum transport cost of any executable plan (shared by the offline planner).
- 按名绑定（3 个模块 / 5 处使用）：`src/shotplan/agents/mock.py`（L33）；`src/shotplan/domain/feasibility.py`（L73, L195）；`src/shotplan/knowledge/tools.py`（L125, L135）
- 同文件内使用：L260
- 规则：`—`
- 探针：`budget-floor`（4 条 brief）
- 变异：无
- 漏改会怎样：只改一侧（比如只收紧校验端）时，`probe_lab` 的 one-sided 组会掉通过率——这正是「漏改一处」的典型症状。

### `MIN_TRANSFER_MINUTES = 20`

- 定义：`src/shotplan/domain/constraints.py:18`
- 注释：Minimum buffer between two different shooting locations.
- 按名绑定（7 个模块 / 15 处使用）：`src/shotplan/agents/mock.py`（L42, L43, L279, L291…）；`src/shotplan/agents/validator.py`（L153, L159）；`src/shotplan/domain/feasibility.py`（L173）；`src/shotplan/domain/transfer.py`（L38）；`src/shotplan/knowledge/tools.py`（L98, L167）；`tests/test_knowledge.py`（L144）；`tests/test_transfer.py`（L37）
- 同文件内使用：L170, L257
- 规则：`hard:TRANSFER_BUFFER`
- 探针：`transfer`（12 条 brief）
- 变异：`planner/no-transfer`、`validator/no-transfer-rule`
- 漏改会怎样：只改一侧（比如只收紧校验端）时，`probe_lab` 的 one-sided 组会掉通过率——这正是「漏改一处」的典型症状。

### `STYLING_FLOOR_MINUTES = 60`

- 定义：`src/shotplan/domain/constraints.py:42`
- 按名绑定（1 个模块 / 1 处使用）：`src/shotplan/domain/feasibility.py`（L172）
- 同文件内使用：L52
- 规则：`indirect`
- 探针：无
- 变异：无
- 漏改会怎样：没有专门的探针或变异；改动只能靠全量评测与测试兜底。

### `STYLING_SHARE = 0.35`

- 定义：`src/shotplan/domain/constraints.py:41`
- 注释：Styling is the one block that can give time back. A 1.5-hour booking cannot hold a full 85-minute 民国 styling block plus a shoot, but the answer is a compressed styling, not a five-minute photoshoot -- which is what a real-model run actually produced before this rule existed (C46).
- 按名绑定：无（只在本文件内使用）
- 同文件内使用：L52
- 规则：`indirect`
- 探针：无
- 变异：无
- 漏改会怎样：没有专门的探针或变异；改动只能靠全量评测与测试兜底。

## 没有探针或变异的常量

这些数改了之后，没有任何一组探针或变异会专门变红。它们不是不能改，而是改完只能看全量评测——先开一条实验、写下预测，再动。注意：这不等于「没有测试」，只是本表的探针/变异两列都不覆盖它。

- `MIN_ALTERNATIVES`
- `MIN_SHOOT_BLOCK_FLOOR`
- `MIN_SHOOT_BLOCK_MINUTES`
- `MIN_SOURCE_REFS`
- `STYLING_FLOOR_MINUTES`
- `STYLING_SHARE`

## 这张表看不见的

- **复制成字面量的副本**：AST 只认名字。如果某个模块自己写了 `FIXED_MINUTES = 20`，它不会出现在「按名绑定」里。`mutation_lab` 的变异能抓到其中一部分，抓不到全部。
- **文档里的数字**：`docs/` 有些正文仍然手写阈值。`check_doc_claims.py` 只覆盖它能解析的那部分声明。
- **运行时才绑定的名字**：`patch.object(module, "NAME", ...)` 这类字符串引用不算 import，只有出现在 `tests/` 或 `evals/` 的补丁里时才会被本表看到。
- **单元测试里的边界断言**：对 `min_budget_cny` 这类派生值的断言会挡住改动，但它既不是探针也不是变异，不会出现在上表的两列里。`变异` 列只表示 mutation_lab 的 80 条评测语料看不看得见这个常量。

统计：11 个标量常量，5 个有探针，4 个有变异，6 个两者都没有。
