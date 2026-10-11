.PHONY: install test lint console demo serve sandbox benchmark
install:
	python -m pip install -r requirements-dev.lock
	python -m pip install --no-deps -e .
test:
	python -m pytest -q
lint:
	python -m ruff check atlas apps tests benchmarks scripts
	python -m ruff format --check atlas apps tests benchmarks scripts
console:
	cd frontend && npm ci && npm run build
demo:
	atlas demo auth --trusted-local
serve:
	atlas serve
sandbox:
	docker build -f deploy/sandbox.Dockerfile -t atlas-sandbox:1.0 .
benchmark:
	python -m benchmarks.run_all --trusted-local --repetitions 3
