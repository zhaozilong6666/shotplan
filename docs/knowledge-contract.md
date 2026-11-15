# 知识库数据契约

> 由 `python scripts/validate_knowledge.py --check` 校验。
> **本文件不含新城市数据**：扩到 10 城需要人工核验票价、开放时间与光线条件，
> 编造地名与价格比不扩展更糟。这里只定义「什么才算一条合格记录」。

## 当前状态

- 5 座城市、40 个地点、5 种风格
- 每条地点记录必须有 14 个字段，每种风格 11 个字段
- 校验结果：**0 项问题**（全部通过）

## 一条合格记录必须满足

| 约束 | 原因 |
| --- | --- |
| `source` 非空 | 没有出处的价格无法复核 |
| `last_verified_at` 是合法且非未来的 ISO 日期 | 与 `check_knowledge_freshness.py` 的 180 天门禁配套 |
| `id` 全库唯一 | 重复 id 会让 `location()` 返回错误地点 |
| `best_light` 是 `morning/golden/night/any` 的非空子集 | 校验器的 `LIGHT_MISUSE` 直接读它 |
| `indoor` 是布尔 | 决定雨天备选与光线规则是否适用 |
| `min_minutes > 0`，`travel_min_from_center >= 0`，`cost_cny >= 0` | 时间轴与预算算术的输入 |
| `crowd` / `weather_risk` ∈ low/medium/high | 排序与备选方案的枚举值 |
| 风格 `price_cny = [min, max]` 且 min ≤ max | 预检用它算最低可执行成本 |

## 扩城流程（需要真人核验）

1. 每个候选地点记录：官方名称、区县、门票/预约价、建议停留时长、
   从市中心交通时间、最佳光线、室内/室外、客流与天气风险。
2. 为每条记录写 `source`（可复核的出处）与 `last_verified_at`（核验当天）。
3. 运行 `python scripts/validate_knowledge.py`，再运行 `make gates`。
4. 扩展后必须重跑：`evals/run_eval`（80 用例）、`evals/case_miner`（盲测集覆盖）、
   `evals/slo_baseline`（延迟/成本基线可能变化）。
5. 新城市不能只加进 `locations.json` 就算完成：风格标签、候选检索、
   转场时间估算、`light_window()` 的季节表都要一起复核。

## 为什么这一项仍不能勾掉

阶段 8 的验收是「城市从 5 扩到 10，每条记录带 source 与 last_verified_at」。
工具链能保证**格式与可复核性**，但保证不了**数据是真的**。
在拿到可核验的票价与光线资料之前，写满 10 座城市只会把 5 座可信数据稀释成 10 座存疑数据。
