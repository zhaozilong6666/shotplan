# 失败案例库

这份清单不是 bug 列表，而是**校验器真实抓到过的错误模式**。
它回答一个面试里常被追问的问题：「你怎么知道你的规则不是拍脑袋定的？」

当前共 **437** 条记录，来自 `naive` 对照 provider 在 **60** 个可满足 brief 上的输出，覆盖 **4** 类硬约束违规。

## 按问题码汇总

| 问题码 | 触发次数 | 涉及用例 | 典型修复建议 |
| --- | ---: | ---: | --- |
| `TRANSFER_BUFFER` | 171 | 60 | 在两段拍摄之间插入一个 travel 时间块 |
| `UNKNOWN_LOCATION` | 150 | 60 | 换成知识库里真实存在的地点 id |
| `BUDGET_EXCEEDED` | 60 | 60 | 减少收费景点、改用免费场景或降低妆造套餐档位 |
| `TIME_OVERRUN` | 56 | 56 | 删减一个拍摄点，或缩短妆造/拍摄时长 |

## 典型案例

### `TRANSFER_BUFFER`

- 用例：`C06`（conflict）  ｜  该次得分 `0.19`
- 判定：从 big_wild_goose_pagoda 到 nonexistent_palace 只留了 0 分钟转场
- 期望：>= 20 min
- 修复建议：在两段拍摄之间插入一个 travel 时间块

输入 brief：

```json
{"budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 3, "preferences": ["时间紧"], "styles": ["tang"]}
```

观察到的输出（声明总计 3000 元，时间块前 3 条）：

```json
[
  {
    "start": "08:00",
    "end": "08:30",
    "activity": "makeup",
    "location_id": "indoor_studio",
    "cost_cny": 0
  },
  {
    "start": "08:30",
    "end": "09:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  },
  {
    "start": "09:00",
    "end": "10:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  }
]
```

### `UNKNOWN_LOCATION`

- 用例：`C06`（conflict）  ｜  该次得分 `0.19`
- 判定：引用了知识库中不存在的地点：'nonexistent_palace'
- 期望：candidate_locations 中的 id
- 修复建议：换成知识库里真实存在的地点 id

输入 brief：

```json
{"budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 3, "preferences": ["时间紧"], "styles": ["tang"]}
```

观察到的输出（声明总计 3000 元，时间块前 3 条）：

```json
[
  {
    "start": "08:00",
    "end": "08:30",
    "activity": "makeup",
    "location_id": "indoor_studio",
    "cost_cny": 0
  },
  {
    "start": "08:30",
    "end": "09:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  },
  {
    "start": "09:00",
    "end": "10:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  }
]
```

### `BUDGET_EXCEEDED`

- 用例：`C06`（conflict）  ｜  该次得分 `0.19`
- 判定：总花费 3000 元超出预算 1500 元
- 期望：<= 1500 CNY
- 修复建议：减少收费景点、改用免费场景或降低妆造套餐档位

输入 brief：

```json
{"budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 3, "preferences": ["时间紧"], "styles": ["tang"]}
```

观察到的输出（声明总计 3000 元，时间块前 3 条）：

```json
[
  {
    "start": "08:00",
    "end": "08:30",
    "activity": "makeup",
    "location_id": "indoor_studio",
    "cost_cny": 0
  },
  {
    "start": "08:30",
    "end": "09:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  },
  {
    "start": "09:00",
    "end": "10:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  }
]
```

### `TIME_OVERRUN`

- 用例：`C06`（conflict）  ｜  该次得分 `0.19`
- 判定：总时长 660 分钟，超出可用时长 180 分钟
- 期望：<= 195 min
- 修复建议：删减一个拍摄点，或缩短妆造/拍摄时长

输入 brief：

```json
{"budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 3, "preferences": ["时间紧"], "styles": ["tang"]}
```

观察到的输出（声明总计 3000 元，时间块前 3 条）：

```json
[
  {
    "start": "08:00",
    "end": "08:30",
    "activity": "makeup",
    "location_id": "indoor_studio",
    "cost_cny": 0
  },
  {
    "start": "08:30",
    "end": "09:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  },
  {
    "start": "09:00",
    "end": "10:00",
    "activity": "shoot",
    "location_id": "big_wild_goose_pagoda",
    "cost_cny": 0
  }
]
```

## 这份清单怎么用

1. **新增规则前**：先在这里找证据。没有真实失败案例支撑的规则，多半是过度设计。
2. **接入真实模型后**：把模型犯的新错误按同样字段追加到 `evals/failures/cases.jsonl`，再补一条规则与一个评测用例。这样每条规则都能追溯到一次真实失败。
3. **回归时**：改动校验器后重跑采集，若某类问题突然消失，要确认是修复生效还是规则失效——对照组仍在，所以「问题消失」本身也需要解释。

## 边界

当前记录全部来自**故意犯错的离线对照组**，用于验证判定链与文档格式；它们不代表真实模型的错误分布。真实模型失败案例待阶段 4 接入后补充。

## 复现

```bash
python -m evals.collect_failures --write-report
```

`evals/failures/cases.jsonl` 与本文件均由代码生成，请勿手工编辑。
