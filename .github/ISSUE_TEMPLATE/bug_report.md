---
name: Bug report
about: 报告可复现的问题
title: "[bug] "
labels: bug
assignees: ""
---

## 现象

一句话说明发生了什么，以及你期望发生什么。

## 复现步骤

```bash
# 请给出可直接复制的命令
python -m shotplan.cli plan --hours 3 --budget 800
```

## 实际输出

粘贴命令输出或 `/api/plan` 的响应片段（**不要粘贴 API key**）。

## 环境

- OS：
- Python 版本：
- provider：`mock` / `openai_compatible`
- 是否已安装 `.[api,dev]`：

## 是否影响评测

- [ ] `python -m pytest` 是否通过
- [ ] `python -m evals.run_eval` 退出码

如果问题涉及某条规则，请附上 `validation.issues` 里的 code。
