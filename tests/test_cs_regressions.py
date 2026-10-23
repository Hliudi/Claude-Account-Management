import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs_accounts as cs
from cs_session import receive_json, reply_and_close, request_handoff, stop_child, write_all
from cs_storage import atomic_json, state_database


THREAD = "12345678-1234-4234-8234-123456789abc"


class RegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cs-regression-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.base / "switch"
        self.root.mkdir()
        for field, value in (("APP", self.home), ("ROOT", self.root), ("CONF", self.root / "accounts.json")):
            scoped = patch.object(cs.CSDir, field, value)
            scoped.start()
            self.addCleanup(scoped.stop)
        environment = patch.dict(os.environ, {"CODEX_SQLITE_HOME": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.account = {"name": "test", "type": "proxy", "base_url": "https://example.invalid/v1",
                        "api_key": "SYNTHETIC_SECRET", "model": "custom-model"}
        self.conf = {"current": "test", "accounts": {"test": self.account}}
        cs.write_conf(self.conf)

    def test_account_temp_file_is_private_before_publish(self):
        original = os.replace

        def check(source, destination):
            self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
            original(source, destination)

        with patch("cs_storage.os.replace", side_effect=check):
            cs.write_conf(self.conf)
        self.assertEqual(cs.read_conf(), self.conf)

    def test_atomic_write_refuses_symlink_and_ignores_old_fixed_temp(self):
        victim = self.base / "victim"
        victim.write_text("preserve")
        (self.root / ".accounts.json.tmp").symlink_to(victim)
        cs.write_conf(self.conf)
        self.assertEqual(victim.read_text(), "preserve")
        link = self.root / "link.json"
        link.symlink_to(victim)
        with self.assertRaises(OSError):
            atomic_json(link, self.conf)
        self.assertEqual(victim.read_text(), "preserve")

    def test_invalid_account_schema_does_not_expose_secret(self):
        cs.CSDir.CONF.write_text(json.dumps({"accounts": {"test": {"api_key": "SYNTHETIC_SECRET"}}}))
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
            cs.read_conf()
        self.assertNotIn("SYNTHETIC_SECRET", error.getvalue())

    def test_cancelled_force_add_preserves_credentials(self):
        original = cs.CSDir.CONF.read_bytes()
        with patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cs.main(["add", "--force", "test"])
        self.assertEqual(cs.CSDir.CONF.read_bytes(), original)

    def test_force_option_works_on_either_side_of_name(self):
        for args in (["--force", "test"], ["test", "--force"]):
            with patch.object(cs, "cmd_add") as add:
                cs.main(["add", *args])
                add.assert_called_once_with("test", force=True, preset=None)

    def test_poe_preset_uses_independent_model_key_and_responses(self):
        with patch("builtins.input", return_value="My-Poe-Bot"), \
             patch.object(cs.getpass, "getpass", return_value="POE_SYNTHETIC"), contextlib.redirect_stdout(io.StringIO()):
            cs.main(["add", "poe", "--preset", "poe"])
        account = cs.read_conf()["accounts"]["poe"]
        self.assertEqual(account["base_url"], "https://api.poe.com/v1")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "OFFICIAL_SYNTHETIC", "CODEX_ACCESS_TOKEN": "OLD_TOKEN"}):
            command, env = cs.build_command(account, [])
        provider = tomllib.loads(command[1])["model_providers"]["cs_poe"]
        self.assertEqual(provider["wire_api"], "responses")
        self.assertFalse(provider.get("requires_openai_auth", False))
        self.assertFalse(provider["supports_websockets"])
        self.assertIn('model="My-Poe-Bot"', command)
        self.assertNotIn("POE_SYNTHETIC", repr(command))
        self.assertEqual(env[cs.PROXY_ENV_KEY], "POE_SYNTHETIC")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_ACCESS_TOKEN", env)

    def test_poe_requires_explicit_model_before_save(self):
        with patch("builtins.input", return_value=""), patch.object(cs.getpass, "getpass", return_value="synthetic"), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cs.main(["add", "poe", "--preset", "poe"])
        self.assertNotIn("poe", cs.read_conf()["accounts"])

    def test_api_roots_reject_embedded_credentials_and_endpoint_suffix(self):
        for url in ["https://user:secret@example.invalid/v1", "https://example.invalid/v1/responses", "https://example.invalid/v1?api_key=secret"]:
            with self.assertRaises(ValueError):
                cs.validate_account({**self.account, "base_url": url})

    def test_provider_names_do_not_collide(self):
        self.assertNotEqual(cs.provider_id("my-proxy"), cs.provider_id("my_proxy"))
        command, _ = cs.build_command({**self.account, "name": "my-proxy"}, [])
        self.assertIn("cs_my-proxy", tomllib.loads(command[1])["model_providers"])

    def test_all_exec_resume_entrypoints_use_handoff_and_correct_account(self):
        for call in [lambda: cs.native_cli(["exec", "--json", "resume", THREAD, "continue"]),
                     lambda: cs.cmd_run("test", ["exec", "--json", "resume", THREAD, "continue"]),
                     lambda: cs.main(["exec", "--json", "resume", THREAD, "continue"])]:
            with patch.object(cs, "launch", return_value=0) as launch:
                call()
                self.assertEqual(launch.call_args.kwargs, {"thread_id": THREAD, "exec_mode": True})
                self.assertEqual(launch.call_args.args[1], ["exec", "--json", "resume", THREAD, "continue"])

    def test_literal_prompt_and_stdin_marker_are_preserved(self):
        for prompt in ["--help", "--last", "-"]:
            with patch.object(cs, "launch", return_value=0) as launch:
                cs.native_cli(["resume", THREAD, "--", prompt])
                self.assertEqual(launch.call_args.args[1], ["resume", THREAD, "--", prompt])

    def test_state_override_is_not_allowed_to_select_the_wrong_database(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(cs, "launch") as launch, self.assertRaises(SystemExit):
            cs.cmd_resume(["-c", 'sqlite_home="/other"', THREAD], account=self.account)
        launch.assert_not_called()

    def test_legacy_database_and_custom_location(self):
        database_home = self.base / "database"
        database_home.mkdir()
        database = database_home / "state_10.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE threads (id TEXT,cwd TEXT,updated_at INTEGER)")
            connection.execute("INSERT INTO threads VALUES (?,?,123)", (THREAD, str(self.base)))
        (database_home / "state_5.sqlite").touch()
        (self.home / "config.toml").write_text(f'sqlite_home = "{database_home}"\n')
        self.assertEqual(state_database(self.home), database)
        self.assertEqual(cs.load_sessions()[0]["updated_at_ms"], 123000)
        override = self.base / "override"
        override.mkdir()
        (override / "state_5.sqlite").touch()
        with patch.dict(os.environ, {"CODEX_SQLITE_HOME": str(override)}):
            self.assertEqual(state_database(self.home), database)
            (self.home / "config.toml").unlink()
            self.assertEqual(state_database(self.home), override / "state_5.sqlite")
        with patch.dict(os.environ, {"CODEX_SQLITE_HOME": "override"}):
            self.assertEqual(state_database(self.home, cwd=self.base), override / "state_5.sqlite")

    def test_session_numbers_are_scoped_and_empty_lists_clear_previous_numbers(self):
        atomic_json(self.root / "last-sessions.json", {"home": str(self.home), "ids": [THREAD]})
        self.assertEqual(cs.resolve_index("1"), THREAD)
        with patch.object(cs.CSDir, "APP", self.base / "other"):
            self.assertIsNone(cs.resolve_index("1"))
        with patch.object(cs, "load_sessions", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            cs.cmd_sessions()
        self.assertIsNone(cs.resolve_index("1"))

    def test_picker_reads_page_keys_and_chinese(self):
        for data, expected in [(b"\x1b[5~", "pgup"), (b"\x1b[6~", "pgdn"), ("会".encode(), "会"), (b"q", "q"), (b"\x03", "esc")]:
            reader, writer = os.pipe()
            try:
                os.write(writer, data)
                self.assertEqual(cs.read_picker_key(reader), expected)
            finally:
                os.close(reader)
                os.close(writer)

    def test_handoff_legacy_reply_and_fragmented_reply(self):
        for chunks in [[b'{"released":true}'], [b'{"relea', b'sed":true}\n']]:
            local, peer = socket.socketpair()
            try:
                for chunk in chunks:
                    peer.sendall(chunk)
                peer.shutdown(socket.SHUT_WR)
                self.assertEqual(receive_json(local), {"released": True})
            finally:
                local.close()
                peer.close()

    def test_disconnected_requester_does_not_raise_in_owner(self):
        local, peer = socket.socketpair()
        peer.close()
        reply_and_close(local, {"released": False})

    def test_unrelated_record_disappearing_during_scan_does_not_abort_handoff(self):
        record = self.root / "exited.json"
        record.write_text('{}')
        with patch.object(Path, "read_text", side_effect=FileNotFoundError), \
             patch("cs_session.writer_busy", side_effect=[True, False]):
            request_handoff(self.root, self.home, THREAD)

    def test_partial_terminal_writes_are_completed(self):
        captured = []

        def partial(descriptor, data):
            captured.append(bytes(data[:2]))
            return min(2, len(data))

        with patch("cs_session.os.write", side_effect=partial):
            write_all(1, b"abcdefg")
        self.assertEqual(b"".join(captured), b"abcdefg")

    def test_stuck_owned_process_cleanup_is_bounded(self):
        script = "import signal,time; signal.signal(signal.SIGHUP,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"
        process = subprocess.Popen([sys.executable, "-c", script], start_new_session=True, stdout=subprocess.PIPE)
        self.assertEqual(process.stdout.readline(), b"ready\n")
        started = time.monotonic()
        try:
            process.returncode = stop_child(process.pid, timeout=0.1)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
