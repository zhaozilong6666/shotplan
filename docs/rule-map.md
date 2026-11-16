# 规则地图

> 由 `python scripts/build_rule_map.py --write` 从 `src/shotplan/domain/constraints.py`、失败库与测试生成。
> `--check` 会把它当门禁：规则加了但没更新文档，CI 会红。

每条规则要能回答三个问题：守的是什么、真的触发过吗、删掉它哪个测试会红。

| 规则 | 严重度 | 构造失败库 | 真实模型触发 | 覆盖测试 | 为什么存在 |
| --- | --- | ---: | ---: | --- | --- |
| `TIME_OVERLAP` | hard | 0 | 0 | `test_validator.py::test_time_overlap_is_hard`, `test_cli.py::test_rules_config_history_and_persistence` | 重叠的行程在现实中不可执行，是最常见的 LLM 幻觉 |
| `TIME_OVERRUN` | hard | 64 | 0 | `test_validator.py::test_time_overrun_is_hard`, `test_pipeline.py::test_naive_pipeline_fails_in_the_expected_ways` | 超出拍摄时长意味着要临时加钱或砍环节 |
| `MISSING_ACTIVITY` | hard | 0 | 0 | `test_validator.py::test_missing_activity_is_hard` | 汉服拍摄没有妆造就不成立 |
| `MAKEUP_ORDER` | hard | 0 | 0 | `test_validator.py::test_makeup_order_is_hard` | 顺序错误是自由文本生成的高频错误 |
| `TRANSFER_BUFFER` | hard | 198 | 0 | `test_validator.py::test_transfer_buffer_is_hard`, `test_pipeline.py::test_naive_pipeline_fails_in_the_expected_ways` | 西安景区之间转场 + 补妆实际需要 20 分钟以上 |
| `BUDGET_EXCEEDED` | hard | 69 | 0 | `test_validator.py::test_budget_exceeded_is_hard`, `test_validator.py::test_budget_uses_slot_cost_even_if_declared_total_is_low` | 预算是用户给的硬约束，不是建议 |
| `UNKNOWN_LOCATION` | hard | 186 | 0 | `test_validator.py::test_unknown_location_is_hard`, `test_validator.py::test_empty_travel_location_is_allowed` | 用于检测模型编造不存在的地点 |
| `MISSING_REQUIRED_LOCATION` | hard | 6 | 1 | `test_validator.py::test_missing_required_location_is_hard`, `test_validator.py::test_required_location_satisfied_by_a_shoot_block` | 用户明确要求的地点被忽略，方案即使自洽也不可交付 |
| `AVOIDED_LOCATION` | hard | 4 | 0 | `test_validator.py::test_avoided_location_is_hard`, `test_validator.py::test_unused_avoid_entry_is_not_an_issue` | 排除项是用户给的硬约束；只把它当检索提示，模型可以绕过 |
| `UNKNOWN_SOURCE_REF` | hard | 0 | 0 | `test_validator.py::test_unknown_source_ref_is_hard`, `test_validator.py::test_valid_source_refs_are_accepted` | 引用不存在的来源等于伪造溯源，比不引用更糟 |
| `SHOOT_TOO_SHORT` | soft | 0 | 5 | `test_timeline.py::test_a_physically_short_block_is_not_flagged`, `test_validator.py::test_soft_rules_fire` | 过短的拍摄块性价比低；但短预约在物理上装不下 60 分钟，门槛需随时长缩放 |
| `LIGHT_MISUSE` | soft | 0 | 26 | `test_light.py::test_the_validator_stops_flagging_morning_finish_briefs`, `test_light.py::test_pipeline_repairs_the_light_without_asking_the_model_again` | 光线是成片质量的主要变量 |
| `NO_ALTERNATIVE` | soft | 0 | 0 | `test_validator.py::test_soft_rules_fire` | 户外拍摄受天气与客流影响，必须有 Plan B |
| `NO_SOURCE_REF` | soft | 0 | 0 | `test_validator.py::test_soft_rules_fire` | 可溯源是判断输出是否可信的前提 |
| `STYLE_MISMATCH` | soft | 0 | 1 | `test_domain_portability.py::test_only_style_matching_is_declared_domain_specific`, `test_validator.py::test_soft_rules_fire` | 唐制配现代极简场景会显得违和 |

## 缺口

每条规则都至少有一个测试直接引用。

## 怎么读这两列触发次数

- **构造失败库**：由 `naive` planner 生成的坏方案里，这条规则抓到多少次（69 个用例，527 条记录）。
- **真实模型触发**：`evals/real-model.json` 里头约束通过率那一轮的统计；`0` 可能是没触发，也可能是该轮样本里没出现（69 例，检测不到低频问题）。

两列都不能当作「规则重要性」的排名：高频可能只是因为 `naive` 特别容易犯这个错。
