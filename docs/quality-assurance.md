# 质量边界：哪些有保证，哪些没有

> 结论先说：**确定性核心有自动门禁，真实模型/生产部署/长跑没有“绝对保证”。**
> 这里列清楚每一条承诺由什么机械检查支撑，避免把“测试绿”说成“不会错”。

## 自动执行、失败即红

| 承诺 | 证据 | 执行位置 |
| --- | --- | --- |
| 678 个单测通过，覆盖率 ≥85% | `pytest --cov-fail-under=85`，当前 **91.63%** | CI × Python 3.10–3.13；Windows py3.12 另跑 unit + HTTP smoke |
| 40 道质量门禁 | `make gates`，唯一清单在 Makefile | CI py3.12，25 分钟超时 |
| 类型与风格 | `ruff check src tests evals scripts` + `mypy` | CI 每个 Python 版本 |
| 80/80 主集 + 320/320 盲测 | `evals.run_eval` / `case_miner --check` | `make gates` |
| 门禁自身有效 | 9 处受控变异，9/9 被拦截 | `evals.mutation_lab --check` |
| 垃圾输入不崩 | 1000 条模糊输入，0 未捕获异常 | `evals.fuzz --check` |
| 文档不漂移 | `check_doc_claims` / `kb --check` / `audit_reports` / 生成物 `git diff` | CI |

## 没有自动保证

- **真实模型质量**：已有 2026-11-27 完整 80 例实测（67/69 硬通过、均分 0.9158、¥5.89），但不在每次 CI 重跑；
  v7 是单次快照，回归用 `make compare-v7` 的逐例配对非劣检验（[口径](baseline-compare.md)），不能拿总通过率相减。
- **长跑稳定性**：5 小时/120 分钟报告是本地实测，不在 CI 重跑；它是证据，不是每次提交的保证。
- **Windows 多进程**：已加 Windows py3.12 的 lint/type/unit/HTTP smoke job；但 `uvicorn --workers N` 的 30s accept 长跑仍不在 CI 重跑，默认仍是 1 worker。
- **Linux 部署**：已用 `scripts/edge_proxy.py` 本地验证 4 后端 + 边缘代理（30 分钟 34,113 请求 0 错误）；nginx/systemd 配方仍未在 Linux 执行，CI 不启动真实反向代理。
- **前端 E2E**：没有浏览器级端到端测试；UI 由 API 测试和手工 smoke 覆盖。
- **绝对正确性**：规则/知识库仍有边界，覆盖率不是正确性证明。

## 怎么复核

```bash
python -m ruff check src tests evals scripts
python -m mypy
python -m pytest --cov=src/shotplan --cov-fail-under=85
make gates          # Linux/macOS；本机 Git Bash 没有 make 时逐条跑 Makefile
```

如果这些命令全绿，能保证的是：**上述确定性契约没有被本次改动破坏**。
不能保证的是：真实模型效果、生产网络、OS 调度和未覆盖的输入组合。
