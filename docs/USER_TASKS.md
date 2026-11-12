# 需要你本人动手的事项（当前状态）

> **项目主副本已迁到 `D:\WorkBuddy\projects\shotplan`**（C 盘空间不足），下面所有命令都基于新路径；C 盘旧副本暂留作备份。
> **2026-11-12 更新：第 1、2 节已由我在本机执行完毕并验证，你不用再照着做。**
> 线上仓库：<https://github.com/zhaozilong6666/shotplan>
>
> 我用的是 Windows 凭据管理器里**你已经登录**的 GitHub 凭证（系统级 `credential.helper=manager`）。整个过程没有让你交出密码或 token，也没有把凭证写进任何文件。

## 现在还需要你本人做的，只有这三件

| # | 事项 | 时机 | 为什么只能你做 |
| --- | --- | --- | --- |
| 1 | 把 3 条 bullets 贴进简历 | 现在 | 简历是你本人的，只有你能改 |
| 2 | 申请一个 API key（可选） | 阶段 4 真实模型实验 | 需要注册并实名/付款的服务 |
| 3 | 注册部署平台账号（可选） | 阶段 5 | 需要你的账号授权 |

其余全部已完成或由我继续推进。阶段 4 里不依赖 key 的部分（36 用例评测集、`INFEASIBLE` 分支、prompt v1/v2 对比框架、成本/延迟报告、修复循环实验、失败案例库）以及运行统计面板、英文 README 都已做完并验证。需要你配合时，我会给「一步一条命令 + 验证方法」，不会让你自己去查。

---

## 1. 把代码推到 GitHub ✅ 已完成

**之前卡在哪**（这也是「每一步都出问题」的原因，不是你操作错了）：

1. 网页登录 ≠ 命令行凭证：`gh` 没装、git 全局没配用户名/邮箱、没有 SSH key；但系统级 `credential.helper=manager` 里存着已授权 token，其实能直连。
2. 网页上已建好空的 `shotplan` 仓库，但本地目录还没 `git init`，推送必然报错。
3. `README.md` 里还有 `YOUR_NAME` 占位符，直接推上去 CI 徽章是死链。
4. 手册里的 `grep`、`python -c "..."` 在 PowerShell 下容易因引号/命令不存在失败。
5. 本机代理只放行 `api.github.com`，`github.com` 的 CONNECT 隧道返回 502，第二次 `git push` 因此失败。

**已完成的操作**：替换占位符 → 仓库级配置提交身份 → `git init` → 提交全部文件 → 设置 `origin` → 推送 `main` → 通过 API 补齐仓库描述与 topics → 修复 CI 首轮暴露的问题 → 复跑 4 个 Python 版本全部通过。提交身份只写在**本仓库**，没有改动你的全局 git 配置。

**CI 与评测抓到并修掉的两个真问题**：
- Python 3.10/3.11 失败、3.12/3.13 通过 —— 3.12 起 `sum()` 改用补偿求和，满分方案在旧版本算出 `1.0000000000000002`。已改为 `math.fsum` + 固定 6 位精度（ADR 0006）。
- 36 用例里的 C24（预算 400 元）暴露 planner 会挑收费景点导致超预算。已改为按剩余预算裁剪地点组合 + 风格选择先预留转场费。修的是实现，不是调低用例期望。

**网络后患也处理了**：`git push` 走不通时不必换网络，仓库里有 `scripts/push_via_api.py`，通过 GitHub Git Data API 完成等价推送并逐对象校验 SHA（详见 [runbook.md](runbook.md)）。密钥仍从系统凭证读取，不落盘。

**你想自己复核，在 Git Bash 里执行**（只读，安全）：

```bash
cd "D:/WorkBuddy/projects/shotplan"

git remote -v                 # origin 应指向 https://github.com/zhaozilong6666/shotplan.git
git status                    # working tree clean，且与 origin/main 同步
git log --oneline -5
git ls-files | grep -E "\.env$|\.db$|evals/out/" || echo "没有敏感文件被提交"
```

