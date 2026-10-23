import contextlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs_daemon as daemon
from cs_session import HandoffError, ownership_dir


THREAD = "12345678-1234-4234-8234-123456789abc"
CHILD = "12345678-1234-4234-8234-123456789abd"
ARCHIVED = "12345678-1234-4234-8234-123456789abe"
UNRELATED = "12345678-1234-4234-8234-123456789abf"


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cs-daemon-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.directory = ownership_dir(self.home / "switch", self.home)
        self.database = self.home / "state_5.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.executescript("CREATE TABLE threads (id TEXT PRIMARY KEY, archived INTEGER);"
                                     "CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT);")
            connection.executemany("INSERT INTO threads VALUES (?,?)",
                                   [(THREAD, 0), (CHILD, 0), (ARCHIVED, 1), (UNRELATED, 0)])
            connection.executemany("INSERT INTO thread_spawn_edges VALUES (?,?)", [(THREAD, CHILD), (THREAD, ARCHIVED)])

    def client(self, fail_restore=False, fail_archive=False, paginate=False, spawn=False):
        owner = self

        class FakeClient:
            def __init__(self, *args):
                self.home = owner.home
                self.calls = []

            def __enter__(self):
                owner.instance = self
                return self

            def __exit__(self, *args):
                pass

            def call(self, method, params):
                self.calls.append((method, params))
                if method == "thread/loaded/list":
                    if paginate and not params.get("cursor"):
                        return {"data": [UNRELATED], "nextCursor": "page-two"}
                    return {"data": [THREAD, UNRELATED]}
                with sqlite3.connect(owner.database) as connection:
                    if method == "thread/archive":
                        if fail_archive:
                            raise HandoffError("archive failed")
                        connection.execute("UPDATE threads SET archived=1 WHERE id<>?", (UNRELATED,))
                        if spawn:
                            spawned = "12345678-1234-4234-8234-123456789ac0"
                            connection.execute("INSERT INTO threads VALUES (?,1)", (spawned,))
                            connection.execute("INSERT INTO thread_spawn_edges VALUES (?,?)", (THREAD, spawned))
                    elif method == "thread/unarchive":
                        if fail_restore:
                            raise HandoffError("restore failed")
                        connection.execute("UPDATE threads SET archived=0 WHERE id=?", (params["threadId"],))
                return {}

        return FakeClient

    def release(self, **kwargs):
        with patch.object(daemon, "daemon_pid", return_value=123), \
             patch.object(daemon, "owns_writer", return_value=True), \
             patch.object(daemon, "writer_busy", return_value=False), \
             patch.object(daemon, "DaemonClient", self.client(**kwargs)), contextlib.redirect_stdout(io.StringIO()):
            return daemon.release_daemon_writer("codex", self.home, self.directory, THREAD)

    def test_releases_only_target_subtree_and_restores_archive_flags(self):
        before = daemon.thread_states(self.home, THREAD)
        self.assertTrue(self.release())
        self.assertEqual(daemon.thread_states(self.home, THREAD), before)
        restored = [params["threadId"] for method, params in self.instance.calls if method == "thread/unarchive"]
        self.assertEqual(set(restored), {THREAD, CHILD})
        self.assertFalse(list(self.directory.glob("*.recovery.json")))
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT archived FROM threads WHERE id=?", (UNRELATED,)).fetchone(), (0,))

    def test_failed_restoration_keeps_journal_and_retry_recovers(self):
        original = daemon.thread_states(self.home, THREAD)
        with self.assertRaisesRegex(HandoffError, "recovery"):
            self.release(fail_restore=True)
        journal = self.directory / f"{THREAD}.recovery.json"
        self.assertTrue(journal.exists())
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        with patch.object(daemon, "DaemonClient", self.client()):
            daemon.recover_handoff("codex", self.home, self.directory, THREAD)
        self.assertEqual(daemon.thread_states(self.home, THREAD), original)
        self.assertFalse(journal.exists())

    def test_archive_failure_does_not_change_original_states(self):
        original = daemon.thread_states(self.home, THREAD)
        with self.assertRaisesRegex(HandoffError, "archive failed"):
            self.release(fail_archive=True)
        self.assertEqual(daemon.thread_states(self.home, THREAD), original)
        self.assertTrue((self.directory / f"{THREAD}.recovery.json").exists())

    def test_non_daemon_owner_is_never_archived(self):
        with patch.object(daemon, "daemon_pid", return_value=None), patch.object(daemon, "DaemonClient") as client:
            self.assertFalse(daemon.release_daemon_writer("codex", self.home, self.directory, THREAD))
            client.assert_not_called()

    def test_loaded_thread_on_later_page_can_be_released(self):
        self.assertTrue(self.release(paginate=True))
        pages = [params for method, params in self.instance.calls if method == "thread/loaded/list"]
        self.assertEqual(pages, [{}, {"cursor": "page-two"}])

    def test_child_created_during_handoff_is_restored(self):
        self.assertTrue(self.release(spawn=True))
        self.assertEqual(daemon.thread_states(self.home, THREAD)["12345678-1234-4234-8234-123456789ac0"], 0)

    def test_malformed_recovery_record_is_rejected_before_contacting_daemon(self):
        path = self.directory / f"{THREAD}.recovery.json"
        path.write_text(json.dumps({"thread_id": THREAD, "states": []}))
        with patch.object(daemon, "DaemonClient") as client, self.assertRaisesRegex(HandoffError, "Invalid handoff"):
            daemon.recover_handoff("codex", self.home, self.directory, THREAD)
        client.assert_not_called()

    def test_websocket_handles_fragmentation_ping_and_masked_client_frames(self):
        client = daemon.DaemonClient("unused", self.home)
        local, peer = socket.socketpair()
        self.addCleanup(local.close)
        self.addCleanup(peer.close)
        client.socket = local
        payload = json.dumps({"id": 1, "result": {"value": "hello"}}).encode()
        peer.sendall(bytes([0x01, 5]) + payload[:5] + b"\x89\x02hi" + bytes([0x80, len(payload) - 5]) + payload[5:])
        self.assertEqual(client.receive(time.monotonic() + 1), {"id": 1, "result": {"value": "hello"}})
        pong = peer.recv(32)
        self.assertEqual(pong[:2], b"\x8a\x82")
        self.assertEqual(bytes(value ^ pong[2 + index % 4] for index, value in enumerate(pong[6:])), b"hi")

    def test_websocket_rejects_oversized_frame_before_reading_payload(self):
        client = daemon.DaemonClient("unused", self.home)
        local, peer = socket.socketpair()
        self.addCleanup(local.close)
        self.addCleanup(peer.close)
        client.socket = local
        peer.sendall(b"\x81\x7f" + struct.pack("!Q", 33 * 1024 * 1024))
        with self.assertRaisesRegex(HandoffError, "size limit"):
            client.receive(time.monotonic() + 1)
