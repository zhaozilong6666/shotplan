# ADR 0015 · trace 记录知识库与规则指纹

- 状态：已采纳
- 日期：2026-11-13
- 相关：[0004 版本化 prompt](0004-versioned-prompts.md)、[0012 写后置持久化](0012-write-behind-persistence.md)

## 背景

运行历史保存了完整请求、方案、校验报告与 trace，trace 里也记录了 `provider` 与 `prompt_version`。但知识库（`locations.json` / `styles.json`）和规则（`constraints.py` 的规则表与阈值）没有版本标识。

结果是：改一次价格、标签或规则阈值之后，历史分数仍在数据库里，却无法判断它是由哪一版数据与规则产生的。对一个主张「可追溯、可复现」的项目，这是结构性缺口：分数可比的前提是尺子和题库都没变。

## 决策

1. `RunTrace` 增加三个字段：
   - `kb_version`：`locations.json + styles.json` 的内容 SHA-256 前 12 位；
   - `rules_version`：`RULES`、`SCORE_WEIGHTS` 与全部阈值序列化后的 SHA-256 前 12 位；
   - `wall_duration_ms`：请求端到端墙钟；`total_duration_ms` 保留为各阶段计时之和，两者语义不同，不再混用。
2. `TraceCollector` 在创建时写入这两个指纹，随 JSON payload 一起落库，CLI 的 trace 摘要也显示它们。
3. 不维护手工版本字符串：手工版本一定会忘记 bump；内容哈希在数据或规则变化时自动变化。
4. 数据库 schema 增加 `PRAGMA user_version`（当前 v1）；遇到更高版本的数据库直接拒绝启动，而不是带着未知 schema 继续写。
5. 运行历史是辅助能力：写库失败不再让请求失败，而是返回方案并追加一条软问题 `PERSISTENCE_FAILED`，同时写日志。错误按类型映射 HTTP 状态：provider → 502、storage → 503、配置/知识库 → 500、解析/输入 → 422。

## 后果

- 同一 `(kb_version, rules_version, prompt_version)` 下的分数才可直接比较；跨指纹比较需要在报告里说明。
- 每次改知识库或规则都会让指纹变化，历史运行不会被误认为「可复现」。
- `wall_duration_ms` 与 `total_duration_ms` 同时保留，避免为了修语义而破坏已有字段的消费者。
- SQLite 增加一个 schema 版本标记，未来加列时有明确的迁移入口。

## 没有采纳的方案

- **只记录 git commit**：工作区可能有未提交的数据改动，commit 不能代表实际运行内容。
- **手工维护 `KB_VERSION = "v3"`**：已经发生过版本字符串漏改（v0.10.0），不再依赖人工纪律。
- **把统计改成第二张指标表**：会引入两份真相；当前瓶颈不在这里。
