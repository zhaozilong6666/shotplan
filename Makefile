.PHONY: help install test lint eval report serve plan smoke clean

PYTHON ?= python

help:
	@echo "shotplan commands"
	@echo "  install   安装可选依赖 (api + dev)"
	@echo "  test      运行单元测试"
	@echo "  lint      ruff 静态检查"
	@echo "  eval      运行评测集并生成报告"
	@echo "  plan      用离线 mock 生成一份示例方案"
	@echo "  serve     启动 Web / API (需要 install)"
	@echo "  smoke     测试 + 评测 + CLI 冒烟，一条命令验证全仓"

install:
	$(PYTHON) -m pip install -e ".[api,dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests evals

eval:
	$(PYTHON) -m evals.run_eval --write-report

plan:
	$(PYTHON) -m shotplan.cli plan --city xian --date 2026-10-04 --hours 6 --budget 1500 --style tang

serve:
	$(PYTHON) -m shotplan.cli serve --port 8000

smoke: test eval plan

report:
	$(PYTHON) -m evals.run_eval --write-report --compare evals/baselines/v1-naive.json

clean:
	rm -rf .pytest_cache .ruff_cache evals/out
