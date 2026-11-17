# 多轮追问能力实测

用例 **12** 条，一次通过 **5** 条（**42%**）。

本页由 `evals/multi_turn_lab.py` 生成。被测对象是 `followup.py` 的确定性规则，不调用任何模型，因此结果可复现、可归因：失败即规则缺口。

## 逐例结果

| 用例 | 类型 | 结果 | 说明 |
| --- | --- | --- | --- |
| M01 | shorter | **失败** | 用「砍」表达缩短，动词不在白名单里 |
| M02 | cheaper_amount | **失败** | 带金额的降价，应该算出新预算而不是只记一条偏好 |
| M03 | city_switch | 通过 | 换城市时其余条件必须原样继承，不能重置 |
| M04 | style_replace | **失败** | 否定并替换风格，styles 要真的换掉 |
| M05 | compound | 通过 | 两件事一起调，对照组：白名单本来就能过 |
| M06 | absolute_budget | **失败** | 直接给数字，没有「多/加」这类动词 |
| M07 | implicit_deadline | **失败** | 赶车是隐含的结束时间约束，不是一句闲聊 |
| M08 | cheaper_vague | **失败** | 模糊降价，没有给金额 |
| M09 | unrecognised | 通过 | 守门用例：说不清的话不能被瞎猜成某个字段 |
| M10 | inherit | **失败** | 先降价再放松，第一轮的预算必须留在第二轮里 |
| M11 | revert | 通过 | 改了又改回去，最后一轮的城市说了算 |
| M12 | ceiling | 通过 | 已经到 12 小时上限，再说「再多」不能突破也不能报错 |

## 失败明细

### M01 · 用「砍」表达缩短，动词不在白名单里

- 时间砍一点吧，4 个小时够了  ->  （未识别）
- **hours: 期望 4，实际 6.0**
- **最后一轮识别：期望 识别，实际 拒答**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": [], "styles": ["tang"]}`

### M02 · 带金额的降价，应该算出新预算而不是只记一条偏好

- 能不能便宜 300  ->  （未识别）
- **budget_cny: 期望 1200，实际 1500**
- **最后一轮识别：期望 识别，实际 拒答**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": [], "styles": ["tang"]}`

### M04 · 否定并替换风格，styles 要真的换掉

- 不要唐制了，换宋制  ->  （未识别）
- **styles: 期望 ['song']，实际 ['tang']**
- **最后一轮识别：期望 识别，实际 拒答**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": [], "styles": ["tang"]}`

### M06 · 直接给数字，没有「多/加」这类动词

- 预算两千吧  ->  （未识别）
- **budget_cny: 期望 2000，实际 1500**
- **最后一轮识别：期望 识别，实际 拒答**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": [], "styles": ["tang"]}`

### M07 · 赶车是隐含的结束时间约束，不是一句闲聊

- 我得赶下午五点的高铁  ->  （未识别）
- **preferences 缺 '17:00前结束'（实际 []）**
- **最后一轮识别：期望 识别，实际 拒答**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": [], "styles": ["tang"]}`

### M08 · 模糊降价，没有给金额

- 太贵了，预算降一点  ->  （未识别）
- **budget_cny: 期望 1200，实际 1500**
- **最后一轮识别：期望 识别，实际 拒答**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": [], "styles": ["tang"]}`

### M10 · 先降价再放松，第一轮的预算必须留在第二轮里

- 预算降到 800  ->  （未识别）
- 再松一点  ->  增加偏好：轻松、少转场
- **budget_cny: 期望 800，实际 1500**

最终 brief：`{"avoid": [], "budget_cny": 1500, "city": "xian", "date": "2026-10-04", "hours": 6.0, "must_include": [], "party_size": 1, "preferences": ["轻松", "少转场"], "styles": ["tang"]}`


## 口径

- 一条用例通过 = `expect` 里每个字段都对，且最后一轮的「识别/拒答」符合预期。
- 中间轮次不单独计分，只要求最终状态正确：多轮的价值在于收敛，不在于每句都改对。
- 拒答也是正确答案之一：说不清的需求被硬塞进某个字段，比让用户改表单更糟。
