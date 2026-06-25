.PHONY: all test lint lint-json lint-policy lint-codex diff-check release-check clean

all: lint test

test:
	@tests/package-policy.sh

lint: lint-json lint-policy lint-codex
	@echo "All checks passed."

lint-json:
	@echo "Checking marketplace.json is valid JSON..."
	@jq empty .agents/plugins/marketplace.json
	@echo "Checking plugin.json is valid JSON..."
	@jq empty plugin/.codex-plugin/plugin.json
	@echo "Checking hooks.json is valid JSON..."
	@jq empty plugin/hooks/hooks.json
	@echo "Checking .mcp.json is valid JSON..."
	@jq empty plugin/.mcp.json

lint-policy:
	@echo "Checking plugin remains a thin GitKB wrapper..."
	@tests/package-policy.sh

lint-codex:
	@if command -v codex >/dev/null 2>&1; then \
		echo "Checking Codex CLI is available for marketplace install testing"; \
		codex plugin --help >/dev/null; \
	else \
		echo "Skipping Codex CLI check (codex not available)"; \
	fi

diff-check:
	git diff --check

release-check: lint test diff-check

clean:
	@echo "Nothing to clean."
