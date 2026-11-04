import contextlib
import io
import os
from pathlib import Path
import sys
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs_accounts as cs
from cs_session import TerminalLabel


THREAD = "12345678-1234-4234-8234-123456789abc"


class IdentityTests(unittest.TestCase):
    def test_resume_help_delegates_without_opening_picker(self):
        for args in (["resume", "--help"], ["resume", "-h"], ["-m", "chosen", "resume", "--help"]):
            with patch.object(cs, "launch", return_value=0) as launch, \
                 patch.object(cs, "pick_session", side_effect=AssertionError("custom picker must not open")):
                cs.native_cli(args)
                self.assertEqual(launch.call_args.args, ({"name": "native", "type": "native"}, args))

    def test_managed_official_resume_is_explicit(self):
        with patch.object(cs, "cmd_resume", return_value=0) as resume:
            cs.main(["native", "resume"])
        resume.assert_called_once_with([], account={"name": "native", "type": "native"})

    def test_explicit_id_keeps_official_handoff(self):
        with patch.object(cs, "launch", return_value=0) as launch:
            cs.native_cli(["resume", THREAD])
        self.assertEqual(launch.call_args.kwargs, {"thread_id": THREAD, "exec_mode": False})
        self.assertEqual(launch.call_args.args[0]["type"], "native")

    def test_gateway_compatibility_name_keeps_explicit_proxy_identity(self):
        account = {"name": "sub2", "type": "proxy", "provider_name": "OpenAI",
                   "base_url": "https://gateway.example/v1", "api_key": "synthetic"}
        command, _ = cs.build_command(account, [])
        provider = tomllib.loads(command[1])["model_providers"]["cs_sub2"]
        self.assertEqual(provider["name"], "OpenAI")
        self.assertIn("Proxy account=sub2 gateway=gateway.example", cs.account_description(account))

    def test_manual_route_override_cannot_silently_change_displayed_account(self):
        account = {"name": "native", "type": "native"}
        for args in (["-c", 'model_provider="proxy"'], ['--config=model_providers.openai.base_url="https://example.invalid"']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cs.build_command(account, args)
        command, _ = cs.build_command(account, ["resume", THREAD, "--", "model_provider=proxy"])
        self.assertEqual(command[-1], "model_provider=proxy")

    def test_launch_publishes_credential_route_and_banner(self):
        for account, label, provider in [
            ({"name": "native", "type": "native"}, "CODEX OFFICIAL", "openai"),
            ({"name": "proxy", "type": "proxy", "base_url": "https://gateway.example/v1", "api_key": "synthetic"},
             "CS PROXY / proxy", "cs_proxy"),
        ]:
            stderr = io.StringIO()
            with patch.object(cs, "find_codex", return_value="/codex"), patch.object(cs, "ensure_root"), \
                 patch.object(cs, "run_interactive", return_value=0) as run, contextlib.redirect_stderr(stderr):
                cs.launch(account, [])
            env = run.call_args.args[2]
            self.assertEqual(env["CS_ACCOUNT_TYPE"], account["type"])
            self.assertEqual(env["CS_MODEL_PROVIDER"], provider)
            self.assertEqual(env["CS_SESSION_LABEL"], label)
            self.assertIn(label, stderr.getvalue())
            self.assertNotIn("synthetic", stderr.getvalue())

    def test_who_distinguishes_current_process_from_next_cs_account(self):
        proxy = {"name": "proxy", "type": "proxy", "base_url": "https://gateway.example/v1"}
        output = io.StringIO()
        with patch.object(cs, "read_conf", return_value={"current": "proxy", "accounts": {"proxy": proxy}}), \
             patch.dict(os.environ, {"CS_ACCOUNT": "native", "CS_ACCOUNT_TYPE": "native"}), \
             contextlib.redirect_stdout(output):
            cs.cmd_who()
        self.assertIn("This process was launched as: Official OpenAI", output.getvalue())
        self.assertIn("Next cs launch: Proxy account=proxy", output.getvalue())

    def test_terminal_label_survives_split_sequences_and_keeps_other_controls(self):
        title = TerminalLabel("CS PROXY / sub2")
        data = b"text\x1b]0;project\x07\x1b]10;?\x1b\\\x1b]2;busy\x1b\\\x1b]0;\x07tail\x1b"
        result = b"".join(title.feed(bytes([byte])) for byte in data) + title.flush()
        self.assertEqual(result, b"text\x1b]0;[CS PROXY / sub2] project\x07\x1b]10;?\x1b\\"
                                b"\x1b]2;[CS PROXY / sub2] busy\x1b\\\x1b]0;\x07tail\x1b")
