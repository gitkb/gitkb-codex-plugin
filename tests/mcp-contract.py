#!/usr/bin/env python3
"""Guard the packaged MCP launch contract, including its credential boundary."""
import json
import sys
import unittest
from pathlib import Path

FORWARDED = {
    "SESSION_ACTIVITY_RESOURCE_ID", "SESSION_ACTIVITY_RESOURCE_KIND",
    "SESSION_ACTIVITY_PROVIDER", "SESSION_ACTIVITY_CONTEXT_FILE",
    "SESSION_ACTIVITY_SINK_COMMAND", "SESSION_ACTIVITY_SINK_ARGS_JSON",
    "ATC_ROOT", "ATC_CONFIG",
}
PLUGIN_ROOT = Path(sys.argv.pop(1))


class LaunchContract(unittest.TestCase):
    def test_parent_context_is_forwarded_by_name_only(self):
        """Require the activity/registry allowlist while rejecting fixed environment values."""
        config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
        self.assertEqual(set(config["mcpServers"]), {"gitkb"})
        server = config["mcpServers"]["gitkb"]
        names = server["env_vars"]
        self.assertIsInstance(names, list)
        self.assertTrue(all(isinstance(name, str) for name in names))
        self.assertEqual(set(names), FORWARDED)
        self.assertEqual(len(names), len(FORWARDED), "duplicate forwarding names")
        self.assertNotIn("env", server, "do not bake session identity into the plugin")
        self.assertEqual(server["command"], "git-kb")
        self.assertEqual(server["args"], ["mcp"])

    def test_cache_version_changes_with_launch_contract(self):
        """Ensure the installable version cannot reuse either pre-fix cache entry."""
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text())
        version = tuple(int(part) for part in manifest["version"].split("."))
        self.assertEqual(len(version), 3)
        self.assertGreaterEqual(version, (0, 1, 2), "old cache versions lack this contract")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")


if __name__ == "__main__":
    unittest.main()
