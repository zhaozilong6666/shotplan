.PHONY: help install test lint typecheck eval mutation mine report fuzz bench load httpload workerlab persistlab pipelinedoc scale examples gates kb citygap sweep transfer sample thresholds analysis showcase serve plan smoke clean

PYTHON ?= python

help:
	@echo "shotplan commands"
	@echo "  install   安装可选依赖 (api + dev)"
	@echo "  test      运行单元测试（含覆盖率门槛）"
	@echo "  lint      ruff 静态检查"
	@echo "  typecheck mypy 类型检查（src/shotplan）"
	@echo "  eval      运行评测集并生成报告"
	@echo "  mutation  变异测试：注入受控故障验证门禁有效，并刷新报告"
	@echo "  mine      从失败案例库挖掘盲测挑战集并刷新报告"
	@echo "  report    评测 + 与当前基线对比"
	@echo "  fuzz      确定性模糊测试（1000 次变异，生成报告）"
	@echo "  bench     性能基准（P50/P95、吞吐、堆峰值）"
	@echo "  load      并发负载测试（错误数、丢写、饱和点）"
	@echo "  httpload  真实 uvicorn 服务 + 多 worker 的 HTTP 压测"
	@echo "  workerlab 持久化开/关对照实验，验证多 worker 瓶颈归因"
	@echo "  persistlab 写入 off/sync/async 三组对照，验证异步化收益与不丢写"
	@echo "  pipelinedoc 从代码里的状态表重新生成流水线契约文档"
	@echo "  productlab 产品层测量（一句话理解 / 三策略 / 协商）"
	@echo "  scale     线程 vs 进程扩展性对比"
	@echo "  examples  重新生成 docs/examples.md 真实输出示例"
	@echo "  gates     本地跑一遍 CI 的全部质量门禁"
	@echo "  kb        重新生成知识库健康度 / 下一步队列 / 交接页"
	@echo "  citygap   三城补充回归（成都 / 北京 / 开封）"
	@echo "  sweep     可行性判定的影响面（442 条 brief）"
	@echo "  transfer  20 分钟转场够不够（三角不等式上下界）"
	@echo "  sample    重新生成 README 里的示例输出"
	@echo "  thresholds 重新生成阈值来源表（哪个数是量出来的）"
	@echo "  analysis  重新生成四份分析（转场模型、按城市、敏感性、余量）"
	@echo "  showcase  重新生成展示页数据块"
	@echo "  plan      用离线 mock 生成一份示例方案"
	@echo "  serve     启动 Web / API (需要 install)"
	@echo "  smoke     门禁 + 评测 + CLI 冒烟，一条命令验证全仓"

install:
	$(PYTHON) -m pip install -e ".[api,dev]"

test:
	$(PYTHON) -m pytest --cov=src/shotplan --cov-fail-under=85

lint:
	$(PYTHON) -m ruff check src tests evals scripts

typecheck:
	$(PYTHON) -m mypy

eval:
	$(PYTHON) -m evals.run_eval --write-report

mutation:
	$(PYTHON) -m evals.mutation_lab --write
	$(PYTHON) -m evals.run_eval --write-report

mine:
	$(PYTHON) -m evals.case_miner --write
	$(PYTHON) -m evals.case_miner --check

report:
	$(PYTHON) -m evals.run_eval --write-report --compare evals/baselines/v6-naive.json

fuzz:
	$(PYTHON) -m evals.fuzz --iterations 1000 --write-report

bench:
	$(PYTHON) -m evals.benchmark --write-report

httpload:
	$(PYTHON) -m evals.http_load --write
	$(PYTHON) -m evals.http_load --check

workerlab:
	$(PYTHON) -m evals.worker_lab --write
	$(PYTHON) -m evals.worker_lab --check

persistlab:
	$(PYTHON) -m evals.persist_lab --write
	$(PYTHON) -m evals.persist_lab --check

pipelinedoc:
	$(PYTHON) scripts/build_pipeline_doc.py --write
	$(PYTHON) scripts/build_pipeline_doc.py --check

