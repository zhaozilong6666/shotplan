# 需要你本人动手的事项（当前状态）

> **2026-11-12 更新：第 1、2 节已由我在本机执行完毕并验证，你不用再照着做。**
> 线上仓库：<https://github.com/zhaozilong6666/shotplan>
>
> 我用的是 Windows 凭据管理器里**你已经登录**的 GitHub 凭证（系统级 `credential.helper=manager`）。整个过程没有让你交出密码或 token，也没有把凭证写进任何文件。

## 现在还需要你本人做的，只有这三件

| # | 事项 | 时机 | 为什么只能你做 |
| --- | --- | --- | --- |
| 1 | 把 3 条 bullets 贴进简历 | 现在 | 简历是你本人的 |
| 2 | 申请一个 API key（可选） | 阶段 4 | 需要注册并实名/付款的服务 |
| 3 | 注册部署平台账号（可选） | 阶段 5 | 需要你的账号授权 |

其余全部已完成或由我继续推进。

---

## 1. 把代码推到 GitHub ✅ 已完成

**之前卡在哪**（这也是「每一步都出问题」的原因，不是你操作错了）：

1. 网页里登录了 GitHub，但命令行是另一套凭证体系 —— 本机 `gh` 没装、全局 git 没配用户名/邮箱、没有 SSH key。不过系统级 git 配置里有 `credential.helper=manager`，Windows 凭据管理器里存着已授权 token，所以其实能直连，只是手册没接上这一层。
2. 网页上已经建好了空的 `shotplan` 仓库，但本地目录还不是 git 仓库（`git init` 没执行），所以推送命令必然报错。
3. `README.md` 里还有 `YOUR_NAME` 占位符，直接推上去 CI 徽章会是死链。
4. 手册里的 `grep`、`python -c "..."` 在 PowerShell 下容易因引号和命令不存在而报错，需要 Git Bash 或等价写法。

**已完成的操作**：替换占位符 → 仓库级配置提交身份 → `git init` → 提交全部文件 → 设置 `origin` → 推送 `main` → 通过 API 补齐仓库描述与 topics → 检查 CI 首次运行结论。提交身份只写在**本仓库**，没有改动你的全局 git 配置。

**CI 顺带抓到并修掉的一个真问题**：首次运行在 Python 3.10 / 3.11 上失败、3.12 / 3.13 通过 —— 3.12 起内置 `sum()` 改用补偿求和，导致同一份满分方案在旧版本上算出 `1.0000000000000002`。已把评分改为 `math.fsum` + 固定 6 位精度，评测聚合同步处理，并加了契约测试与 [ADR 0006](adr/0006-scoring-determinism.md)。这类跨解释器差异本地单版本跑不出来，只有推上 CI 才暴露，所以这一步不能省。

**你想自己复核，在 Git Bash 里执行**（只读，安全）：

```bash
cd "C:/Users/34877/WorkBuddy/2026-11-12-14-29-42/shotplan"

git remote -v                 # origin 应指向 https://github.com/zhaozilong6666/shotplan.git
git status                    # working tree clean
git log --oneline -3          # 首次提交 + 修复提交
git ls-files | grep -E "\.env$|\.db$|evals/out/" || echo "没有敏感文件被提交"
```

浏览器打开 <https://github.com/zhaozilong6666/shotplan> 看 README，点 **Actions** 看 CI 结果。

> PowerShell 用户：`grep` 换成 `Select-String`，或直接用上面的 Git Bash 命令。

---

## 2. 本地装依赖并完整验证 ✅ 已完成

已在模拟 GitHub Actions 的环境（可编辑安装、不带 `PYTHONPATH`）里实测：

| 检查 | 结果 |
| --- | --- |
| `pytest` | 77 passed |
| `ruff check src tests evals` | All checks passed |
| `evals.run_eval --compare` | 12/12，均分 0.9734，对照组 0.1900，退出码 0 |
| 真实启动服务 | `/api/health`、首页、`/api/plan` 全部正常 |
| CI 矩阵 | Python 3.10 / 3.11 / 3.12 / 3.13，见仓库 Actions |

你想自己再跑一遍（Git Bash）：

```bash
cd "C:/Users/34877/WorkBuddy/2026-11-12-14-29-42/shotplan"
python -m pip install -e ".[api,dev]"
python -m pytest
python -m evals.run_eval --write-report --compare evals/baselines/v1-naive.json
python -m ruff check src tests evals
python -m shotplan.cli serve
```

浏览器打开 <http://127.0.0.1:8000>：把运行模式切到 `对照基线（naive）`，生成方案后看「校验报告」标签，应出现一片红色硬问题；切回 `规则 Agent（good）` 则全部通过。

> 若提示找不到模块，用 `PYTHONPATH=src python -m shotplan.cli plan`。

---

## 3. 把项目写进简历 ⬜ 需要你本人（约 10 分钟）

直接可用的一行描述：

> **shotplan** — 可评测的旅拍方案 Agent（Python / FastAPI / SQLite）：以确定性硬约束校验替代 LLM 自评，校验报告驱动定向修复；建 12 用例评测集与对照组，CI 设质量回归门禁。

三条 bullets 和 10 分钟讲稿在 [interview-notes.md](interview-notes.md)，挑 2–3 条贴进简历即可。验证方法：找个人用 30 秒讲一遍，对方能复述出「校验不交给模型」这个点就算成功。

> 面试时可以主动加一句：首次推送 CI 在 Python 3.10/3.11 上暴露了浮点求和差异，我用固定精度评分与 4 版本矩阵解决了它（ADR 0006）。这比只讲功能更能证明你真的跑过工程闭环。

---

## 4. 申请 API key，接真实模型（阶段 4 再做，可选）

> 不用现在做。没 key 时项目所有功能都能跑。

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

> 拿到 key 之后告诉我，剩下的对比实验、prompt v2、成本/延迟报告我来做。key 只放本地 `.env`，不要发给我、不要贴到聊天里。

### 验证

```bash
python -m shotplan.cli config      # 应显示 "api_key_set": true，且不会打印 key 本身
python -m shotplan.cli plan        # 不带 --mock-mode，走真实模型
python -m evals.run_eval --write-report --compare evals/baselines/v1-naive.json
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
| 加评测用例 | 在 `evals/cases/*.jsonl` 加一行，格式见 [evaluation.md](evaluation.md) |
| 改一条规则 | 改 `src/shotplan/domain/constraints.py` 的常量，补一条 `tests/test_validator.py` 测试 |
| 看某次运行到底发生了什么 | `python -m shotplan.cli history`，或访问 `/api/runs/<run_id>` |
| 每次改完代码 | 按 [runbook.md](runbook.md) 的 5 步自检 |

---

## 我这边已经做完、你不用管的

仓库结构与全部源码、77 个单元测试、12 个评测用例与对照基线、自动生成的评测报告、架构文档与 6 份 ADR、CI 工作流、Docker 配置、Web 界面、README、路线图、面试讲稿、排错手册、GitHub 建仓与推送、仓库描述与 topics、CI 跨版本浮点问题修复。本地已验证：测试全绿、评测退出码 0、CLI 与 API 端到端可用。
