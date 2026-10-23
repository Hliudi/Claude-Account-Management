import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs_accounts as cs
from cs_session import HandoffError, ownership_dir, request_handoff, writer_busy


THREAD = "12345678-1234-4234-8234-123456789abc"
REPO = Path(__file__).resolve().parents[1]


class Terminal:
    def __init__(self, command, env, cwd):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
        self.process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                        env=env, cwd=cwd, start_new_session=True)
        os.close(slave)
        self.output = b""

    def send(self, value):
        os.write(self.master, value)

    def expect(self, value, timeout=15):
        deadline = time.monotonic() + timeout
        while value not in self.output:
            if time.monotonic() > deadline:
                raise AssertionError(f"Missing {value!r}: {self.output[-6000:]!r}")
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    raise AssertionError(f"Exited before {value!r}: {self.output[-6000:]!r}")
                self.output += chunk
                if b"\x1b[6n" in chunk:
                    self.send(b"\x1b[1;1R")
                if b"\x1b[c" in chunk:
                    self.send(b"\x1b[?1;2c")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(16)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        os.close(self.master)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.account = {"name": "proxy", "type": "proxy", "base_url": "https://example.invalid/v1",
                        "api_key": "synthetic", "model": "test-model"}

    def command(self, args):
        with patch.object(cs, "launch", return_value=0) as launch:
            cs.cmd_resume(args, account=self.account)
            return launch.call_args

    def test_value_flags_remain_attached_before_and_after_id(self):
        for args in (["--model", "chosen", THREAD], [THREAD, "--model", "chosen"]):
            call = self.command(args)
            self.assertEqual(call.args[1], ["--model", "chosen", "resume", THREAD])
            self.assertEqual(call.kwargs["thread_id"], THREAD)

    def test_config_and_prompt_preserved(self):
        call = self.command(["-c", 'model_reasoning_effort="high"', THREAD, "continue here"])
        self.assertEqual(call.args[1], ["-c", 'model_reasoning_effort="high"', "resume", THREAD, "continue here"])

    def test_last_resolves_across_providers_and_obeys_cwd(self):
        sessions = [{"id": THREAD, "cwd": os.getcwd(), "model_provider": "openai"}]
        with patch.object(cs, "load_sessions", return_value=sessions):
            self.assertEqual(self.command(["--last"]).kwargs["thread_id"], THREAD)
        with patch.object(cs, "load_sessions", return_value=[]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.command(["--last"])

    def test_number_resolves_both_entrypoints(self):
        with patch.object(cs, "resolve_index", return_value=THREAD), patch.object(cs, "launch") as launch:
            cs.native_cli(["resume", "1"])
            self.assertEqual(launch.call_args.args[0]["type"], "native")
            self.assertEqual(launch.call_args.kwargs["thread_id"], THREAD)

    def test_official_return_explicitly_overrides_persisted_proxy(self):
        with patch.dict(os.environ, {"CS_PROXY_API_KEY": "synthetic", "OPENAI_BASE_URL": "https://invalid"}):
            command, env = cs.build_command({"name": "native", "type": "native"}, ["resume", THREAD])
        self.assertEqual(command[:2], ["-c", 'model_provider="openai"'])
        self.assertNotIn("CS_PROXY_API_KEY", env)
        self.assertNotIn("OPENAI_BASE_URL", env)

    def test_exec_option_does_not_enter_terminal_mode(self):
        call = self.command(["--exec", "--json", THREAD, "continue"])
        self.assertTrue(call.kwargs["exec_mode"])
        self.assertEqual(call.args[1], ["exec", "--json", "resume", THREAD, "continue"])

    def test_invalid_index_does_not_launch(self):
        with patch.object(cs, "resolve_index", return_value=None), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.command(["999"])

    def test_install_preserves_downstream_launcher_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="cs-install-") as temporary:
            base = Path(temporary)
            binary = base / "original-codex"
            binary.touch()
            binary.chmod(0o700)
            bindir = base / ".local/bin"
            bindir.mkdir(parents=True)
            (bindir / "codex").symlink_to(binary)
            with patch.object(Path, "home", return_value=base), patch.object(cs.CSDir, "ROOT", base / "switch"), \
                 patch.object(cs, "find_codex", return_value=str(bindir / "codex")), \
                 contextlib.redirect_stdout(io.StringIO()):
                cs.cmd_install()
            saved = json.loads((base / "switch/launcher.json").read_text())
            self.assertEqual(saved["binary"], str(binary))
            with patch.object(Path, "home", return_value=base), patch.object(cs.CSDir, "ROOT", base / "switch"), \
                 patch.dict(os.environ, {"CODEX_CLI_PATH": ""}), contextlib.redirect_stdout(io.StringIO()):
                cs.cmd_install()
            self.assertEqual((bindir / "codex").resolve(), REPO / "codex-cs")
            self.assertEqual((base / ".bashrc").read_text().count("# >>> cs session handoff >>>"), 1)


