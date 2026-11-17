# ADR 0016 · 引用必须指向真实存在的知识库条目

- 状态：已采纳
- 日期：2026-11-13
- 相关：[0003 精确检索优先于向量库](0003-exact-retrieval-over-vectors.md)、[0014 请求约束是硬规则](0014-request-constraints-are-hard-rules.md)

## 背景

`source_refs` 是方案可溯源性的核心字段。此前校验器只检查引用数量（`NO_SOURCE_REF`），不检查引用是否真实存在。

结果是：模型可以写 `locations.json#invented_palace` 或 `styles.json#no_such_style`，仍然拿到 `traceability` 维度的分数。这比「没有引用」更糟——它把伪造的溯源伪装成证据。

## 决策

1. 新增硬规则 `UNKNOWN_SOURCE_REF`：`plan.source_refs` 与每个时间块的 `source_refs` 必须满足：
   - `locations.json#<id>` 且该 id 在当前方案城市的知识库中存在；
   - `styles.json#<id>` 且该 id 在风格知识库中存在。
2. 其他前缀、空 id、跨城市地点都算未知引用。
3. 未知引用是硬失败：一份带伪造引用的方案不可交付，不能只扣软分。
4. 测试覆盖「伪造引用被拦截」与「真实引用被接受」两条路径。

## 后果

- 规则总数 14 → 15（10 硬 + 5 软）。
- 离线 `good` provider 的引用本来就来自知识库，分数不受影响；`naive` 没有引用，仍由 `NO_SOURCE_REF` 记录，不会新增失败记录。
- 失败案例库不需要新增记录；它只收集硬约束失败，而当前对照组没有伪造引用。

## 2026-11-16 实现补充

真实模型会偶发编造引用。流水线在 `validate()` 之前调用 `grounding.sanitize_source_refs()`：能解析的引用原样保留，不能解析的丢弃，并把 `dropped_source_refs` 写入 `validate` 阶段的 trace。

这没有取消硬规则：直接调用 `validate()` 时，`UNKNOWN_SOURCE_REF` 仍然硬失败；流水线只是选择「把伪造降级为缺失（soft `NO_SOURCE_REF`）」，避免一条坏指针让整份方案不可交付。丢弃是可见的，不再静默。

## 没有采纳的方案

- **只把引用当字符串展示**：无法阻止模型编造来源，和「可溯源」的主张冲突。
- **用软规则扣分**：伪造证据是可信度问题，不是质量瑕疵。
- **在 UI 层隐藏未知引用**：掩盖生成端的问题，也会让评测看不到真实失败。
