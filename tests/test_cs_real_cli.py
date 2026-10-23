"""Opt-in real Codex PTY tests; every model request goes to a local fake server."""

import http.server
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_cs_accounts import REPO, Terminal
from cs_session import writer_busy
from cs_daemon import DaemonClient, thread_states


@unittest.skipUnless(os.environ.get("CS_TEST_CODEX_BIN"), "Set CS_TEST_CODEX_BIN to a real CLI")
class RealCliTests(unittest.TestCase):
    def test_official_proxy_official_same_thread_and_history(self):
        self.roundtrip()

    def test_shared_daemon_proxy_official_preserves_other_writers_and_child_states(self):
        self.roundtrip(shared_daemon=True)

    def test_third_party_tool_loop_and_exec_resume(self):
        self.roundtrip(exercise_tools=True)

    def roundtrip(self, shared_daemon=False, exercise_tools=False):
        with tempfile.TemporaryDirectory(prefix="cs-real-") as temporary:
            base = Path(temporary)
            home = base / "home"
            root = base / "switch"
            home.mkdir()
            root.mkdir()
            requests = []

            def has_user_message(request, text):
                return any(item.get("role") == "user" and any(content.get("text") == text
                           for content in item.get("content", []) if isinstance(content, dict))
                           for item in request.get("input", []) if isinstance(item, dict))

            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def do_POST(self):
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    request = json.loads(body)
                    request["test_authorization"] = self.headers.get("Authorization")
                    request["test_path"] = self.path
                    requests.append(request)
                    number = len(requests)
                    marker = next((marker for marker in ("EXEC_NATIVE", "EXEC_PROXY", "OFFICIAL_RETURN", "PROXY_CONTINUATION", "SEED_NATIVE")
                                   if has_user_message(request, marker)), "PREWARM")
                    events = [
                        {"type": "response.created", "response": {"id": f"resp-{number}"}},
                        {"type": "response.output_item.done", "item": {"id": f"msg-{number}",
                            "type": "message", "role": "assistant", "content": [
                                {"type": "output_text", "text": f"LOCAL_REPLY_{marker}"}]}},
                        {"type": "response.completed", "response": {"id": f"resp-{number}",
                            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}},
                    ]
                    if exercise_tools and marker == "PROXY_CONTINUATION" and not any(
                            item.get("type") == "function_call_output" and item.get("call_id") == "call-proxy-tool"
                            for item in request.get("input", [])):
                        events[1] = {"type": "response.output_item.done", "item": {
                            "type": "function_call", "call_id": "call-proxy-tool", "name": "exec_command",
                            "arguments": json.dumps({"cmd": "printf TOOL_LOOP_OK", "shell": "/bin/sh", "login": False,
                                                     "yield_time_ms": 1000, "max_output_tokens": 200})}}
                    data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = f"http://127.0.0.1:{server.server_port}/v1"
            config = ('model = "gpt-5.2-codex"\n'
                      f'openai_base_url = "{url}"\n'
                      'approval_policy = "never"\n'
                      'cli_auth_credentials_store = "file"\n'
                      'web_search = "disabled"\n'
                      '[features]\nresponses_websockets = false\nresponses_websockets_v2 = false\n'
                      '[tui]\nscreen_reader_detection_done = true\n'
                      f'[projects.{json.dumps(str(base))}]\ntrust_level = "trusted"\n')
            (home / "config.toml").write_text(config)
            auth = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "synthetic-local-only"})
            (home / "auth.json").write_text(auth)
            (root / "accounts.json").write_text(json.dumps({"current": "proxy", "accounts": {
                "proxy": {"name": "proxy", "type": "proxy", "base_url": url,
                          "api_key": "synthetic-proxy-only", "model": "gpt-5.2-codex",
                          "requires_openai_auth": not exercise_tools, "supports_websockets": False}}}))
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CODEX_", "CS_", "OPENAI_"))}
            env.update(CODEX_HOME=str(home), CS_DIR=str(root), TERM="xterm-256color",
                       CODEX_CLI_PATH=os.environ["CS_TEST_CODEX_BIN"])
            binary = os.environ["CS_TEST_CODEX_BIN"]
            seed = subprocess.run([binary, "exec", "--skip-git-repo-check", "--json", "SEED_NATIVE"],
                                  env=env, cwd=base, capture_output=True, text=True, timeout=40)
            self.assertEqual(seed.returncode, 0, seed.stderr[-5000:])
            thread_id = next(json.loads(line)["thread_id"] for line in seed.stdout.splitlines()
                             if json.loads(line).get("type") == "thread.started")
            daemon = None
            client = None
            if shared_daemon:
                daemon = subprocess.Popen([binary, "app-server", "--listen", "unix://"],
                                          env=env, cwd=base, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                def stop_daemon():
                    daemon.terminate()
                    try:
                        daemon.wait(5)
                    except subprocess.TimeoutExpired:
                        daemon.kill()
                        daemon.wait()

                self.addCleanup(stop_daemon)
                endpoint = home / "app-server-control/app-server-control.sock"
                deadline = time.monotonic() + 10
                while not endpoint.exists():
                    self.assertIsNone(daemon.poll(), "Test app-server failed to start")
                    if time.monotonic() > deadline:
                        self.fail("Test daemon control socket never appeared")
                    time.sleep(0.1)
                client = DaemonClient(binary, home).__enter__()
                self.addCleanup(client.__exit__, None, None, None)
                client.call("thread/resume", {"threadId": thread_id})
                unrelated_id = client.call("thread/fork", {"threadId": thread_id})["thread"]["id"]
                child_id = client.call("thread/fork", {"threadId": thread_id})["thread"]["id"]
                archived_id = client.call("thread/fork", {"threadId": thread_id})["thread"]["id"]
                client.call("thread/archive", {"threadId": archived_id})
                database = next(home.glob("state_*.sqlite"))
                with sqlite3.connect(database) as connection:
                    connection.executemany("INSERT INTO thread_spawn_edges VALUES (?,?,'completed')",
                                           [(thread_id, child_id), (thread_id, archived_id)])
                original_states = thread_states(home, thread_id)

            def launch(*args):
                command = ([str(REPO / "codex-cs"), *args[1:]] if args[0] == "--native-cli"
                           else [str(REPO / "cs"), *args])
                terminal = Terminal(command, env, base)
                self.addCleanup(terminal.close)
                return terminal

            def wait_writer(terminal):
                deadline = time.monotonic() + 20
                while not writer_busy(home, thread_id):
                    if time.monotonic() > deadline:
                        self.fail(f"Writer never acquired: {terminal.output[-5000:]!r}")
                    terminal.expect(b"", timeout=1)
                    time.sleep(0.1)

            def submit(terminal, text):
                terminal.send(text.encode())
                time.sleep(0.3)
                terminal.send(b"\r")

            first = None
            if not shared_daemon:
                first = launch("--native-cli", "resume", thread_id)
                first.expect(b"SEED_NATIVE", timeout=30)
                wait_writer(first)
            second = launch("resume", thread_id)
            second.expect(b"SEED_NATIVE", timeout=30)
            if first:
                self.assertEqual(first.process.wait(10), 0)
            else:
                self.assertIsNone(daemon.poll())
                self.assertTrue(writer_busy(home, unrelated_id))
                self.assertFalse(writer_busy(home, child_id))
                self.assertEqual(thread_states(home, thread_id), original_states)
            submit(second, "PROXY_CONTINUATION")
            second.expect(b"LOCAL_REPLY_PROXY_CONTINUATION", timeout=30)
            submit(second, "/exit")
            self.assertEqual(second.process.wait(15), 0)
            self.assertFalse(writer_busy(home, thread_id))
            third = launch("--native-cli", "resume", thread_id)
            third.expect(b"PROXY_CONTINUATION", timeout=30)
            submit(third, "OFFICIAL_RETURN")
            third.expect(b"LOCAL_REPLY_OFFICIAL_RETURN", timeout=30)
            submit(third, "/exit")
            self.assertEqual(third.process.wait(15), 0)
            self.assertFalse(writer_busy(home, thread_id))
            proxy_request = next(request for request in requests
                                 if has_user_message(request, "PROXY_CONTINUATION")
                                 and request["test_authorization"] == "Bearer synthetic-proxy-only")
            native_request = next(request for request in requests
                                  if has_user_message(request, "OFFICIAL_RETURN"))
            self.assertEqual(native_request["test_authorization"], "Bearer synthetic-local-only")
            self.assertIn("SEED_NATIVE", json.dumps(proxy_request["input"]))
            self.assertIn("PROXY_CONTINUATION", json.dumps(native_request["input"]))
            self.assertIn("LOCAL_REPLY_PROXY_CONTINUATION", json.dumps(native_request["input"]))
            if exercise_tools:
                outputs = [item for request in requests for item in request.get("input", [])
                           if item.get("type") == "function_call_output" and item.get("call_id") == "call-proxy-tool"]
                self.assertTrue(any("TOOL_LOOP_OK" in json.dumps(item.get("output")) for item in outputs), repr(outputs))
                self.assertIn("TOOL_LOOP_OK", json.dumps(native_request["input"]))
                for command, marker, expected_auth in [
                    ([str(REPO / "cs"), "run", "proxy", "exec"], "EXEC_PROXY", "Bearer synthetic-proxy-only"),
                    ([str(REPO / "codex-cs"), "exec"], "EXEC_NATIVE", "Bearer synthetic-local-only"),
                ]:
                    result = subprocess.run([*command, "--skip-git-repo-check", "--json", "resume", thread_id, marker],
                                            cwd=base, env=env, capture_output=True, text=True, timeout=40)
                    self.assertEqual(result.returncode, 0, result.stderr[-3000:])
                    resumed = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
                    self.assertTrue(any(event.get("thread_id") == thread_id for event in resumed))
                    request = next(request for request in requests if has_user_message(request, marker))
                    self.assertEqual(request["test_authorization"], expected_auth)
                    self.assertIn("TOOL_LOOP_OK", json.dumps(request["input"]))
                    self.assertFalse(writer_busy(home, thread_id))
            database = next(home.glob("state_*.sqlite"))
            with sqlite3.connect(database) as connection:
                rows = connection.execute("SELECT id,model_provider FROM threads WHERE id=?", (thread_id,)).fetchall()
            self.assertEqual(rows, [(thread_id, "openai")])
            self.assertEqual((home / "auth.json").read_text(), auth)
            self.assertEqual((home / "config.toml").read_text(), config)