@unittest.skipUnless(sys.platform == "linux", "Linux process locks")
class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cs-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.base / "switch"
        self.root.mkdir()
        (self.root / "accounts.json").write_text(json.dumps({"current": "proxy", "accounts": {
            "proxy": {"name": "proxy", "type": "proxy", "base_url": "https://example.invalid/v1",
                      "api_key": "synthetic", "model": "test-model"}}}))
        fixture = REPO / "tests/fixtures/fake_codex.py"
        self.binary = self.base / "codex"
        self.binary.write_text(fixture.read_text())
        self.binary.chmod(0o700)
        self.env = dict(os.environ, CODEX_HOME=str(self.home), CS_DIR=str(self.root),
                        CODEX_CLI_PATH=str(self.binary), TEST_THREAD_ID=THREAD, TERM="xterm-256color")

    def terminal(self, *args):
        terminal = Terminal([sys.executable, "-B", str(REPO / "cs_accounts.py"), *args], self.env, self.base)
        self.addCleanup(terminal.close)
        return terminal

    def test_native_proxy_native_handoff_preserves_id_and_history(self):
        first = self.terminal("--native-cli")
        first.expect(b"READY openai previous=0")
        first.send(b"native turn\r")
        first.expect(b"SAVED")
        self.assertTrue(writer_busy(self.home, THREAD))
        second = self.terminal("resume", THREAD)
        second.expect(b"READY cs_proxy previous=1")
        first.process.wait(5)
        self.assertEqual(first.process.returncode, 0)
        second.send(b"proxy turn\r")
        second.expect(b"SAVED")
        second.send(b"/exit\r")
        self.assertEqual(second.process.wait(5), 0)
        self.assertFalse(writer_busy(self.home, THREAD))
        third = self.terminal("--native-cli", "resume", THREAD)
        third.expect(b"READY openai previous=2")
        third.send(b"native again\r")
        third.expect(b"SAVED")
        third.send(b"/exit\r")
        self.assertEqual(third.process.wait(5), 0)
        self.assertFalse(writer_busy(self.home, THREAD))
        rows = [json.loads(line) for line in (self.home / "transcript.jsonl").read_text().splitlines()]
        self.assertEqual([row["thread_id"] for row in rows], [THREAD] * 3)
        self.assertEqual([row["provider"] for row in rows], ["openai", "cs_proxy", "openai"])
        self.assertEqual([row["message"] for row in rows], ["native turn", "proxy turn", "native again"])
        self.assertFalse(list(self.root.glob("ownership/*/*.sock")))

    def test_reverse_handoff_releases_proxy_without_manual_exit(self):
        first = self.terminal("resume", THREAD)
        first.expect(b"READY cs_proxy")
        second = self.terminal("--native-cli", "resume", THREAD)
        second.expect(b"READY openai")
        self.assertEqual(first.process.wait(5), 0)
        second.send(b"/exit\r")
        self.assertEqual(second.process.wait(5), 0)
        self.assertFalse(writer_busy(self.home, THREAD))

    def test_unmanaged_writer_is_not_unlinked_or_killed(self):
        directory = self.home / "thread-writer-locks"
        directory.mkdir()
        path = directory / f"{THREAD}.lock"
        with path.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            inode = path.stat().st_ino
            with self.assertRaisesRegex(HandoffError, "unmanaged"):
                request_handoff(ownership_dir(self.root, self.home), self.home, THREAD)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertTrue(writer_busy(self.home, THREAD))
        self.assertFalse(writer_busy(self.home, THREAD))

    def test_stale_unlocked_file_is_safe_to_resume_without_unlink(self):
        directory = self.home / "thread-writer-locks"
        directory.mkdir()
        path = directory / f"{THREAD}.lock"
        path.touch()
        inode = path.stat().st_ino
        request_handoff(ownership_dir(self.root, self.home), self.home, THREAD)
        self.assertEqual(path.stat().st_ino, inode)

    def test_wrapper_crash_releases_terminal_writer(self):
        terminal = self.terminal("resume", THREAD)
        terminal.expect(b"READY cs_proxy")
        terminal.process.kill()
        terminal.process.wait(5)
        deadline = time.monotonic() + 5
        while writer_busy(self.home, THREAD) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(writer_busy(self.home, THREAD))
        next_terminal = self.terminal("--native-cli", "resume", THREAD)
        next_terminal.expect(b"READY openai")
        next_terminal.send(b"/exit\r")
        self.assertEqual(next_terminal.process.wait(5), 0)