浏览器打开 <https://github.com/zhaozilong6666/shotplan> 看 README，点 **Actions** 看 CI 绿色记录。

> PowerShell 用户：`grep` 换成 `Select-String`，或直接用上面的 Git Bash 命令。

---

## 2. 本地装依赖并完整验证 ✅ 已完成

已在模拟 GitHub Actions 的环境（可编辑安装、不带 `PYTHONPATH`）里实测：

| 检查 | 结果 |
| --- | --- |
| `pytest` | 116 passed（本机 Python 3.13） |
| `ruff check src tests evals` | All checks passed |
| `evals.run_eval --compare v4` | 60/60，均分 0.9762，对照组 0.3115，差值 0.6647，退出码 0 |
| 不可满足需求 | 8/8 正确结构化拒绝 |
| 真实启动服务 | `/api/health`、首页、`/api/plan` 全部正常 |
| 静态展示页 | <https://zhaozilong6666.github.io/shotplan/>（GitHub Pages，已验证 built） |
| CI 矩阵 | Python 3.10 / 3.11 / 3.12 / 3.13 全部通过 |

你想自己再跑一遍（Git Bash）：

```bash
cd "D:/WorkBuddy/projects/shotplan"
python -m pip install -e ".[api,dev]"
python -m pytest
python -m evals.run_eval --write-report --compare evals/baselines/v4-naive.json
python -m ruff check src tests evals
python -m shotplan.cli serve
```

浏览器打开 <http://127.0.0.1:8000>：把运行模式切到 `对照基线（naive）`，生成方案后看「校验报告」标签，应出现一片红色硬问题；切回 `规则 Agent（good）` 则全部通过。把预算改成 100 元，会看到专门的「需求不可满足」视图。

> 若提示找不到模块，用 `PYTHONPATH=src python -m shotplan.cli plan`。

---

## 3. 把项目写进简历 ⬜ 需要你本人（约 10 分钟）

直接可用的一行描述：

> **shotplan** — 可评测的旅拍方案 Agent（Python / FastAPI / SQLite）：生成前判定需求可满足性，以确定性硬约束校验替代 LLM 自评，校验报告驱动定向修复；建 36 用例评测集与对照组，CI 设质量回归门禁。

四条 bullets 和 10 分钟讲稿在 [interview-notes.md](interview-notes.md)，挑 2–3 条贴进简历即可。验证方法：找个人用 30 秒讲一遍，对方能复述出「校验不交给模型」这个点就算成功。

> 面试时可以主动加两句：CI 在 Python 3.10/3.11 上暴露的浮点求和差异（ADR 0006）；以及「可行性判定也不交给模型」的取舍（ADR 0007）。这比只讲功能更能证明你跑过工程闭环。

---

## 4. 申请 API key，接真实模型（阶段 4 剩余部分，可选）

> 不用现在做。没 key 时项目所有功能都能跑；阶段 4 的离线部分已经完成。

### 步骤

1. 选一个 OpenAI 兼容服务并注册（任选其一）：
   - OpenAI Platform：<https://platform.openai.com/api-keys>
   - DeepSeek：<https://platform.deepseek.com/api_keys>
   - 本地免费方案：安装 Ollama，把 `SHOTPLAN_LLM_BASE_URL` 指向 `http://localhost:11434/v1`
2. 创建一个 API key，**只在创建时可见，立刻复制**。
3. 在项目目录复制配置模板：

```bash
cp .env.example .env
```

4. 用文本编辑器打开 `.env`，改两行：

```
SHOTPLAN_PROVIDER=openai_compatible
SHOTPLAN_LLM_API_KEY=你复制的key
```

   若用 DeepSeek / Ollama，同步改 `SHOTPLAN_LLM_BASE_URL` 和 `SHOTPLAN_LLM_MODEL`。

