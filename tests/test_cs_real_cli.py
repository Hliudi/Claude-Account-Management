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

    def test_restored_subagent_requests_follow_provider_in_both_directions(self):
        self.roundtrip(exercise_agents=True)

    def roundtrip(self, shared_daemon=False, exercise_tools=False, exercise_agents=False):
        with tempfile.TemporaryDirectory(prefix="cs-real-") as temporary:
            base = Path(temporary)
            home = base / "home"
            root = base / "switch"
            home.mkdir()
            root.mkdir()
            requests = []
            root_request_thread = []
            phase = ["SEED_NATIVE"]

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
                    request["test_thread"] = self.headers.get("thread-id")
                    if not root_request_thread:
                        root_request_thread.append(request["test_thread"])
                    is_child = request["test_thread"] != root_request_thread[0]
                    request["test_child"] = is_child
                    request["test_phase"] = phase[0]
                    requests.append(request)
                    number = len(requests)
                    expected_cipher = "synthetic-cipher-" + request["test_authorization"]
                    if any(item.get("encrypted_content") not in (None, expected_cipher)
                           for item in request.get("input", [])):
                        data = json.dumps({"error": {"code": "invalid_encrypted_content",
                                                   "message": "Synthetic provider mismatch"}}).encode()
                        self.send_response(400)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    marker = next((marker for marker in ("EXEC_NATIVE", "EXEC_PROXY", "OFFICIAL_RETURN", "PROXY_CONTINUATION", "SEED_NATIVE")
                                   if has_user_message(request, marker)), "PREWARM")
                    if is_child:
                        marker = "CHILD_REPLY"
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
                    if exercise_agents and not is_child:
                        call_id = "call-agent-" + marker
                        if marker in ("SEED_NATIVE", "PROXY_CONTINUATION", "OFFICIAL_RETURN") and not any(
                                item.get("type") == "function_call_output" and item.get("call_id") == call_id
                                for item in request.get("input", [])):
                            spawn = marker == "SEED_NATIVE"
                            arguments = ({"task_name": "probe", "fork_turns": "none", "message": "Synthetic child probe"}
                                         if spawn else {"target": "/root/probe", "message": "Synthetic child probe " + marker})
                            events[1] = {"type": "response.output_item.done", "item": {
                                "type": "function_call", "call_id": call_id, "namespace": "collaboration",
                                "name": "spawn_agent" if spawn else "followup_task", "arguments": json.dumps(arguments),
                                "encrypted_function_args": []}}
                    events.insert(1, {"type": "response.output_item.done", "item": {
                        "type": "reasoning", "id": f"rs-{number}", "summary": [],
                        "encrypted_content": expected_cipher}})
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
                      # Only the synthetic local server can request commands;
                      # avoid depending on bwrap mount privileges in containers.
                      'sandbox_mode = "danger-full-access"\n'
                      'cli_auth_credentials_store = "file"\n'
                      'web_search = "disabled"\n'
                      '[features]\nresponses_websockets = false\nresponses_websockets_v2 = false\n'
                      + ('multi_agent = true\nmulti_agent_v2 = true\n' if exercise_agents else '') +
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
            probe_thread_id = None
            if exercise_agents:
                with sqlite3.connect(next(home.glob("state_*.sqlite"))) as connection:
                    children = connection.execute("SELECT child_thread_id FROM thread_spawn_edges WHERE parent_thread_id=?",
                                                  (thread_id,)).fetchall()
                self.assertEqual(len(children), 1, "Seed must persist exactly one test child")
                probe_thread_id = children[0][0]
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
                first = launch("--native-cli", "resume", thread_id if exercise_agents else "--include-non-interactive")
                if not exercise_agents:
                    first.expect(b"SEED_NATIVE", timeout=30)
                    self.assertIn(b"Select a session to resume", first.output)
                    self.assertIn(b"Official OpenAI", first.output)
                    first.send(b"\r")
                first.expect(b"LOCAL_REPLY_SEED_NATIVE", timeout=30)
                self.assertIn(b"CODEX OFFICIAL", first.output)
                wait_writer(first)
            second = launch("resume", thread_id)
            second.expect(b"SEED_NATIVE", timeout=30)
            self.assertIn(b"CS PROXY / proxy", second.output)
            if first:
                self.assertEqual(first.process.wait(10), 0)
            else:
                self.assertIsNone(daemon.poll())
                self.assertTrue(writer_busy(home, unrelated_id))
                self.assertFalse(writer_busy(home, child_id))
                self.assertEqual(thread_states(home, thread_id), original_states)
                with sqlite3.connect(database) as connection:
                    self.assertEqual(connection.execute("SELECT model_provider FROM threads WHERE id=?", (child_id,)).fetchone()[0], "cs_proxy")
                    self.assertEqual(connection.execute("SELECT model_provider FROM threads WHERE id=?", (archived_id,)).fetchone()[0], "cs_proxy")
                    self.assertEqual(connection.execute("SELECT model_provider FROM threads WHERE id=?", (unrelated_id,)).fetchone()[0], "openai")
            phase[0] = "PROXY_CONTINUATION"
            submit(second, "PROXY_CONTINUATION")
            second.expect(b"LOCAL_REPLY_PROXY_CONTINUATION", timeout=30)
            def wait_child(marker):
                deadline = time.monotonic() + 15
                while not any(r["test_thread"] == probe_thread_id and r["test_phase"] == marker
                              and marker in json.dumps(r.get("input")) for r in requests):
                    if time.monotonic() > deadline:
                        self.fail("No real subagent request for " + marker)
                    time.sleep(0.1)
            if exercise_agents:
                wait_child("PROXY_CONTINUATION")
            submit(second, "/exit")
            self.assertEqual(second.process.wait(15), 0)
            self.assertFalse(writer_busy(home, thread_id))
            # Bare official resume must list the thread even though its latest
            # saved provider is now cs_proxy, then preserve the proxy history.
            third = launch("--native-cli", "resume", thread_id if exercise_agents else "--include-non-interactive")
            if not exercise_agents:
                third.expect(b"Select a session to resume", timeout=30)
                third.expect(b"cs_proxy", timeout=30)
                third.send(b"\r")
            third.expect(b"PROXY_CONTINUATION", timeout=30)
            self.assertIn(b"CODEX OFFICIAL", third.output)
            phase[0] = "OFFICIAL_RETURN"
            submit(third, "OFFICIAL_RETURN")
            third.expect(b"LOCAL_REPLY_OFFICIAL_RETURN", timeout=30)
            if exercise_agents:
                wait_child("OFFICIAL_RETURN")
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
            self.assertFalse(any(item.get("encrypted_content") for item in native_request["input"]))
            self.assertFalse(any(item.get("encrypted_content") for item in proxy_request["input"]))
            if exercise_agents:
                child_requests = [r for r in requests if r["test_thread"] == probe_thread_id]
                self.assertTrue(child_requests, "The CLI must actually spawn and run a child")
                for marker, expected_auth in [("PROXY_CONTINUATION", "Bearer synthetic-proxy-only"),
                                              ("OFFICIAL_RETURN", "Bearer synthetic-local-only")]:
                    matches = [r for r in child_requests if r["test_phase"] == marker and marker in json.dumps(r.get("input"))]
                    self.assertTrue(matches, "Missing child continuation: " + marker)
                    self.assertTrue(all(r["test_authorization"] == expected_auth for r in matches))
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
