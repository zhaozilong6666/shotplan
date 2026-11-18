> 由 `python scripts/build_thresholds.py --write` 生成，请勿手工编辑。

# 规则的每一个数字，以及它凭什么

规则里可调的数不多，但每一个都能被追问「为什么是 60 不是 50」。这些理由就写在 `constraints.py` 每个常量的上方——位置是对的，但作为集合读不了：没人会为了问「哪个阈值是量出来的、哪个是拍的」去翻三个文件。

下表从代码里读出 11 个标量常量（规则清单、枚举和正则不算），保留各自的注释，并标出注释里是否引用了实际运行（用例编号、实验编号或「real-model」字样）。**这个标注是启发式的**：没有引用不等于拍脑袋，有引用也不等于测得充分。

| 常量 | 值 | 依据 |
| --- | --- | --- |
| `MIN_SHOOT_BLOCK_FLOOR` | 30 | 注释引用了实测 |
| `STYLING_SHARE` | 0.35 | 注释引用了实测 |
| `BUDGET_TOLERANCE_CNY` | 0 | 设计选择 |
| `MAX_OVERRUN_MINUTES` | 15 | 设计选择 |
| `MIN_ALTERNATIVES` | 1 | 设计选择 |
| `MIN_FEASIBLE_HOURS` | 1.5 | 设计选择 |
| `MIN_SHOOT_BLOCK_MINUTES` | 60 | 设计选择 |
| `MIN_SOURCE_REFS` | 2 | 设计选择 |
| `MIN_TRANSFER_COST_CNY` | 50 | 设计选择 |
| `MIN_TRANSFER_MINUTES` | 20 | 设计选择 |
| `STYLING_FLOOR_MINUTES` | 60 | 设计选择 |

## 有实测依据的（2 个）

这些数字背后有一次具体的运行，失败模式是那条注释里写的那个。

- **`MIN_SHOOT_BLOCK_FLOOR = 30`** —— But a 1.5-hour booking is makeup (90 min) + one transfer (20 min) + whatever is left. Demanding a 60-minute block there flagged 7 of 8 real-model failures that were physically impossible, not badly planned. The floor now scales with the booking, capped at the ceiling above.
- **`STYLING_SHARE = 0.35`** —— Styling is the one block that can give time back. A 1.5-hour booking cannot hold a full 85-minute 民国 styling block plus a shoot, but the answer is a compressed styling, not a five-minute photoshoot -- which is what a real-model run actually produced before this rule existed (C46).

## 设计选择的（9 个）

这些是按经验定的初始值，没有针对性实验。它们不是缺陷——大多数产品参数就该先有值、再等失败来修正——但面试被问「凭什么」时，诚实的答案是「这是起点，E04/E06 正在动它」，而不是编一个来源。

- `MAX_OVERRUN_MINUTES = 15`（A plan may not run longer than the booked time plus this grace period.）
- `MIN_TRANSFER_MINUTES = 20`（Minimum buffer between two different shooting locations.）
- `MIN_SHOOT_BLOCK_MINUTES = 60`（A shoot block shorter than this is not worth setting up lights for. This is the CEILING: with time to spare, a block should be at least this long.）
- `STYLING_FLOOR_MINUTES = 60`
- `MIN_FEASIBLE_HOURS = 1.5`（Below this, makeup + one transfer + one shoot cannot fit at all.）
- `MIN_TRANSFER_COST_CNY = 50`（Minimum transport cost of any executable plan (shared by the offline planner).）
- `BUDGET_TOLERANCE_CNY = 0`（Hard ceiling: no plan may exceed the stated budget.）
- `MIN_ALTERNATIVES = 1`（A credible plan offers at least one fallback when weather or crowds interfere.）
- `MIN_SOURCE_REFS = 2`（Every generated plan must cite the knowledge entries it used.）

## 怎么把这个数变成量出来的

改一个阈值之前，先用命令开一条实验、写下预测，再跑：

```bash
python -m evals.experiment_log --open --area rules --question ... --expect ... --metric ...
```

跑完 `--close` 时带上 `--rules` 标记，`kb-health.md` 的「被实验检验过门槛的规则」会增加一。这个数字目前很小，它应该小——16 条规则里只有两条的门槛被真正探过。