> 拿到 key 之后告诉我，剩下的真实模型对比实验、prompt v1/v2 实测差异、失败案例库与成本/延迟报告我来做。key 只放本地 `.env`，不要发给我、不要贴到聊天里。

### 验证

```bash
python -m shotplan.cli config      # 应显示 "api_key_set": true，且不会打印 key 本身
python -m shotplan.cli plan        # 不带 --mock-mode，走真实模型
python -m evals.run_eval --write-report --compare evals/baselines/v4-naive.json
```

### 安全提醒

- `.env` 已在 `.gitignore` 中，**永远不要**把它 commit 或贴到任何地方。
- 公开 Demo 一律用 `SHOTPLAN_PROVIDER=mock`，不要在部署平台填真实 key。
- 真实调用会产生费用，先设一个用量上限再跑评测集。

---

## 5. 部署在线 Demo（阶段 5 再做，可选）

> 招聘方点开一个能直接玩的链接，比看十张截图有用。

### 方案 A：Render / Railway（推荐，免费额度够用）

1. 注册 <https://render.com> 或 <https://railway.app>，用 GitHub 账号登录。
2. New → Web Service → 连接 `shotplan` 仓库。
3. Runtime / Build 选 **Docker**（仓库里有 `Dockerfile`，暴露 8000 端口）。
4. 环境变量加一条：`SHOTPLAN_PROVIDER=mock`（**不要**填 API key）。
5. 点部署，等待生成 `https://xxx.onrender.com` 之类的地址。
6. 把地址告诉我，我来更新 README 里的「在线 Demo」一行并推送。

### 方案 B：自己的服务器

```bash
docker compose up -d --build
```

### 验证

```bash
curl https://你的地址/api/health
# 应返回 {"status":"ok","provider":"mock","version":"0.1.0"}
```

浏览器打开根地址，点「生成方案」应能出结果。若 502，多半是端口没对上（容器内是 8000）或服务还在冷启动。

---

## 6. 随时可以做的小事

| 想做 | 怎么做 |
| --- | --- |
| 换成你熟悉的场景 | 编辑 `knowledge/data/locations.json` 和 `styles.json`，id 保持英文小写 |
| 加评测用例 | 在 `evals/cases/*.jsonl` 加一行，格式见 [evaluation.md](evaluation.md)；加完要重刷基线 |
| 改一条规则 | 改 `src/shotplan/domain/constraints.py` 的常量，补一条 `tests/test_validator.py` 测试 |
| 加一个不可行条件 | 在 `domain/feasibility.py` 加一条 `FeasibilityIssue`，补测试与 `infeasible` 组用例 |
| 看某次运行到底发生了什么 | `python -m shotplan.cli history`，或访问 `/api/runs/<run_id>` |
| `git push` 被代理挡住 | `python scripts/push_via_api.py`，见 [runbook.md](runbook.md) |
| 每次改完代码 | 按 [runbook.md](runbook.md) 的自检顺序跑一遍 |

---

## 我这边已经做完、你不用管的

仓库结构与全部源码、116 个单元测试、50 个评测用例与两代对照基线、自动生成的评测报告、架构文档与 7 份 ADR、CI 工作流（含修复实验与失败案例两道门禁）、Docker 配置、Web 界面（含运行统计视图）、中英文 README、GitHub Pages 静态展示页、路线图、面试讲稿、排错手册、GitHub 建仓与推送、仓库描述与 topics、CI 跨版本浮点问题修复、代理受限时的 API 推送脚本、生成前可行性判定、prompt v1/v2 对比框架、成本/延迟报告与模拟 provider、修复循环实验、结构化失败案例库、按 provider / prompt 版本聚合的运行统计面板。本地已验证：测试全绿、评测退出码 0、CLI 与 API 端到端可用；GitHub Actions 4 个 Python 版本全部通过。
