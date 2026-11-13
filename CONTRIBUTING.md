# 贡献指南

这个仓库的每条改动都应该能被验证。以下是四类常见改动的标准流程。

## 本地准备

```bash
python -m pip install -e ".[api,dev]"
python -m pytest
python -m evals.run_eval --write-report
```

核心功能不需要安装，但改代码建议装全套，方便跑测试和界面。

## 加一条约束

1. 在 `src/shotplan/domain/constraints.py` 加常量和一条 `RULES` 记录（写清 `why`）。
2. 在 `src/shotplan/agents/validator.py` 实现检查，使用已有 code，区分 `hard` / `soft`。
3. 在 `tests/test_validator.py` 加**一个反例测试**：构造一份违反该规则的 plan，断言对应 code 出现。
4. 若影响评分，同步调整 `SCORE_WEIGHTS`（总和必须为 1）。
5. 跑 `python -m pytest`、`python -m evals.run_eval --write-report`、`python -m evals.mutation_lab --check`、`python -m evals.case_miner --check`、`python -m evals.http_load --check` 和 `python -m evals.worker_lab --check`，看哪些用例分数变化、哪些未见组合的判定变了，并在 PR 里说明。

不要为了让评测全绿而调低阈值。分数下降如果是合理的（例如新规则暴露了真实缺陷），应该在 PR 里解释，而不是掩盖。改了校验器或对照组 `naive` 的输出时，还要同步 `evals.scorers.CONTROL_RULE_MIN_COUNTS` 并新增一个变异体——门禁依赖这份「已知缺陷清单」，清单变了门禁必须跟着变（ADR 0009）。

## 加一个评测用例

在 `evals/cases/` 下对应分组的 `.jsonl` 加一行。用例描述的是**需求与约束**，不是期望输出。先跑一次看实际得分，再设 `expect_min_score`——阈值应反映该场景下合理的质量水平，不是「刚好能通过」。

## 加一个 provider

实现 `agents/base.py` 的 `PlanProvider` 协议（一个 `generate()` 方法，返回 `GenerationResult`），在 `agents/__init__.py::build_provider` 注册。注意：provider 负责生成，**不要**在 provider 里做质量判定，那是校验器的职责。

真实模型接入时记得把 `prompt_version` 正确写进 trace，否则运行记录无法归因。

## 改 prompt

递增 `agents/prompts.py` 的 `PROMPT_VERSION`，在 `PROMPT_CHANGELOG` 追加一条记录（版本、日期、改动、观测结果）。流程：

```bash
python -m evals.run_eval --save-baseline evals/baselines/before-change.json
# 改 prompt
python -m evals.run_eval --write-report --compare evals/baselines/before-change.json
```

分数下降就回滚或继续调整，不要直接合并。

## 代码风格

- Python 3.10+，类型标注用 `from __future__ import annotations` + 内置泛型。
- `python -m ruff check src tests evals` 必须通过（line-length 100）。
- 核心域不引入第三方依赖。新依赖只能加在 `[api]` 或 `[dev]` extra。
- 每个公开函数/类写清楚「为什么」，而不是复述「做了什么」。
- 提交信息用 `feat:` / `fix:` / `test:` / `docs:` / `refactor:` 前缀。

## 提交前自检

```bash
python -m pytest
python -m evals.run_eval --write-report
python -m ruff check src tests evals
python -m shotplan.cli plan
git status    # 确认没有 .env / .db / evals/out 被提交
```
