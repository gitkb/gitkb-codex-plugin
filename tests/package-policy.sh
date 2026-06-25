#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

test -f .agents/plugins/marketplace.json || fail "missing repo marketplace"
test -f plugin/.codex-plugin/plugin.json || fail "missing plugin manifest"
test -f plugin/hooks/hooks.json || fail "missing hooks config"
test -f plugin/.mcp.json || fail "missing MCP config"
test -f plugin/skills/gitkb-code-intelligence/SKILL.md || fail "missing code-intelligence skill"

marketplace_name=$(jq -r '.name' .agents/plugins/marketplace.json)
test "$marketplace_name" = "gitkb" || fail "marketplace name must be gitkb"

plugin_name=$(jq -r '.name' plugin/.codex-plugin/plugin.json)
test "$plugin_name" = "gitkb" || fail "plugin name must be gitkb"

source_path=$(jq -r '.plugins[] | select(.name == "gitkb") | .source.path' .agents/plugins/marketplace.json)
test "$source_path" = "./plugin" || fail "marketplace source.path must point to ./plugin"

if jq -e 'has("hooks")' plugin/.codex-plugin/plugin.json >/dev/null; then
  fail "plugin manifest must not use unsupported hooks field"
fi

jq -e '.skills == "./skills/"' plugin/.codex-plugin/plugin.json >/dev/null \
  || fail "plugin manifest must point at bundled skills"

jq -e '.mcpServers == "./.mcp.json"' plugin/.codex-plugin/plugin.json >/dev/null \
  || fail "plugin manifest must point at bundled MCP config"

hook_commands=$(jq -r '.. | objects | select(.type? == "command") | .command' plugin/hooks/hooks.json)
test -n "$hook_commands" || fail "expected command hooks"
if echo "$hook_commands" | grep -v '^git-kb hook codex$' >/dev/null; then
  fail "all hooks must delegate to git-kb hook codex"
fi

if grep -R "gitkb-a[t]c\\|@personal" . \
  --exclude-dir=.git \
  --exclude=package-policy.sh >/dev/null; then
  fail "plugin must not contain old plugin or personal-marketplace identity"
fi

if [ -d plugin/commands ] && find plugin/commands -type f -print -quit | grep -q .; then
  fail "plugin must not vendor duplicated command surface"
fi

echo "Package policy checks passed."
