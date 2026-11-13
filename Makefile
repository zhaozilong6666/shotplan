.PHONY: help install test lint eval mutation mine report fuzz bench load httpload scale examples gates showcase serve plan smoke clean

PYTHON ?= python

help:
	@echo "shotplan commands"
	@echo "  install   安装可选依赖 (api + dev)"
	@echo "  test      运行单元测试（含覆盖率门槛）"
	@echo "  lint      ruff 静态检查"
	@echo "  eval      运行评测集并生成报告"
	@echo "  mutation  变异测试：注入受控故障验证门禁有效，并刷新报告"
	@echo "  mine      从失败案例库挖掘盲测挑战集并刷新报告"
	@echo "  report    评测 + 与当前基线对比"
	@echo "  fuzz      确定性模糊测试（1000 次变异，生成报告）"
	@echo "  bench     性能基准（P50/P95、吞吐、堆峰值）"
	@echo "  load      并发负载测试（错误数、丢写、饱和点）"
	@echo "  httpload  真实 uvicorn 服务 + 多 worker 的 HTTP 压测"
	@echo "  scale     线程 vs 进程扩展性对比"
	@echo "  examples  重新生成 docs/examples.md 真实输出示例"
	@echo "  gates     本地跑一遍 CI 的全部质量门禁"
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
	$(PYTHON) -m evals.scaling --check --requests 300
	$(PYTHON) -m evals.examples --check

showcase:
	$(PYTHON) -m evals.cost_latency --write-report
	$(PYTHON) -m evals.benchmark --out evals/benchmark.json --write-report
	$(PYTHON) scripts/build_showcase.py

serve:
	$(PYTHON) -m shotplan.cli serve --port 8000

plan:
	$(PYTHON) -m shotplan.cli plan --city xian --date 2026-10-04 --hours 6 --budget 1500 --style tang

smoke: lint test gates plan

clean:
	rm -rf .pytest_cache .ruff_cache evals/out
