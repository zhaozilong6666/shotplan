# 发布状态与自助上传说明

## 一、WorkBuddy 内置发布：当前不可用

连续 5 次尝试，全部失败在同一个位置：

| 次数 | 沙箱节点 | 结果 |
| ---: | --- | --- |
| 1 | `e2b.gz3` | 连接超时 10s |
| 2 | `e2b.ap-beijing` | 连接超时 10s |
| 3 | `e2b.bj7` | 连接超时 10s |
| 4 | `e2b.gz1` | 连接超时 10s |
| 5 | `e2b.gz4` | 连接超时 10s |

错误码：`SITES_UPLOAD_FAILED` / `SANDBOX_DATA_PLANE_REQUEST_FAILED`

判断依据：`*.sandbox.cloudstudio.club` 在公网 DNS 返回「名称不存在」，说明它是平台内部地址；报错里已经出现解析后的 IP，说明卡在 TCP 连接而不是 DNS。换节点、重启客户端、换网络都不改变结果 → **平台侧上传通道问题，不是本机配置问题**。

应用记录已创建：`wbapp_fBGl1iycT44ThE1r0xK5T1`（未部署）。平台恢复后重新发布，会复用这条记录和同一条链接。

## 二、可以直接上传的发布包

已生成并本地验证：

```
D:\WorkBuddy\projects\shotplan-demo\
```

内容（40 个文件，约 246 KB）：

| 文件/目录 | 作用 |
| --- | --- |
| `Dockerfile` | 基于 `python:3.12-slim`，非 root 运行，启动 `demo_app.py` |
| `demo_app.py` | 强制 `SHOTPLAN_PROVIDER=mock`，读取 `$PORT`，监听 `0.0.0.0` |
| `src/shotplan/` | 服务端代码 + 内置前端页面 |
| `pyproject.toml` | 依赖声明（`[api]` extra 含 FastAPI/uvicorn） |
| `README.md` | 含 Hugging Face Space 元数据（`sdk: docker`、`app_port: 7860`） |
| `requirements.lock` | 锁定版本，供需要时使用 |

已验证：`/api/health` 返回 `provider=mock`，首页 200，`/api/plan` 正常出方案。包内**不含** `.env`、密钥、测试与评测数据（已扫描确认）。

## 三、Hugging Face Spaces 上传步骤（免费、无需服务器）

1. 登录 <https://huggingface.co>，右上头像 → **New Space**
2. 填 `shotplan-demo`，**SDK 选 Docker**，Hardware 选 **CPU basic · Free**，可见性选 **Public**，Create
3. 进入 Space → **Files** 标签 → **Add file → Upload files**
4. 把 `D:\WorkBuddy\projects\shotplan-demo\` 里的**所有内容**拖进去（必须保留 `src/` 目录层级）
5. 底部 **Commit changes to main**
6. 等状态从 `Building` 变成 `Running`（约 3–5 分钟）
7. 页面顶部 **App** 标签就是公开链接，形如 `https://<用户名>-shotplan-demo.hf.space`

注意事项：

- 首次构建会 `pip install`，需要几分钟；失败时点 **Logs** 看构建日志。
- 容器内 SQLite 存在临时目录，重启即清空——演示用途，不需要持久化。
- 如果浏览器不支持拖拽整个文件夹，用 **Add file → Upload folder**（Chrome / Edge 支持）。
- git 方式也可行，但有两个前提：Hugging Face 直连不通，必须走 `127.0.0.1:7897`；系统里的 `HTTP_PROXY=127.0.0.1:50306` 是失效端口，会覆盖配置，push 时要显式指定：
  `git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push`
- Docker SDK 本身免费，收费的是 Hardware 档位；创建时务必选 **CPU basic · Free**。

## 四、其他可选平台

| 平台 | 前提 | 说明 |
| --- | --- | --- |
| Render | GitHub 账号 | **推荐**。原生 Python 运行时，不需要 Docker，免费档可用 |
| 腾讯云 CloudBase 云托管 | 实名 + 支付方式 | 国内访问快，配置步骤较多，按量计费 |
| 阿里云函数计算 FC | 实名 | 有免费额度，需要改造成函数入口，改动较大 |
| 任意 VPS | 一台服务器 | `docker build -t shotplan . && docker run -p 8000:8000 shotplan` |

### Render 部署步骤（无 Docker）

**最快路径：直接用已公开的仓库，不需要上传任何文件**

仓库 `zhaozilong6666/shotplan` 是公开的，`main` 上已有 `pyproject.toml`、`src/shotplan/`、`web/index.html`，可以直接部署：

1. <https://render.com> 注册（邮箱即可）
2. **New +** → **Web Service** → 选 **Public Git repository**
3. 填 `https://github.com/zhaozilong6666/shotplan`
4. 配置：

   | 字段 | 值 |
   | --- | --- |
   | Name | `shotplan-demo` |
   | Language | `Python 3` |
   | Branch | `main` |
   | Build Command | `pip install -e ".[api]"` |
   | Start Command | `python -m shotplan.cli serve --host 0.0.0.0 --port $PORT` |
   | Instance Type | **Free** |

5. 环境变量加一条 `SHOTPLAN_PROVIDER=mock`（不加也默认 mock，加了更明确）
6. Create Web Service，等 3–5 分钟

注意：这条路部署的是 `main` 上**最后一次推送**的版本，不包含本地未提交的改动（安全加固、新增约束、知识库更新、`demo_app.py`）。要发布最新版，需要先把工作区提交并推送。

**需要最新版时：把发布包传到 GitHub**

当前网络下 `git push` 到 GitHub 会 502（代理只放行 `api.github.com`），所以用网页上传：

1. <https://github.com/new> → Repository name 填 `shotplan-demo` → Public → Create
2. 仓库页 → **Add file → Upload files**
3. 把 `D:\WorkBuddy\projects\shotplan-demo\` 里的**所有内容**拖进去（保留 `src/` 层级）
4. Commit changes

**2. Render 建服务**

1. <https://render.com> → 用 GitHub 账号登录
2. **New +** → **Web Service** → 选刚建的 `shotplan-demo` 仓库
3. 填写：
   - Language：`Python 3`
   - Build Command：`pip install -e ".[api]"`
   - Start Command：`python demo_app.py`
   - Instance Type：**Free**
4. 环境变量（可选，但建议加）：`PYTHON_VERSION=3.12`
5. **Create Web Service**，等 3–5 分钟

**3. 拿到链接**

形如 `https://shotplan-demo.onrender.com`。

注意事项：

- 免费档 15 分钟无访问会休眠，下次首次打开要等 30–60 秒冷启动，属正常现象。
- `demo_app.py` 读取平台注入的 `$PORT`，不需要手改端口。
- 容器内 SQLite 放在临时目录，重启即清空——演示用途。

## 五、恢复后的检查清单

- [ ] `GET /api/health` 返回 `{"provider": "mock"}`（不是 `openai_compatible`）
- [ ] 首页能正常渲染，样式未丢
- [ ] 提交一个西安需求，能返回时间轴与评分
- [ ] 页面无报错（浏览器 Console）
