.PHONY: check test lint fmt install list

check: lint test ## Run linters and unit tests

test: ## Run the unit test suite
	python3 -m unittest discover -s tests -v

lint: ## Run ruff and shellcheck
	ruff check .
	shellcheck install.sh

fmt: ## Format Python code with ruff
	ruff format .
	ruff check --fix .

install: ## Link herdr-taskr into ~/.local/bin
	sh install.sh

list: ## List tasks discovered in this project
	./bin/herdr-taskr list
