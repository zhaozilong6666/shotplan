# 运维与排错手册

## 命令速查

| 想做什么 | 命令 |
| --- | --- |
| 生成一份方案 | `python -m shotplan.cli plan` |
| 看一个不可满足需求如何被拒绝 | `python -m shotplan.cli plan --budget 100`（退出码 3） |
| 输出 JSON 并存文件 | `python -m shotplan.cli plan --json --out result.json --persist` |
| 看全部规则 | `python -m shotplan.cli rules` |
| 看当前配置（隐藏密钥） | `python -m shotplan.cli config` |
| 看运行历史 | `python -m shotplan.cli history --limit 10` |
| 跑测试 | `python -m pytest` |
| 跑评测并出报告 | `python -m evals.run_eval --write-report` |
| 与当前基线对比 | `python -m evals.run_eval --compare evals/baselines/v6-naive.json` |
| 记录指定 prompt 版本 | `python -m evals.run_eval --prompt-version planner-v1 --write-report` |
| 验证门禁本身有效 | `python -m evals.mutation_lab --check` |
| 重刷变异报告与快照 | `python -m evals.mutation_lab --write` |
| 跑修复循环实验 | `python -m evals.repair_lab --write-report` |
| 采集失败案例库 | `python -m evals.collect_failures --write-report` |
| 验证盲测挑战集 | `python -m evals.case_miner --check` |
| 真实 HTTP 服务压测门禁 | `python -m evals.http_load --check` |
| 重刷 HTTP 压测矩阵与报告 | `python -m evals.http_load --write` |
| 重刷盲测挑战集与报告 | `python -m evals.case_miner --write` |
| 看运行统计 | `python -m shotplan.cli stats --limit 50` |
| 静态检查 | `python -m ruff check src tests evals` |
| 启动 Web | `python -m shotplan.cli serve --port 8000` |
| 一条命令全验证 | `make smoke` |

没有 `make` 的 Windows 环境，直接按顺序跑 `python -m pytest`、`python -m evals.run_eval --write-report`、`python -m evals.case_miner --check`、`python -m evals.http_load --check`、`python -m shotplan.cli plan`。

## 常见故障

**`ModuleNotFoundError: No module named 'shotplan'`**
包在 `src/` 下。二选一：`python -m pip install -e ".[api,dev]"`，或临时设置 `PYTHONPATH=src`（PowerShell：`$env:PYTHONPATH="src"`）。

**`serve 需要可选依赖`**
执行 `python -m pip install -e ".[api]"`。核心功能不受影响。

**`SHOTPLAN_LLM_API_KEY is empty`**
`.env` 里把 `SHOTPLAN_PROVIDER` 设成了 `openai_compatible` 但没填 key。改用 `SHOTPLAN_PROVIDER=mock`，或按 [USER_TASKS.md](USER_TASKS.md) 第 4 节配置。

**`unknown city` / `KnowledgeError`**
知识库目前有 `xian` / `luoyang` / `hangzhou` / `suzhou` / `nanjing` 五座城市。走 `run_plan()` 时未知城市会变成结构化 `INFEASIBLE`（`UNSUPPORTED_CITY`），不是异常；若直接调 `KnowledgeTools.city()` 才会抛错。要加城市就往 `knowledge/data/locations.json` 增加一个 city 对象，并同步补用例与基线。

**`budget ... cannot cover the cheapest styling plus transfer`**
预算连「最简妆造 300 元 + 转场 50 元」都覆盖不了（低于 350 元）。通常不会看到这个异常：生成前的 `check_feasibility()` 会先返回结构化 `INFEASIBLE`。若直接调用了 `best_style_for()` 才可能触发。用 `--budget 400` 以上即可，或看拒绝结果里的原因与建议。

**CLI 退出码 3**
不是崩溃，是需求不可满足。输出里会列出 `code / 缺口 / 建议` 与理论下限。退出码约定：`0` 成功、`2` 方案未过硬约束、`3` 不可行、`1` 其它错误。API 返回 HTTP 200 但 `status=infeasible`，Web 渲染专门的不可行视图。

**评测退出码 1**
看输出里的 `REGRESSION:` 段落。可能原因：均分低于阈值、硬约束通过率低于阈值、有用例未达预期、对照组均分追上正式方案、两组差值过小、对照组分数/通过率异常升高，或某条已知缺陷不再被报出。用 `evals/out/latest.json` 查具体是哪个用例、触发了哪些问题码。不要直接调低阈值，先定位原因——C24 就是这样逼出了一次真实的实现修复。

**`measurement is broken` / `a validator rule appears to be disabled`**
对照组追上正式方案，或校验端某条规则不再命中已知缺陷。说明打分器或规则被改坏了，属于严重回归，不要用提高阈值的方式绕过。可以先跑 `python -m evals.mutation_lab --check` 确认门禁自身是否仍然有效。

**`MUTATION GATE FAILED`**
两种可能：出现了存活变异（注入的故障没被门禁拦住，说明门禁被削弱），或 `docs/mutation-report.md` / `evals/mutation.json` 过期。前者要修门禁，不要改变异体；后者执行 `python -m evals.mutation_lab --write` 重刷，再跑一次 `python -m evals.run_eval --write-report`（评测报告会引用变异快照）。

**`CASE-MINING GATE FAILED`**
先看是「artifact is stale」还是不变量失败。前者说明改了校验器 / 知识库 / provider 却没重刷产物，执行 `python -m evals.case_miner --write` 后重新提交三份文件；后者要读具体原因：「negative controls disappeared」表示生成前可行性判定被削弱，25 个矛盾输入不再被挡下；「accepted challenge cases」或「min good/naive gap」表示候选区分度退化；「challenge set contains briefs copied」表示去重逻辑失效。不要通过调低阈值绕过——挑战集变少本身就是回归信号。

**`HTTP-LOAD GATE FAILED`**
若提示 `could not run the service`，先看本机 `HTTP_PROXY` / `HTTPS_PROXY`：代理会把 loopback 请求改写成绝对路径（`GET http%3A//127.0.0.1...`），除了 `/api/health` 所有端点都会 404。压测客户端已经设了 `trust_env=False`，如果有人删掉它就会复现这个现象。若提示 `not persisted`，说明并发下 SQLite 丢写；若提示 `exceeds the loose ceiling`，说明服务挂住而不是「机器慢」——3000ms 只是防挂阈值，不要调它来掩盖真实性能问题。

**`--compare` 显示巨大差值**
先确认基线是不是同一批用例。`v1-naive.json` 是 12 用例时代的历史快照，当前 80 用例必须比 `v6-naive.json`。新增用例后要重刷基线，否则用例集变化会被误读成质量变化。

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

1. `python -m pytest`（规则、可行性与边界没被改坏）
2. `python -m evals.run_eval --write-report`（质量没退化，用例没失效）
3. `python -m evals.mutation_lab --check`（门禁本身仍有效，且产物未过期）
4. `python -m evals.case_miner --check`（未见组合上的判定链仍一致，且产物未过期）
5. `python -m evals.http_load --check`（真实服务 0 错误、0 丢写）
6. `python -m ruff check src tests evals scripts`（风格）
7. `make gates` 跑完 CI 的全部 13 道质量门禁（含覆盖率、模糊测试、盲测、真实 HTTP、并发、扩展性与展示页一致性）
8. `python -m shotplan.cli plan` 与 `python -m shotplan.cli plan --budget 100`（正常路径 + 拒绝路径冒烟）
9. `git status` 确认没有把 `.env` / `.db` / `evals/out/` 加进去
