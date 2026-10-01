#!/usr/bin/env python3
"""Install the real package in an isolated Codex home and inspect its MCP child.

Uses only initialize and mcpServerStatus/list; never starts a thread or model turn.
The fake GitKB server captures synthetic context without running an activity sink.
"""
import argparse
import hashlib
import json
import os
import selectors
import shlex
import shutil
import signal
import stat
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
    return result.stdout


def inspect_child(codex, env, root, capture, *, response_timeout=15):
    """Negotiate packaged MCP tools, capture context, and clean up the owned process group."""
    capture.unlink(missing_ok=True)
    with (root / "stderr.log").open("w+b") as stderr:
        process = subprocess.Popen([codex, "app-server"], env=env, cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = bytearray()

        def send(message):
            """Write one newline-delimited app-server request or notification."""
            try:
                process.stdin.write((json.dumps(message) + "\n").encode())
                process.stdin.flush()
            except BrokenPipeError as error:
                raise AssertionError(f"App-server closed stdin during {message['method']}") from error

        def response(request_id):
            """Read the matching reply with a deadline, ignoring asynchronous notifications."""
            deadline = time.monotonic() + response_timeout
            while time.monotonic() < deadline:
                while b"\n" in pending and time.monotonic() < deadline:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeDecodeError) as error:
                        raise AssertionError(f"Invalid app-server JSON while waiting for {request_id}") from error
                    if not isinstance(message, dict):
                        raise AssertionError(f"Invalid app-server message while waiting for {request_id}")
                    if message.get("id") == request_id:
                        if "error" in message:
                            raise AssertionError(message["error"])
                        if "result" not in message:
                            raise AssertionError(f"App-server response {request_id} has no result")
                        return message["result"]
                if selector.select(timeout=0.1):
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    pending.extend(chunk)
                    if len(pending) > 1024 * 1024:
                        raise AssertionError(f"App-server output exceeds 1 MiB limit for {request_id}")
            stderr.flush()
            stderr.seek(0, os.SEEK_END)
            stderr.seek(max(0, stderr.tell() - 4000))
            diagnostic = stderr.read(4000).decode("utf-8", errors="replace")
            raise AssertionError(f"No response to {request_id} (timeout or EOF): {diagnostic}")

        try:
            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "gitkb-package-test", "version": "1"},
                "capabilities": {"experimentalApi": True}}})
            response(1)
            send({"method": "initialized"})
            send({"id": 2, "method": "mcpServerStatus/list", "params": {}})
            servers = response(2)["data"]
            matches = [item for item in servers if item.get("name") == "gitkb"]
            if len(matches) != 1 or matches[0].get("pluginId") != "gitkb@gitkb":
                raise AssertionError(f"Expected one packaged gitkb@gitkb server: {matches}")
            if "context_probe" not in matches[0].get("tools", {}):
                raise AssertionError(f"Packaged MCP probe tool missing: {matches[0]}")
            if not capture.is_file():
                raise AssertionError("Packaged MCP child did not start")
            return json.loads(capture.read_text())
        finally:
            selector.close()
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                if process.poll() is None:
                    raise
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            # A descendant may ignore TERM even if the app-server itself exits promptly.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                if process.poll() is None:
                    raise
            process.stdin.close()
            process.stdout.close()


