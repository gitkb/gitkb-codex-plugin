.PHONY: all test test-integration lint lint-json lint-policy lint-codex diff-check release-check clean

all: lint test

test:
	@tests/package-policy.sh
	@python3 -B tests/mcp-contract.py plugin
	@python3 -B tests/mcp-startup-unit.py

test-integration:
	@python3 -B tests/mcp-startup.py --codex "$(CODEX_TEST_BINARY)"
	@python3 -B tests/mcp-startup.py --codex "$(CODEX_TEST_BINARY)" --legacy-first

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
	@if [ -n "$(CODEX_TEST_BINARY)" ]; then \
		"$(CODEX_TEST_BINARY)" plugin --help >/dev/null; \
	elif command -v codex >/dev/null 2>&1; then \
		codex plugin --help >/dev/null; \
	else \
		echo "Skipping Codex CLI check (codex not available)"; \
	fi

diff-check:
	git diff --check

release-check: lint test diff-check

clean:
	@echo "Nothing to clean."
