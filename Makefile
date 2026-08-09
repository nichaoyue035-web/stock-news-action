PYTHON ?= .venv/bin/python
BOOTSTRAP_PYTHON ?= python3

.PHONY: setup test lint validate check

setup:
	$(BOOTSTRAP_PYTHON) -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

test:
	@test -x "$(PYTHON)" || (echo "未找到 $(PYTHON)，请先执行 make setup" && exit 1)
	$(PYTHON) -m pytest

lint:
	@test -x "$(PYTHON)" || (echo "未找到 $(PYTHON)，请先执行 make setup" && exit 1)
	$(PYTHON) -m ruff check .

validate:
	@test -x "$(PYTHON)" || (echo "未找到 $(PYTHON)，请先执行 make setup" && exit 1)
	$(PYTHON) scripts/validate_deployment.py

check: test lint validate
