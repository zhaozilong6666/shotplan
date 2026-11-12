# 真实输出示例

> 由 `python -m evals.examples --write` 生成，请勿手工编辑。
> 每个示例都是**实际跑一遍流水线**的结果；只展示确定性字段，所以本文件在任何机器上都应逐字节一致。

四个示例分别覆盖：正常需求、带排除约束的需求、时长上限、以及**数学上不可满足**的需求。
看最后一节：没有时间块，只有原因、缺口与建议——那正是「拒绝」比「硬凑」更有用的地方。

## 西安 · 唐制 · 6 小时 · 1500 元

**输入 brief**

```json
{
  "avoid": [],
  "budget_cny": 1500,
  "city": "xian",
  "date": "2026-10-04",
  "hours": 6.0,
  "must_include": [],
  "party_size": 1,
  "preferences": [],
  "styles": [
    "tang"
  ]
}
```

**输出：可执行方案**

| 时间 | 环节 | 地点 | 花费 | 说明 |
| --- | --- | --- | ---: | --- |
| 12:00–14:05 | makeup | `indoor_studio` | 400 元 | 含发型 90 分钟、着装 40 分钟；齐胸襦裙与大袖衫，配高髻与花钿；与唐风建筑最协调 |
| 14:05–14:25 | travel | `big_wild_goose_pagoda` | 50 元 | 按知识库估算至少 20 分钟，含补妆与器材搬运 |
| 14:25–16:02 | shoot | `big_wild_goose_pagoda` | 0 元 | 免费但人流密集，建议避开节假日正午；建议光线 16:00-18:00 |
| 16:02–16:22 | travel | `city_wall` | 0 元 | 按知识库估算至少 20 分钟，含补妆与器材搬运 |
| 16:22–18:00 | shoot | `city_wall` | 420 元 | 永宁门段最出片，清晨人少；城墙上风大，注意发型固定；建议光线 16:00-18:00 |

妆造：**唐制**，发型 125 分钟、着装 40 分钟。
总花费：**870 元**（预算 1500 元）。

备选方案：
- `rain`：遇雨或强风改至室内古风影棚，光线可控（`indoor_studio`）
- `crowd`：客流超预期时改去广仁寺，客流低且更从容（`guangren_temple`）

假设：
- 预约时长仅 6.0 小时，妆造压缩为 125 分钟（标准 130 分钟）
- 黄金光线按 2026-10-04 所在季节估算为 16:00-18:00

**校验结论**

- 结论：**硬约束通过**，得分 **0.9750**
- 分项：budget 1.000、completeness 1.000、constraints 1.000、light 0.833、traceability 1.000
- 问题清单：无

<details><summary>运行轨迹（步骤顺序，不含耗时）</summary>

1. `preflight`
2. `retrieve`
3. `generate`
4. `parse`
5. `validate`

</details>

## 苏州 · 明制 · 排除两个最热门地点

**输入 brief**

```json
{
  "avoid": [
    "humble_administrator_garden",
    "pingjiang_road"
  ],
  "budget_cny": 1300,
  "city": "suzhou",
  "date": "2026-10-04",
  "hours": 6.0,
  "must_include": [],
  "party_size": 1,
  "preferences": [],
  "styles": [
    "ming"
  ]
}
```

**输出：可执行方案**

| 时间 | 环节 | 地点 | 花费 | 说明 |
| --- | --- | --- | ---: | --- |
| 12:00–13:55 | makeup | `indoor_studio_suzhou` | 400 元 | 含发型 80 分钟、着装 35 分钟；端庄立领与马面裙，适合古建筑与室内棚拍 |
| 13:55–14:15 | travel | `jinji_lake` | 50 元 | 按知识库估算至少 20 分钟，含补妆与器材搬运 |
| 14:15–15:57 | shoot | `jinji_lake` | 0 元 | 现代天际线与湖面，适合民国风与时尚混搭；建议光线 16:00-18:00 |
| 15:57–16:17 | travel | `canglang_pavilion` | 0 元 | 按知识库估算至少 20 分钟，含补妆与器材搬运 |
| 16:17–18:00 | shoot | `canglang_pavilion` | 320 元 | 最古老的苏州园林之一，客流低，适合安静取景；建议光线 16:00-18:00 |