gates:
	$(PYTHON) -m evals.run_eval --write-report
	$(PYTHON) -m evals.mutation_lab --check
	$(PYTHON) -m evals.repair_lab --check
	$(PYTHON) -m evals.collect_failures --check
	$(PYTHON) -m evals.case_miner --check
	$(PYTHON) -m evals.fuzz --check --iterations 500
	$(PYTHON) -m evals.benchmark --check
	$(PYTHON) -m evals.loadtest --check
	$(PYTHON) -m evals.http_load --check
	$(PYTHON) -m evals.worker_lab --check
	$(PYTHON) -m evals.persist_lab --check
	$(PYTHON) scripts/build_pipeline_doc.py --check
	$(PYTHON) scripts/build_rule_map.py --check
	$(PYTHON) scripts/build_thresholds.py --check
	$(PYTHON) -m evals.product_lab --check
	$(PYTHON) -m evals.domain_portability --check
	$(PYTHON) -m evals.multi_turn_lab --check
	$(PYTHON) -m evals.repair_attribution --check
	$(PYTHON) scripts/check_knowledge_freshness.py --max-age-days 180
	$(PYTHON) scripts/check_snapshot_hygiene.py
	$(PYTHON) -m evals.fault_injection --check
	$(PYTHON) -m evals.slo_baseline --check
	$(PYTHON) -m evals.http_smoke --check
	$(PYTHON) scripts/check_doc_claims.py
	$(PYTHON) scripts/render_readme_sample.py --check
	$(PYTHON) -m evals.experiment_log --check
	$(PYTHON) scripts/audit_reports.py --check
	$(PYTHON) scripts/kb.py --check
	$(PYTHON) -m evals.city_gap --check
	$(PYTHON) -m evals.feasibility_sweep --check
	$(PYTHON) -m evals.transfer_check --check
	$(PYTHON) -m evals.annotation_kit --check
	$(PYTHON) scripts/validate_knowledge.py --check
	$(PYTHON) scripts/check_gate_count.py
	$(PYTHON) -m evals.scaling --check --requests 300
	$(PYTHON) -m evals.examples --check

kb:
	$(PYTHON) scripts/kb.py --write
	$(PYTHON) scripts/kb.py --check

citygap:
	$(PYTHON) -m evals.city_gap --write
	$(PYTHON) -m evals.city_gap --check

sweep:
	$(PYTHON) -m evals.feasibility_sweep --write
	$(PYTHON) -m evals.feasibility_sweep --check
	$(PYTHON) -m evals.transfer_check --check

transfer:
	$(PYTHON) -m evals.transfer_check --write

thresholds:
	$(PYTHON) scripts/build_thresholds.py --write

sample:
	$(PYTHON) scripts/render_readme_sample.py --write

# The four analysis pages take about a minute together and change only when the
# rules, the knowledge base or the eval set change. They are not gates: an always-on
# diagnostic is just a slower gate, and these answer questions rather than block work.
analysis:
	$(PYTHON) -m evals.transfer_model --write
	$(PYTHON) -m evals.transfer_by_city --write
	$(PYTHON) -m evals.sensitivity --write
	$(PYTHON) -m evals.eval_margin --write

showcase:
	$(PYTHON) -m evals.cost_latency --write-report
	$(PYTHON) -m evals.benchmark --out evals/benchmark.json --write-report
	$(PYTHON) scripts/build_showcase.py

serve:
	$(PYTHON) -m shotplan.cli serve --port 8000

plan:
	$(PYTHON) -m shotplan.cli plan --city xian --date 2026-10-04 --hours 6 --budget 1500 --style tang

# 3 repeats of the full suite: run-to-run spread, ~90 min, costs real money.
repeat:
	$(PYTHON) -m evals.real_model_lab --repeat 3 --concurrency 3 --retry-errors 1 \
		--write --out evals/real-model-repeat3.json --report-path docs/real-model-repeat3.md

# Paired prompt A/B on a stratified 30-case subset (~60 calls).
# Both arms must use the same cases: that is what makes the CI half as wide.
ab-prompt:
	SHOTPLAN_PROMPT_VERSION=planner-v2 $(PYTHON) -m evals.real_model_lab \
		--profile iterate --write --out evals/out/ab-v2.json --report-path evals/out/ab-v2.md
	SHOTPLAN_PROMPT_VERSION=planner-v3 $(PYTHON) -m evals.real_model_lab \
		--profile iterate --write --out evals/out/ab-v3.json --report-path evals/out/ab-v3.md
	-$(PYTHON) -m evals.pair_compare --a evals/out/ab-v2.json --b evals/out/ab-v3.json \
		--out evals/out/prompt-ab.md

smoke: lint typecheck test gates plan

clean:
	rm -rf .pytest_cache .ruff_cache evals/out
