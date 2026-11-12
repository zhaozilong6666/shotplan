# ADR 0001 · 核心域不依赖任何第三方库

- 状态：已接受
- 日期：2026-11-12

## 背景

项目要放到 GitHub 上供招聘方查看。最常见的情况是：对方点开仓库、clone 下来、发现要装一堆依赖和配置 API key，然后放弃。同时，评测和 CI 也不应该因为没有凭证就跑不起来。

## 决策

`domain/`、`knowledge/`、`agents/`、`pipeline.py`、`tracing.py`、`storage.py`、`cli.py` **只用 Python 标准库**。数据模型用 `dataclasses` 而非 pydantic，HTTP 调用用 `urllib` 而非官方 SDK，配置用 `os.environ` 加一个 20 行的 `.env` 读取器，持久化用 `sqlite3`。

框架级能力放在可选层：`api.py` 需要 `[api]` extra，测试需要 `[dev]` extra。

## 理由

- `git clone` 后 `python -m shotplan.cli plan` 与 `python -m evals.run_eval` 立即可跑，这是「能交付」的最直接证据。
- 核心规则不依赖 pydantic 的校验行为，规则语义完全由自己的代码定义，可测试、可解释。
- 依赖越少，半年后还能跑起来的概率越高。

## 后果

- 正向：CI 无需密钥；离线演示可靠；分层边界清晰。
- 负向：要自己写 JSON 解析容错、类型校验和 HTTP 重试；没有 pydantic 那种开箱即用的请求 schema 与自动生成文档。
- 缓解：把解析逻辑集中在 `planner.py` 这一个信任边界，API 层用 FastAPI 自带的能力处理 HTTP 层校验。

## 备选方案

全部用 pydantic + FastAPI（更常规，但核心与框架耦合）；或用 LangChain 等 Agent 框架（更快搭起来，但把校验、重试、trace 都藏进框架，恰好掩盖了本项目想展示的部分）。
