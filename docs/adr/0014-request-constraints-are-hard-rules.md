# ADR 0014 · 把 `must_include` / `avoid` 升级为硬规则

- 状态：已采纳
- 日期：2026-11-13
- 相关：[0002 校验器不经过模型](0002-validator-outside-model.md)、[0007 生成前可行性判定](0007-feasibility-before-generation.md)

## 背景

`must_include` 与 `avoid` 原本只是检索提示：`KnowledgeTools.context_for()` 会把 `avoid` 从候选里过滤掉，把 `must_include` 传给 prompt，但校验器里没有对应规则。

实测发现这会造成一个严重后果：请求「必去青龙寺」时，规则 provider 生成的方案里没有青龙寺，仍然 `hard_pass=True`、得分 0.975；评测集里的 C18 用例（描述就是「指定必去青龙寺」）照样计入 80/80。也就是说，用户明确提出的条件既没有被系统执行，也没有被评测测出来。

只把条件写进 prompt 不够：模型可以忽略，检索也可以因为标签不匹配而找不到。把用户的硬条件留在「建议」层，等于让「可执行」的定义漏掉一部分。

## 决策

1. 在 `constraints.py` 增加两条硬规则：
   - `MISSING_REQUIRED_LOCATION`：`must_include` 中的每个 id 必须出现在至少一个 `shoot` 时间块里；
   - `AVOIDED_LOCATION`：`avoid` 中的 id 不得出现在任何时间块里。
2. 生成前的可行性判定增加三个结构化拒绝：
   - `UNKNOWN_REQUIRED_LOCATION`：必去地点不在当前城市知识库；
   - `ALL_LOCATIONS_EXCLUDED`：整个城市的可用地点都被 `avoid` 排除；
   - 保留既有的 `CONFLICTING_CONSTRAINTS`。
3. 离线 `good` provider 也必须遵守同一约束：先从全城市地点中挑出必去地点，再补足其余拍摄点；预算裁剪只裁非必去地点，裁不下就让 `BUDGET_EXCEEDED` 如实报错，而不是悄悄丢掉用户要求。
4. 评测用例 C09 修正：它声称「排除全部外景、回退室内」，却把唯一的室内棚也排除了，属于用例数据与意图不一致。

## 后果

- 规则总数 12 → 14（9 硬 + 5 软），失败案例库新增 `MISSING_REQUIRED_LOCATION` / `AVOIDED_LOCATION` 两类记录。
- 规则 provider 在 9 个带 `must_include` 的用例上现在真的会包含必去地点；naive 对照组因忽略这些条件而得分进一步下降。
- 妆造地点也必须避开 `avoid`：室内棚被排除时退到第一个允许的外景集合点，并在方案假设里说明。

## 没有采纳的方案

- **只改 prompt**：无法验证，模型可以忽略；与「校验不交给模型」的核心主张冲突。
- **把两条规则设为软规则**：用户说「必须」时，软规则只会扣分，不会判不可交付。
- **在 API 层过滤输出**：过滤会掩盖生成端的问题，也会让评测看不到真实失败。
