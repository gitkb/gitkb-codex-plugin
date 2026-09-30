#!/usr/bin/env python3
"""Install the real package in an isolated Codex home and inspect its MCP child.

Uses only initialize and mcpServerStatus/list; never starts a thread or model turn.
The fake GitKB server captures synthetic context without running an activity sink.
"""
import argparse
import json
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NAMES = (
    "SESSION_ACTIVITY_RESOURCE_ID", "SESSION_ACTIVITY_RESOURCE_KIND",
    "SESSION_ACTIVITY_PROVIDER", "SESSION_ACTIVITY_CONTEXT_FILE",
    "SESSION_ACTIVITY_SINK_COMMAND", "SESSION_ACTIVITY_SINK_ARGS_JSON",
    "ATC_ROOT", "ATC_CONFIG",
)
FORBIDDEN = ("MCP_ENV_UNRELATED_SECRET", "OPENAI_API_KEY", "ATC_SESSION_ID", "CODEX_THREAD_ID")


def run(codex, args, env, cwd):
    """Run a bounded plugin installation command with only the fixture environment."""
    result = subprocess.run([codex, *args], env=env, cwd=cwd,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(f"Codex {args} failed: {result.stdout} {result.stderr}")


def inspect_child(codex, env, root, capture):
    """Negotiate packaged MCP tools, capture context, and clean up the owned process group."""
    capture.unlink(missing_ok=True)
    with (root / "stderr.log").open("w+") as stderr:
        process = subprocess.Popen([codex, "app-server"], env=env, cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = bytearray()

        def send(message):
            """Write one newline-delimited app-server request or notification."""
            process.stdin.write((json.dumps(message) + "\n").encode())
            process.stdin.flush()

        def response(request_id):
            """Read the matching reply with a deadline, ignoring asynchronous notifications."""
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    message = json.loads(line)
                    if message.get("id") == request_id:
                        if "error" in message:
                            raise AssertionError(message["error"])
                        return message["result"]
                if selector.select(timeout=0.1):
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    pending.extend(chunk)
            stderr.flush()
            stderr.seek(0)
            raise AssertionError(f"No response to {request_id}: {stderr.read()[-4000:]}")

        try:
            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "gitkb-package-test", "version": "1"},
                "capabilities": {"experimentalApi": True}}})
            response(1)
            send({"method": "initialized"})
            send({"id": 2, "method": "mcpServerStatus/list", "params": {}})
            servers = response(2)["data"]
            server = next(item for item in servers if item["name"] == "gitkb")
            assert server["pluginId"] == "gitkb@gitkb", server
            assert "context_probe" in server["tools"], server
            assert capture.exists(), "packaged MCP child did not start"
            return json.loads(capture.read_text())
        finally:
            selector.close()
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()


def main():
    """Verify session isolation and the credential boundary through a real package install."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True, help="Provider Codex binary, not an ATC shim")
    parser.add_argument("--marketplace", default=".")
    args = parser.parse_args()
    codex = shutil.which(args.codex)
    if not codex:
        parser.error("Codex binary not found")
    codex = str(Path(codex).resolve())
    marketplace = str(Path(args.marketplace).resolve())
    with tempfile.TemporaryDirectory(prefix="gitkb-mcp-context-") as directory:
        root = Path(directory)
        (root / "bin").mkdir()
        (root / "codex").mkdir()
        capture = root / "context.json"
        fixture = root / "mcp_fixture.py"
        fixture.write_text('''import json, os, sys
if sys.argv[1:] != ["mcp"]:
    sys.exit(0)
''' + f"names = {NAMES + FORBIDDEN!r}\n" +
            f"with open({str(capture)!r}, 'w') as output:\n" +
            "    json.dump({name: os.environ[name] for name in names if name in os.environ}, output)\n" + '''for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "gitkb-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "context_probe", "description": "Launch boundary probe",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                          "error": {"code": -32601, "message": "Method not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
''')
        shim = root / "bin/git-kb"
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fixture))} \"$@\"\n")
        shim.chmod(0o755)
        # Construct the environment: never inherit the developer's keys, session or home.
        base_env = {"HOME": directory, "CODEX_HOME": str(root / "codex"),
                    "PATH": str(root / "bin") + os.pathsep + os.defpath,
                    "TMPDIR": directory}
        node = shutil.which("node")
        if node:
            # npm Codex launchers use /usr/bin/env node; keep that interpreter reachable.
            base_env["PATH"] += os.pathsep + str(Path(node).parent)
        run(codex, ["plugin", "marketplace", "add", marketplace, "--json"], base_env, root)
        run(codex, ["plugin", "add", "gitkb@gitkb", "--json"], base_env, root)
        for session in ("alpha", "beta", None):
            expected = {} if session is None else dict(zip(NAMES, (
                f"xses-{session}", "external", "codex",
                str(root / f"{session} path 'quoted' ü.json"),
                str(root / "sink path"), json.dumps(["activity", "record", "--json"]),
                str(root / f"{session} registry"), str(root / f"{session} config.toml"))))
            env = dict(base_env, **expected, **{name: "synthetic-canary" for name in FORBIDDEN})
            observed = inspect_child(codex, env, root, capture)
            assert observed == expected, f"{session or 'unshimmed'}: expected {expected}, got {observed}"
            print(f"PASS: {session or 'unshimmed'} MCP context and credential isolation")


if __name__ == "__main__":
    main()
