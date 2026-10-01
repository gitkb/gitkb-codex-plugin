#!/usr/bin/env python3
"""Negative controls for the shared real-Codex launch harness."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('mcp-startup.py')
spec = importlib.util.spec_from_file_location('startup', SCRIPT)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class PayloadTests(unittest.TestCase):
    def test_payload_rejects_links_and_special_files(self):
        """Never follow external links or block reading FIFOs in a package cache."""
        for kind in ('file', 'directory', 'dangling', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = root / 'plugin'
                payload.mkdir()
                (payload / 'manifest').write_text('approved')
                if kind == 'fifo':
                    os.mkfifo(payload / 'escape')
                else:
                    target = root / 'outside'
                    if kind == 'file':
                        target.write_text('outside')
                    elif kind == 'directory':
                        target.mkdir()
                        (target / 'secret').write_text('outside')
                    (payload / 'escape').symlink_to(target)
                with self.assertRaisesRegex(AssertionError, 'regular|symlink'):
                    startup.payload_fingerprint(payload)

    def test_payload_includes_executable_mode(self):
        """Cache equality must detect a lost hook executable bit as well as changed bytes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'hook'
            path.write_text('hook')
            path.chmod(0o644)
            before = startup.payload_fingerprint(root)
            path.chmod(0o755)
            self.assertNotEqual(before, startup.payload_fingerprint(root))

    def test_missing_or_empty_payload_is_not_valid(self):
        """Two absent trees must not pass as matching installations."""
        with tempfile.TemporaryDirectory() as directory:
            for root in (Path(directory), Path(directory) / 'missing'):
                with self.subTest(root=root), self.assertRaises(AssertionError):
                    startup.payload_fingerprint(root)

    def test_receipt_must_stay_inside_isolated_cache(self):
        """Reject external paths, relative paths, and symlink escapes before reading a cache."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "payload").write_text("external")
            cache = root / "codex/plugins/cache"
            cache.mkdir(parents=True)
            (cache / "link").symlink_to(outside, target_is_directory=True)
            for path in (outside, Path("outside"), cache / "link"):
                with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "escapes"):
                    startup.installed_payload({"installedPath": str(path)}, root)
            valid = cache / "gitkb/gitkb/0.1.2"
            valid.mkdir(parents=True)
            (valid / "payload").write_text("approved")
            self.assertEqual(startup.installed_payload({"installedPath": str(valid)}, root),
                             startup.payload_fingerprint(valid))

    def test_optimized_context_check_rejects_missing_or_leaked_values(self):
        """Exercise context mismatch and secret leakage checks in an actual -O interpreter."""
        for observed, expected in (({}, {"SESSION_ACTIVITY_RESOURCE_ID": "alpha"}),
                                   ({"OPENAI_API_KEY": "synthetic-canary"}, {})):
            with self.subTest(observed=observed):
                code = ("import importlib.util; "
                        f"s=importlib.util.spec_from_file_location('startup', {str(SCRIPT)!r}); "
                        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                        f"m.check_context({observed!r}, {expected!r}, 'negative control')")
                result = subprocess.run([sys.executable, "-O", "-B", "-c", code],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("AssertionError", result.stderr)

    def test_legacy_fixture_selects_one_entry_by_name(self):
        """Adding another catalog entry must not silently change the upgrade control's target."""
        other = {"name": "other", "source": {"source": "local", "path": "./other"}}
        catalog = {"plugins": [other, {"name": "gitkb", "source": {"source": "git-subdir"}}]}
        startup.use_legacy_source(catalog)
        self.assertEqual(catalog["plugins"][0]["source"], {"source": "local", "path": "./other"})
        self.assertEqual(catalog["plugins"][1]["source"], {"source": "local", "path": "./plugin"})
        for entries in ([], [other], [{"name": "gitkb"}, {"name": "gitkb"}]):
            with self.subTest(entries=entries), self.assertRaises(AssertionError):
                startup.use_legacy_source({"plugins": entries})


class ProtocolTests(unittest.TestCase):
    def exercise(self, mode, optimized=False):
        """Exercise faults in an owned fake app-server; its PID must be gone on return."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'fake-codex'
            capture = root / 'context.json'
            fake.write_text('#!' + sys.executable + '\n' + '''import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(__file__).parent
(root / 'pid').write_text(str(os.getpid()))
mode = ''' + repr(mode) + '''
if mode == 'descendant':
    code = 'import os, signal, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(' + repr(str(root / 'child-pid')) + ').write_text(str(os.getpid())); time.sleep(30)'
    subprocess.Popen([sys.executable, '-c', code])
    deadline = time.monotonic() + 5
    while not (root / 'child-pid').exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    print('{invalid json', flush=True)
    time.sleep(30)
elif mode == 'timeout':
    time.sleep(30)
elif mode == 'eof':
    sys.stderr.write('synthetic diagnostic\\n')
    sys.exit(0)
elif mode == 'flood':
    sys.stdout.write('x' * (2 * 1024 * 1024))
    sys.stdout.flush()
    time.sleep(30)
elif mode == 'malformed':
    print('{invalid json', flush=True)
    time.sleep(30)
else:
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        result = {} if request['id'] == 1 else {'data': [
            {'name': 'gitkb', 'pluginId': 'wrong' if mode == 'identity' else 'gitkb@gitkb',
             'tools': {} if mode == 'tools' else {'context_probe': {}}}]}
        if mode != 'capture':
            (root / 'context.json').write_text('{}')
        print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''')
            fake.chmod(0o755)
            env = {'PATH': os.defpath, 'HOME': directory}
            if optimized:
                probe = root / 'probe.py'
                probe.write_text('import importlib.util\nfrom pathlib import Path\n'
                    f'spec = importlib.util.spec_from_file_location("startup", {str(SCRIPT)!r})\n'
                    'module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n'
                    f'module.inspect_child({str(fake)!r}, {env!r}, Path({directory!r}), '
                    f'Path({str(capture)!r}), response_timeout=1)\n')
                result = subprocess.run([sys.executable, '-O', '-B', str(probe)],
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, 'optimized Python accepted invalid MCP state')
                self.assertIn('AssertionError', result.stderr)
            else:
                with self.assertRaises(AssertionError) as failure:
                    startup.inspect_child(str(fake), env, root, capture, response_timeout=1)
                if mode == 'eof':
                    self.assertIn('synthetic diagnostic', str(failure.exception))
                if mode == 'flood':
                    self.assertIn('limit', str(failure.exception))
            pid = int((root / 'pid').read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            if mode == 'descendant':
                child = int((root / 'child-pid').read_text())
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    state = subprocess.run(['ps', '-p', str(child), '-o', 'stat='],
                                           capture_output=True, text=True, timeout=5).stdout.strip()
                    if not state or state.startswith('Z'):
                        break  # A zombie is stopped; the host's init process owns its reaping.
                    time.sleep(0.05)
                else:
                    self.fail('TERM-resistant owned descendant survived cleanup')

    def test_faults_fail_and_reap_the_app_server(self):
        """Fail closed on timeout, EOF, malformed/flooding output, wrong identity/tools, or no child."""
        for mode in ('timeout', 'eof', 'flood', 'malformed', 'identity', 'tools', 'capture', 'descendant'):
            with self.subTest(mode=mode):
                self.exercise(mode)

    def test_optimized_python_keeps_security_checks(self):
        """Python -O must not disable installed identity/tool/child checks."""
        for mode in ('identity', 'tools', 'capture'):
            with self.subTest(mode=mode):
                self.exercise(mode, optimized=True)


if __name__ == '__main__':
    unittest.main()
