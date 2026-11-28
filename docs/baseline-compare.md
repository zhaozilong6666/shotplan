# 和 v7 真实模型基线做配对比较

v7 基线是 **2026-11-27 的单次 80 例快照**，不是三轮重复：
`evals/baselines/v7-real-2026-11-27.json`。单次快照可以回答“有没有大改动”，
不能单独证明 1–2pp 的小差异；`measurement.md` 已量过 3 轮重跑的得分极差 1.38pp。

## 命令

```bash
# B 指向新的真实模型快照（同样 80 例、同样 prompt）
make compare-v7 B=evals/out/real-candidate.json
```

等价于：

```bash
python -m evals.pair_compare \
  --a evals/baselines/v7-real-2026-11-27.json \
  --b evals/out/real-candidate.json \
  --regression-margin-pp 3
```

## 怎么读

- **逐例配对**：同一 case id 的 B−A，消掉题目难度；不是拿两个总通过率相减。
- **bootstrap 95% 区间**：5000 次重采样，固定种子；区间跨 0 时不写“略好”。
- **非劣判定**：`--regression-margin-pp 3` 只检查“能否排除下降超过 3pp”；
  `ci_low > -3pp` 才通过。区间太宽、包含大幅下降，即使点估计持平也会判失败。
- 退出码：0 = 非劣通过；1 = 未通过；2 = 输入/用例集不一致。

## 边界

- v7 是**单次快照**；非劣通过只说明“没有证据表明下降超过 margin”，不是“完全没下降”。
- 要证明更小的差异，用 `--repeat N` 生成带每例均值的快照（`pair_compare` 支持），
  或扩大样本；不要缩小 margin 硬凑。
- v7 的 `persist_mode=off`、`pipeline_budget_s=180`、`max_repairs=1`、模型
  `deepseek-v4.1-flash-1017` 都写在 `effective_config`；换模型/预算后不能直接沿用 v7。