妆造：**明制**，发型 115 分钟、着装 35 分钟。
总花费：**770 元**（预算 1300 元）。

备选方案：
- `rain`：遇雨或强风改至苏州室内影棚，光线可控（`indoor_studio_suzhou`）
- `crowd`：客流超预期时改去沧浪亭，客流低且更从容（`canglang_pavilion`）

假设：
- 黄金光线按 2026-10-04 所在季节估算为 16:00-18:00

**校验结论**

- 结论：**硬约束通过**，得分 **0.9587**
- 分项：budget 1.000、completeness 1.000、constraints 0.950、light 0.858、traceability 1.000
- 问题清单：
  - `STYLE_MISMATCH`（soft）风格 ming 与所选地点不匹配

<details><summary>运行轨迹（步骤顺序，不含耗时）</summary>

1. `preflight`
2. `retrieve`
3. `generate`
4. `parse`
5. `validate`

</details>

## 南京 · 民国风 · 12 小时上限 · 4500 元

**输入 brief**

```json
{
  "avoid": [],
  "budget_cny": 4500,
  "city": "nanjing",
  "date": "2026-10-17",
  "hours": 12.0,
  "must_include": [],
  "party_size": 1,
  "preferences": [],
  "styles": [
    "republic"
  ]
}
```

**输出：可执行方案**

| 时间 | 环节 | 地点 | 花费 | 说明 |
| --- | --- | --- | ---: | --- |
| 09:00–10:25 | makeup | `indoor_studio_nanjing` | 300 元 | 含发型 60 分钟、着装 25 分钟；妆造最快，预算紧张或时间紧时的稳妥选择 |
| 10:25–10:45 | travel | `laomendong` | 50 元 | 按知识库估算至少 20 分钟，含补妆与器材搬运 |
| 10:45–15:42 | shoot | `laomendong` | 0 元 | 青砖马头墙与巷弄纵深，夜间灯笼亦可拍；建议光线 16:00-18:00 |
| 15:42–16:02 | travel | `jiming_temple` | 0 元 | 按知识库估算至少 20 分钟，含补妆与器材搬运 |
| 16:02–21:00 | shoot | `jiming_temple` | 320 元 | 黄墙与药师塔，樱花季人流激增；注意拍摄许可；建议光线 16:00-18:00 |

妆造：**民国旗装**，发型 85 分钟、着装 25 分钟。
总花费：**670 元**（预算 4500 元）。

备选方案：
- `rain`：遇雨或强风改至南京室内影棚，光线可控（`indoor_studio_nanjing`）
- `crowd`：客流超预期时改去南京室内影棚，客流低且更从容（`indoor_studio_nanjing`）

假设：
- 黄金光线按 2026-10-17 所在季节估算为 16:00-18:00

**校验结论**

- 结论：**硬约束通过**，得分 **0.9975**
- 分项：budget 1.000、completeness 1.000、constraints 1.000、light 0.983、traceability 1.000
- 问题清单：无

<details><summary>运行轨迹（步骤顺序，不含耗时）</summary>

1. `preflight`
2. `retrieve`
3. `generate`
4. `parse`
5. `validate`

</details>

## 西安 · 预算 100 元（低于最低可执行成本）

**输入 brief**

```json
{
  "avoid": [],
  "budget_cny": 100,
  "city": "xian",
  "date": "2026-10-04",
  "hours": 6.0,
  "must_include": [],
  "party_size": 1,
  "preferences": [],
  "styles": [
    "tang"
  ]
}
```

**输出：结构化拒绝（`status=infeasible`）**

| 原因 | 说明 | 缺口 | 建议 |
| --- | --- | --- | --- |
| `BUDGET_BELOW_MINIMUM` | 预算 100 元低于最低可执行方案成本 350 元 | 还差 250 元 | 把预算提高到至少 350 元，或改用最简妆造套餐与免费场景 |

理论下限：预算 350 元、时长 1.5 小时、最低妆造 300 元、转场 50 元。

**为什么这样处理**：这类 brief 在数学上不可能满足，硬凑一份方案只会产出「看起来合理但无法执行」的行程。拒绝发生在调用 provider 之前，所以两个 provider 会给出同一个结论（见 [ADR 0007](adr/0007-feasibility-before-generation.md)）。

<details><summary>运行轨迹（步骤顺序，不含耗时）</summary>

1. `preflight`

</details>
