# 运维与排错手册

## 命令速查

| 想做什么 | 命令 |
| --- | --- |
| 生成一份方案 | `python -m shotplan.cli plan` |
| 输出 JSON 并存文件 | `python -m shotplan.cli plan --json --out result.json --persist` |
| 看全部规则 | `python -m shotplan.cli rules` |
| 看当前配置（隐藏密钥） | `python -m shotplan.cli config` |
| 看运行历史 | `python -m shotplan.cli history --limit 10` |
| 跑测试 | `python -m pytest` |
| 跑评测并出报告 | `python -m evals.run_eval --write-report` |
| 静态检查 | `python -m ruff check src tests evals` |
| 启动 Web | `python -m shotplan.cli serve --port 8000` |
| 一条命令全验证 | `make smoke` |

没有 `make` 的 Windows 环境，直接按顺序跑 `python -m pytest`、`python -m evals.run_eval --write-report`、`python -m shotplan.cli plan`。

## 常见故障

**`ModuleNotFoundError: No module named 'shotplan'`**
包在 `src/` 下。二选一：`python -m pip install -e ".[api,dev]"`，或临时设置 `PYTHONPATH=src`（PowerShell：`$env:PYTHONPATH="src"`）。

**`serve 需要可选依赖`**
执行 `python -m pip install -e ".[api]"`。核心功能不受影响。

**`SHOTPLAN_LLM_API_KEY is empty`**
`.env` 里把 `SHOTPLAN_PROVIDER` 设成了 `openai_compatible` 但没填 key。改用 `SHOTPLAN_PROVIDER=mock`，或按 [USER_TASKS.md](USER_TASKS.md) 第 4 节配置。

**`unknown city` / `KnowledgeError`**
知识库目前只有 `xian`。检查 `--city` 拼写；要加城市就往 `knowledge/data/locations.json` 增加一个 city 对象。

**`budget ... cannot cover even the cheapest styling`**
预算过低（低于最便宜妆造 300 元）。这是已知限制，路线图阶段 4 会做成显式 `INFEASIBLE` 分支。目前用 `--budget 500` 以上即可。

**评测退出码 1**
看输出里的 `REGRESSION:` 段落。三种可能：均分低于阈值、硬约束通过率低于阈值、有用例未达预期。用 `evals/out/latest.json` 查具体是哪个用例、触发了哪些问题码。不要直接调低阈值，先定位原因。

**`measurement is broken`**
对照组均分追上了正式方案。说明打分器或规则被改坏了，属于严重回归，不要用提高阈值的方式绕过。

**页面打开但点「生成方案」报错**
先看终端里 serve 是否还在运行，再访问 `http://127.0.0.1:8000/api/health`。若用 `file://` 直接打开 `web/index.html`，浏览器无法请求相对路径的 API，必须通过 serve 访问。

**`docs/eval-report.md` 被改动**
这个文件由代码生成，但**会随仓库提交**（招聘方不必跑代码就能看到结论）。要改内容请改 `evals/run_eval.py::build_report` 后重跑 `--write-report`，再把更新后的报告一起提交。`evals/out/latest.json` 才是被忽略的原始产物。

**`git push` 报 `CONNECT tunnel failed, response 502` 或 `schannel: missing close_notify`**
本机网络只放行 `api.github.com`，`github.com` 的 CONNECT 隧道被代理拒绝（`curl https://github.com` 也会超时），但 REST API 正常。先试 `git -c http.version=HTTP/1.1 push`；仍失败就换网络或关闭代理。无法改网络时，用仓库自带的等价通道：

```bash
python scripts/push_via_api.py --dry-run   # 只看计划，不写远程
python scripts/push_via_api.py             # 推送当前 HEAD 到 origin 当前分支
```

脚本只用标准库，通过 Git Data API 重建 commit：blob / tree / commit 的 SHA 会与本地逐一校验一致，因此远程与本地不会分叉；token 从 `git credential fill` 读取，不打印、不落盘。它要求远程 HEAD 正好是本地 HEAD 的父提交（fast-forward），否则会拒绝执行。

## 数据位置

- 运行历史：`data/shotplan.db`（SQLite，已在 `.gitignore`）
- 评测原始结果：`evals/out/latest.json`
- 基线快照：`evals/baselines/*.json`（需要提交，用于跨版本对比）
- 密钥：`.env`（**永不提交**，`config.py` 不会打印 key 值）

清空运行历史：删除 `data/shotplan.db` 即可，下次运行自动重建。

## 修改代码后的自检顺序

1. `python -m pytest`（规则没被改坏）
2. `python -m evals.run_eval --write-report`（质量没退化）
3. `python -m ruff check src tests evals`（风格）
4. `python -m shotplan.cli plan`（端到端冒烟）
5. `git status` 确认没有把 `.env` / `.db` / `evals/out/` 加进去