def payload_fingerprint(root):
    """Hash the package tree so source resolution cannot silently install another payload."""
    if root.is_symlink() or not root.is_dir():
        raise AssertionError(f"Missing package directory or symlink: {root}")
    fingerprint = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise AssertionError(f"Package entry must be a regular file, not a symlink or special file: {path}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        fingerprint[str(path.relative_to(root))] = (bool(mode & 0o111), digest)
    if not fingerprint:
        raise AssertionError(f"Empty package: {root}")
    return fingerprint


def installed_payload(receipt, root):
    """Read only an installed cache beneath the fixture's isolated Codex home."""
    installed = Path(receipt["installedPath"])
    cache = (root / "codex/plugins/cache").resolve()
    if not installed.is_absolute() or not installed.resolve().is_relative_to(cache):
        raise AssertionError(f"Installed path escapes isolated plugin cache: {installed}")
    return payload_fingerprint(installed)


def check_context(observed, expected, label):
    """Keep exact context and credential checks active even under optimized Python."""
    if observed != expected:
        raise AssertionError(f"{label}: expected {expected}, got {observed}")


def use_legacy_source(catalog):
    """Point exactly one named GitKB entry at the temporary legacy fixture."""
    entries = [entry for entry in catalog["plugins"] if entry.get("name") == "gitkb"]
    if len(entries) != 1:
        raise AssertionError(f"Expected one gitkb catalog entry: {entries}")
    entries[0]["source"] = {"source": "local", "path": "./plugin"}


def main():
    """Verify session isolation and the credential boundary through a real package install."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True, help="Provider Codex binary, not an ATC shim")
    parser.add_argument("--marketplace", default=".")
    parser.add_argument("--legacy-first", action="store_true",
                        help="Upgrade an installed bundled 0.1.0 package in the same Codex home")
    parser.add_argument("--expected-plugin-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "plugin",
                        help="Canonical package to compare with the installed cache")
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
        expected_root = args.expected_plugin_root.resolve()
        if not (expected_root / ".codex-plugin/plugin.json").is_file():
            raise AssertionError(f"Missing canonical package: {expected_root}")
        expected_payload = payload_fingerprint(expected_root)
        if args.legacy_first:
            # A temporary old catalog/package is a test fixture, never another maintained source.
            old_catalog = root / "legacy-marketplace"
            catalog_path = old_catalog / ".agents/plugins/marketplace.json"
            catalog_path.parent.mkdir(parents=True)
            old_plugin = old_catalog / "plugin"
            shutil.copytree(expected_root, old_plugin)
            manifest_path = old_plugin / ".codex-plugin/plugin.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["version"] = "0.1.0"
            manifest_path.write_text(json.dumps(manifest))
            mcp_path = old_plugin / ".mcp.json"
            mcp = json.loads(mcp_path.read_text())
            mcp["mcpServers"]["gitkb"].pop("env_vars", None)
            mcp_path.write_text(json.dumps(mcp))
            target_catalog = Path(marketplace) / ".agents/plugins/marketplace.json"
            legacy_catalog = json.loads(target_catalog.read_text())
            use_legacy_source(legacy_catalog)
            catalog_path.write_text(json.dumps(legacy_catalog))
            run(codex, ["plugin", "marketplace", "add", str(old_catalog), "--json"], base_env, root)
            legacy = json.loads(run(codex, ["plugin", "add", "gitkb@gitkb", "--json"], base_env, root))
            if legacy.get("version") != "0.1.0" or installed_payload(legacy, root) != payload_fingerprint(old_plugin):
                raise AssertionError("Legacy fixture was not installed")
            legacy_env = dict(base_env, **{name: "synthetic-canary" for name in NAMES + FORBIDDEN})
            check_context(inspect_child(codex, legacy_env, root, capture), {}, "legacy missing-context control")
            # Publish the target catalog into that same registered directory, retaining installation identity.
            shutil.rmtree(old_plugin)
            shutil.copytree(expected_root, old_plugin)
            catalog_path.write_text(target_catalog.read_text())
            print("PASS: legacy 0.1.0 missing-context control; upgrading existing installation")
        else:
            run(codex, ["plugin", "marketplace", "add", marketplace, "--json"], base_env, root)
        receipt = json.loads(run(codex, ["plugin", "add", "gitkb@gitkb", "--json"], base_env, root))
        actual_payload = installed_payload(receipt, root)
        if actual_payload != expected_payload:
            changed = sorted(path for path in actual_payload.keys() | expected_payload.keys()
                             if actual_payload.get(path) != expected_payload.get(path))
            raise AssertionError(f"Installed payload differs from canonical source: {changed}")
        print(f"PASS: installed canonical payload ({len(actual_payload)} files)")
        for session in ("alpha", "beta", None):
            expected = {} if session is None else dict(zip(NAMES, (
                f"xses-{session}", "external", "codex",
                str(root / f"{session} path 'quoted' ü.json"),
                str(root / "sink path"), json.dumps(["activity", "record", "--json"]),
                str(root / f"{session} registry"), str(root / f"{session} config.toml"))))
            env = dict(base_env, **expected, **{name: "synthetic-canary" for name in FORBIDDEN})
            observed = inspect_child(codex, env, root, capture)
            check_context(observed, expected, session or "unshimmed")
            print(f"PASS: {session or 'unshimmed'} MCP context and credential isolation")


if __name__ == "__main__":
    main()
