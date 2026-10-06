PY = .venv/bin/python

.PHONY: venv test demo live-demo report cluster-up cluster-down clean

venv:
	python3 -m venv .venv && .venv/bin/pip install -q mcp langgraph httpx pytest

test: venv
	$(PY) -m pytest tests/ -q

## Default: deterministic mock-LLM demo, zero API spend.
demo: venv
	$(PY) -m agent.run_demo && $(PY) -m trace.report && echo "demo complete: trace.html"

## Real-LLM demo through OpenRouter (demo-runtime key; cheap flash-class model).
live-demo: venv
	MCP_OPS_MODE=live $(PY) -m agent.run_demo && $(PY) -m trace.report && echo "live demo complete: trace.html"

report:
	$(PY) -m trace.report

cluster-up:
	bash k3d/bootstrap.sh

cluster-down:
	k3d cluster delete ops-demo

clean:
	rm -rf runs trace.html runs/testtmp