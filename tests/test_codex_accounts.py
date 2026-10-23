"""Credential switching regression tests; all credentials and homes are synthetic."""
import contextlib
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_accounts as ca


def auth(name, refresh="initial"):
    return {"auth_mode": "chatgpt", "tokens": {
        "account_id": name, "access_token": "fake-access-" + refresh,
        "refresh_token": "fake-refresh-" + refresh,
    }, "last_refresh": refresh}


def jwt(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "fake-header." + encoded + ".fake-signature"


def auth_with_email(name="new", email="work@example.test"):
    value = auth(name)
    value["tokens"]["id_token"] = jwt({"email": email})
    return value


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ca-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CODEX_HOME": str(self.base / "app"),
            "CX_APP_HOME": str(self.base / "app"),
            "CX_HOME": str(self.base / "home"),
            "CA_CODEX_DIR": str(self.base / "accounts"),
            "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "CODEX_ACCESS_TOKEN": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.accounts = ca.Accounts()
        self.accounts.home.mkdir()
        self.accounts.app_home.mkdir()
        self.accounts.root.mkdir()
        self.put(self.accounts.app_home / "auth.json", auth("app-main"))
        self.app_auth_bytes = (self.accounts.app_home / "auth.json").read_bytes()
        self.put(self.accounts.auth, auth("original"))
        self.put(self.accounts.profile("work"), auth("work"))
        self.put(self.accounts.profile("personal"), auth("personal"))

    def tearDown(self):
        self.assertEqual((self.accounts.app_home / "auth.json").read_bytes(), self.app_auth_bytes,
                         "No cx operation may change the App login")

    def put(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_switch_refresh_and_restore_preserves_history(self):
        config = self.accounts.home / "config.toml"
        config.write_text('model = "custom"\n', encoding="utf-8")
        history = self.accounts.home / "history.jsonl"
        history.write_text("history unchanged", encoding="utf-8")
        self.accounts.use("work")
        self.put(self.accounts.auth, auth("work", "refreshed"))
        self.accounts.use("personal")
        self.assertEqual(self.accounts.load(self.accounts.profile("work")), auth("work", "refreshed"))
        self.accounts.use("--off")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))
        self.assertFalse(self.accounts.state.exists())
        self.assertEqual(config.read_text(), 'model = "custom"\n')
        self.assertEqual(history.read_text(), "history unchanged")

    def test_restoring_original_uses_refreshed_tokens(self):
        self.accounts.add("original")
        self.accounts.use("original")
        self.put(self.accounts.auth, auth("original", "newest"))
        self.accounts.use("work")
        self.accounts.use("--off")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original", "newest"))

    def test_restore_absent_login(self):
        self.accounts.auth.unlink()
        self.accounts.use("work")
        self.accounts.use("--off")
        self.assertFalse(self.accounts.auth.exists())

    def test_selected_account_refresh_is_not_overwritten_by_stale_snapshot(self):
        self.accounts.use("work")
        self.put(self.accounts.auth, auth("work", "newest"))
        self.accounts.use("work")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("work", "newest"))

    def test_new_saved_login_is_not_overwritten_by_older_active_tokens(self):
        self.accounts.use("work")
        ca.write_json(self.accounts.profile("work"), auth("work", "new-login"))
        # Explicit timestamps avoid depending on the filesystem's clock resolution.
        old_time = self.accounts.auth.stat().st_mtime_ns
        os.utime(self.accounts.profile("work"), ns=(old_time + 1000000, old_time + 1000000))
        self.accounts.use("work")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("work", "new-login"))

    def test_invalid_restore_backup_never_removes_active_login(self):
        self.accounts.use("work")
        self.put(self.accounts.state, {"home": str(self.accounts.home)})
        with self.assertRaises(ca.AccountError):
            self.accounts.use("--off")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("work"))

    def test_unknown_external_login_must_be_saved(self):
        self.accounts.use("work")
        self.put(self.accounts.auth, auth("external"))
        with self.assertRaises(ca.AccountError):
            self.accounts.use("personal")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("external"))
        self.accounts.add("external")
        self.accounts.use("personal")
        self.assertEqual(self.accounts.load(self.accounts.profile("external")), auth("external"))

    def test_missing_target_does_not_change_login(self):
        with self.assertRaises(ca.AccountError):
            self.accounts.use("missing")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))
        self.assertFalse(self.accounts.state.exists())

    def test_add_requires_explicit_overwrite(self):
        with self.assertRaises(ca.AccountError):
            self.accounts.add("work")
        self.accounts.add("work", True)
        self.assertEqual(self.accounts.load(self.accounts.profile("work")), auth("original"))

    def test_config_storage_modes_use_scoped_cli_override_without_mutation(self):
        config = self.accounts.home / "config.toml"
        for value in ("keyring", "auto", "ephemeral"):
            config.write_text(f'cli_auth_credentials_store = "{value}"', encoding="utf-8")
            self.accounts.file_store()
            self.assertIn('cli_auth_credentials_store="file"', self.accounts.overrides())
            self.assertEqual(config.read_text(), f'cli_auth_credentials_store = "{value}"')
        config.write_text('[profiles.example]\ncli_auth_credentials_store = "keyring"', encoding="utf-8")
        self.accounts.file_store()  # nested settings are not global settings

    def test_different_home_does_not_change_login(self):
        self.accounts.use("work")
        other = self.base / "other"
        other.mkdir()
        self.put(other / "auth.json", auth("unrelated"))
        with patch.dict(os.environ, {"CX_HOME": str(other)}):
            with self.assertRaises(ca.AccountError):
                ca.Accounts().use("personal")
        self.assertEqual(self.accounts.load(other / "auth.json"), auth("unrelated"))

    def test_credential_and_config_parse_errors_hide_contents(self):
        secret = "fake-super-secret-value"
        self.accounts.auth.write_text('{"secret": "' + secret, encoding="utf-8")
        with self.assertRaises(ca.AccountError) as raised:
            self.accounts.load(self.accounts.auth)
        self.assertNotIn(secret, str(raised.exception))
        (self.accounts.home / "config.toml").write_text(secret + " = [", encoding="utf-8")
        with self.assertRaises(ca.AccountError) as raised:
            self.accounts.file_store()
        self.assertNotIn(secret, str(raised.exception))

    def test_invalid_names_and_git_storage_are_rejected(self):
        for name in ("../outside", "", "work/personal", "--bad", "login", "CODEX HOME"):
            with self.assertRaises(ca.AccountError):
                self.accounts.profile(name)
        (self.base / ".git").write_text("gitdir: fake", encoding="utf-8")
        with self.assertRaises(ca.AccountError):
            ca.write_json(self.accounts.profile("outside"), auth("secret"))

    def test_nested_homes_are_rejected(self):
        with patch.dict(os.environ, {"CA_CODEX_DIR": str(self.accounts.home / "profiles")}):
            with self.assertRaises(ca.AccountError):
                ca.Accounts()

    def test_identity_accepts_api_and_legacy_oauth_without_exposing_keys(self):
        key = "fake-api-key"
        result = ca.identity({"OPENAI_API_KEY": key})
        self.assertEqual(result[0], "api")
        self.assertNotIn(key, str(result))
        legacy = auth("legacy")
        del legacy["auth_mode"]
        self.assertEqual(ca.identity(legacy)[0], "chatgpt")
        for value in (None, [], {"tokens": {}}, {"OPENAI_API_KEY": 123}):
            with self.assertRaises(ca.AccountError):
                ca.identity(value)

    def fake_binary(self, login_auth=None):
        login_auth = auth_with_email() if login_auth is None else login_auth
        script = self.base / "fake_codex.py"
        script.write_text(
            'import json, os, pathlib, sys\n'
            'home = pathlib.Path(os.environ["CODEX_HOME"])\n'
            'pathlib.Path(__file__).with_name("fake_args.json").write_text(json.dumps(sys.argv[1:]))\n'
            'if "login" in sys.argv:\n'
            '    data = {"OPENAI_API_KEY": "fake-login-key"} if "--with-api-key" in sys.argv else json.loads(' + repr(json.dumps(login_auth)) + ')\n'
            '    (home / "auth.json").write_text(json.dumps(data))\n'
            'elif "--exit-seven" in sys.argv:\n'
            '    sys.exit(7)\n'
            'else:\n'
            '    (home / "received.json").write_text(json.dumps(sys.argv[1:]))\n', encoding="utf-8")
        return [sys.executable, str(script)]

    def test_isolated_login_preserves_live_auth(self):
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()), patch("builtins.input", return_value="y"):
            self.assertEqual(self.accounts.login("new"), 0)
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))
        self.assertEqual(ca.identity(self.accounts.load(self.accounts.profile("new")))[0], "chatgpt")
        self.assertIn("--device-auth", json.loads((self.base / "fake_args.json").read_text()))
        self.assertIn("Logged-in email: work@example.test", self.output.getvalue())
        self.assertNotIn("fake-access", self.output.getvalue())
        self.assertNotIn("fake-header", self.output.getvalue())
        self.assertFalse(list(self.accounts.root.glob("login-*")))

    def test_login_displays_email_before_confirmation_and_saving(self):
        def confirm(prompt):
            self.assertIn("Logged-in email: work@example.test", self.output.getvalue())
            self.assertIn("new", prompt)
            self.assertFalse(self.accounts.profile("new").exists())
            return "yes"
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()), patch("builtins.input", side_effect=confirm):
            self.accounts.login("new")
        self.assertEqual(self.accounts.load(self.accounts.profile("new")), auth_with_email())

    def test_cancelled_forced_login_never_overwrites_saved_or_live_auth(self):
        original_bytes = self.accounts.profile("work").read_bytes()
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()), patch("builtins.input", return_value=""):
            with self.assertRaises(ca.AccountError):
                self.accounts.login("work", force=True)
        self.assertEqual(self.accounts.profile("work").read_bytes(), original_bytes)
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))
        self.assertFalse(list(self.accounts.root.glob("login-*")))

    def test_missing_email_and_eof_do_not_save_login(self):
        with patch.object(self.accounts, "binary", return_value=self.fake_binary(auth("new"))), patch("builtins.input") as confirm:
            with self.assertRaises(ca.AccountError):
                self.accounts.login("new")
            confirm.assert_not_called()
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()), patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(ca.AccountError):
                self.accounts.login("new")
        self.assertFalse(self.accounts.profile("new").exists())
        self.assertFalse(list(self.accounts.root.glob("login-*")))

    def test_email_claim_fallback_and_unsafe_values(self):
        value = auth("fallback")
        value["tokens"]["access_token"] = jwt({"https://api.openai.com/profile": {"email": "fallback@example.test"}})
        self.assertEqual(ca.login_email(value), "fallback@example.test")
        for email in ("bad\n@example.test", "bad\x1b@example.test", "bad\u202e@example.test", "missing-at", 123):
            self.assertIsNone(ca.login_email(auth_with_email(email=email)))

    def test_api_key_login_does_not_request_email_confirmation(self):
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()), patch("builtins.input") as confirm:
            self.accounts.login("api", ["--with-api-key"])
            confirm.assert_not_called()
        self.assertEqual(ca.identity(self.accounts.load(self.accounts.profile("api")))[0], "api")
        self.assertNotIn("--device-auth", json.loads((self.base / "fake_args.json").read_text()))
        self.assertNotIn("fake-login-key", self.output.getvalue())

    def test_login_cli_default_and_explicit_browser_modes(self):
        with patch.object(ca, "Accounts", return_value=self.accounts), patch.object(self.accounts, "binary", return_value=self.fake_binary()), patch("builtins.input", return_value="y"):
            self.assertEqual(ca.main(["login", "default"]), 0)
            self.assertIn("--device-auth", json.loads((self.base / "fake_args.json").read_text()))
            self.assertEqual(ca.main(["login", "browser", "--browser"]), 0)
            self.assertNotIn("--device-auth", json.loads((self.base / "fake_args.json").read_text()))

    def test_login_failure_preserves_saved_profile(self):
        with patch.object(self.accounts, "binary", return_value=[sys.executable, "-c", "import sys; sys.exit(7)"]):
            self.assertEqual(self.accounts.login("work", [], force=True), 7)
        self.assertEqual(self.accounts.load(self.accounts.profile("work")), auth("work"))

    def test_launch_forwards_arguments_and_exit_status(self):
        args = ["exec", "a prompt with spaces", '--config', 'example="two words"']
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()):
            self.assertEqual(self.accounts.run("work", args), 0)
            self.assertEqual(json.loads((self.accounts.home / "received.json").read_text())[-len(args):], args)
            self.assertEqual(self.accounts.run("work", ["--exit-seven"]), 7)
        self.assertEqual(self.accounts.current_name(), "work")

    def test_launch_rejects_env_override_and_logout(self):
        with patch.dict(os.environ, {"CODEX_API_KEY": "fake-override"}):
            with self.assertRaises(ca.AccountError):
                self.accounts.run("work", [])
        for args in (["logout"], ["app"], ["agents"], ["--remote=ws://localhost:1"], ["-c", 'sqlite_home="app"']):
            with self.assertRaises(ca.AccountError):
                self.accounts.run("work", args)

    def test_cli_and_app_paths_must_never_overlap(self):
        for value in (self.accounts.app_home, self.accounts.app_home / "cli", self.accounts.app_home.parent):
            with patch.dict(os.environ, {"CX_HOME": str(value)}):
                with self.assertRaises(ca.AccountError):
                    ca.Accounts()

    def test_initial_import_copies_config_and_history_without_credentials_or_links(self):
        app = self.accounts.app_home
        (app / "config.toml").write_text('model="test-model"\ncli_auth_credentials_store="keyring"\n')
        (app / "AGENTS.md").write_text("project preferences")
        (app / "history.jsonl").write_text("history snapshot")
        sessions = app / "sessions" / "2026"
        sessions.mkdir(parents=True)
        (sessions / "chat.jsonl").write_text("synthetic context")
        daemon = app / "app-server-daemon"
        daemon.mkdir()
        (daemon / "socket").write_text("do not copy")
        self.accounts.auth.unlink()
        self.accounts.prepare()
        self.assertFalse(self.accounts.auth.exists())
        self.assertFalse((self.accounts.home / "app-server-daemon").exists())
        self.assertEqual((self.accounts.home / "sessions/2026/chat.jsonl").read_text(), "synthetic context")
        self.assertEqual((self.accounts.home / "config.toml").read_text(), (app / "config.toml").read_text())
        (self.accounts.home / "AGENTS.md").write_text("CLI-only edit")
        self.assertEqual((app / "AGENTS.md").read_text(), "project preferences")
        self.accounts.prepare()
        self.assertEqual((self.accounts.home / "AGENTS.md").read_text(), "CLI-only edit")
        with self.assertRaises(ca.AccountError):
            self.accounts.add("main")  # must not silently import the App's login

    def test_sqlite_history_snapshot_includes_wal_and_rewrites_rollout_paths(self):
        app = self.accounts.app_home
        transcript = app / "sessions" / "chat.jsonl"
        transcript.parent.mkdir()
        transcript.write_text("preserved conversation")
        source = app / "state_5.sqlite"
        with contextlib.closing(sqlite3.connect(source)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, title TEXT)")
            conn.execute("INSERT INTO threads VALUES(?,?,?)", ("chat", str(transcript), "synthetic title"))
            if os.name == "nt":
                conn.execute("INSERT INTO threads VALUES(?,?,?)", ("long-path", "\\\\?\\" + str(transcript), "synthetic title"))
            conn.execute("CREATE TABLE remote_control_enrollments(secret TEXT)")
            conn.execute("INSERT INTO remote_control_enrollments VALUES('fake-secret')")
            conn.commit()
            self.accounts.prepare()  # source is still open; no raw db/WAL copying
            self.assertEqual(conn.execute("SELECT rollout_path FROM threads").fetchone()[0], str(transcript))
        with contextlib.closing(sqlite3.connect(self.accounts.home / "state_5.sqlite")) as snapshot:
            self.assertEqual(snapshot.execute("SELECT rollout_path FROM threads").fetchone()[0], str(self.accounts.home / "sessions/chat.jsonl"))
            self.assertTrue(all(row[0] == str(self.accounts.home / "sessions/chat.jsonl") for row in snapshot.execute("SELECT rollout_path FROM threads")))
            self.assertEqual(snapshot.execute("SELECT count(*) FROM remote_control_enrollments").fetchone()[0], 0)
            snapshot.execute("UPDATE threads SET title='CLI only'")
            snapshot.commit()
        with contextlib.closing(sqlite3.connect(source)) as conn:
            self.assertEqual(conn.execute("SELECT title FROM threads").fetchone()[0], "synthetic title")

    def test_cli_launch_scopes_home_sqlite_and_daemon_without_changing_parent_env(self):
        original = os.environ["CODEX_HOME"]
        with patch.object(self.accounts, "binary", return_value=self.fake_binary()):
            self.accounts.run("work", ["resume", "--last"])
        args = json.loads((self.accounts.home / "received.json").read_text())
        self.assertIn("--no-daemon", args)
        self.assertIn("sqlite_home=" + json.dumps(str(self.accounts.home)), args)
        self.assertEqual(os.environ["CODEX_HOME"], original)
        self.assertEqual(self.accounts.child_env()["CODEX_HOME"], str(self.accounts.home))
        self.assertEqual(self.accounts.child_env()["CODEX_SQLITE_HOME"], str(self.accounts.home))

    def test_legacy_shared_backup_is_never_restored_into_the_app(self):
        legacy = self.accounts.root / "switch-state.json"
        self.put(legacy, {"home": str(self.accounts.app_home), "original": auth("legacy-main")})
        data = legacy.read_bytes()
        self.accounts.use("work")
        self.accounts.use("--off")
        self.assertEqual(legacy.read_bytes(), data)
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))

    def test_forward_slash_marker_and_state_paths_are_equivalent(self):
        self.accounts.use("work")
        marker = self.accounts.home / ".cx-isolated.json"
        data = ca.read_json(marker)
        data["source"] = str(self.accounts.app_home).replace("\\", "/")
        self.put(marker, data)
        data = ca.read_json(self.accounts.state)
        data["home"] = str(self.accounts.home).replace("\\", "/")
        self.put(self.accounts.state, data)
        self.accounts.prepare()
        self.accounts.use("--off")
        self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))

    def test_lock_conflict_and_private_atomic_files(self):
        with self.accounts.lock():
            with self.assertRaises(ca.AccountError):
                with self.accounts.lock():
                    self.fail("lock should be exclusive")
            ca.write_json(self.accounts.profile("secure"), auth("secure"))
        if os.name != "nt":
            self.assertEqual(self.accounts.profile("secure").stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.accounts.root.stat().st_mode & 0o777, 0o700)
        self.assertFalse(list(self.accounts.root.glob(".ca-*")))

    def test_command_entrypoints_use_only_the_isolated_home(self):
        repo = Path(ca.__file__).parent
        commands = []
        if os.name == "nt":
            commands.append(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "cx.ps1")])
            if shutil.which("pwsh"):
                commands.append(["pwsh", "-NoProfile", "-File", str(repo / "cx.ps1")])
        else:
            commands.append(["bash", str(repo / "cx")])
        for command in commands:
            for args, expected in (
                (["help"], "Manage your own Codex accounts"),
                (["add", "terminal"], "Saved Codex account: terminal"),
                (["use", "work"], "Selected Codex account: work"),
                (["ls"], "* work"),
                (["who"], "Codex account: work"),
                (["rm", "terminal"], "Removed Codex account: terminal"),
                (["use", "--off"], "Restored the Codex login"),
            ):
                result = subprocess.run(command + args, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertNotIn("fake-access", result.stdout + result.stderr)
            self.assertEqual(self.accounts.load(self.accounts.auth), auth("original"))


if __name__ == "__main__":
    unittest.main()
