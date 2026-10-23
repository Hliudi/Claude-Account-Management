"""Release a shared daemon writer through its public lifecycle API."""

import contextlib
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import time
import uuid

from cs_session import HandoffError, owns_writer, writer_busy
from cs_storage import atomic_json, state_database


class DaemonClient:
    def __init__(self, binary, home, timeout=30):
        self.binary = binary
        self.home = Path(home)
        self.timeout = timeout
        self.socket = None
        self.buffer = b""
        self.sequence = 0

    def __enter__(self):
        endpoint = self.home / "app-server-control" / "app-server-control.sock"
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(self.timeout)
        try:
            self.socket.connect(str(endpoint))
            self.peer_pid, peer_uid, _ = struct.unpack("3i", self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer_uid != os.getuid():
                raise HandoffError("The daemon control socket belongs to another user.")
            key = base64.b64encode(os.urandom(16)).decode()
            request = ("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                       "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                       f"Sec-WebSocket-Key: {key}\r\n\r\n")
            self.socket.sendall(request.encode())
            while b"\r\n\r\n" not in self.buffer:
                chunk = self.socket.recv(4096)
                if not chunk or len(self.buffer) > 16384:
                    raise HandoffError("Invalid daemon WebSocket handshake.")
                self.buffer += chunk
            headers, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
            lines = headers.decode("ascii").split("\r\n")
            fields = {name.lower(): value.strip() for name, value in
                      (line.split(":", 1) for line in lines[1:] if ":" in line)}
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if len(lines[0].split()) < 2 or lines[0].split()[1] != "101" or fields.get("sec-websocket-accept") != expected:
                raise HandoffError("Daemon rejected the local WebSocket handshake.")
            self.call("initialize", {"clientInfo": {
                "name": "cs_session_handoff", "version": "1.0.0", "title": "CS Session Handoff"}})
            self.send({"jsonrpc": "2.0", "method": "initialized"})
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def send(self, message):
        self.send_frame(1, json.dumps(message).encode())

    def send_frame(self, opcode, payload):
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size <= 65535:
            header += bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", size)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(header + mask + masked)

    def read_bytes(self, size, deadline):
        while len(self.buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HandoffError("Daemon response timed out; retry to recover the handoff.")
            self.socket.settimeout(remaining)
            chunk = self.socket.recv(min(65536, size - len(self.buffer)))
            if not chunk:
                raise HandoffError("Daemon disconnected; retry to recover the handoff.")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def receive(self, deadline):
        fragments = bytearray()
        started = False
        while True:
            first, second = self.read_bytes(2, deadline)
            opcode, size = first & 0x0F, second & 0x7F
            if first & 0x70 or second & 0x80:
                raise HandoffError("Unsupported daemon WebSocket frame.")
            if size == 126:
                size = struct.unpack("!H", self.read_bytes(2, deadline))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read_bytes(8, deadline))[0]
            if size + len(fragments) > 32 * 1024 * 1024:
                raise HandoffError("Daemon response exceeded the handoff size limit.")
            if opcode >= 8 and (size > 125 or not first & 0x80):
                raise HandoffError("Invalid daemon WebSocket control frame.")
            payload = self.read_bytes(size, deadline)
            if opcode == 8:
                raise HandoffError("Daemon closed the handoff connection.")
            if opcode == 9:
                self.send_frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise HandoffError("Unsupported daemon message type.")
            if (opcode == 0 and not started) or (opcode == 1 and started):
                raise HandoffError("Invalid daemon WebSocket continuation.")
            started = True
            fragments.extend(payload)
            if first & 0x80:
                try:
                    message = json.loads(fragments)
                except (ValueError, UnicodeError):
                    raise HandoffError("Invalid daemon JSON response (contents hidden).") from None
                if not isinstance(message, dict):
                    raise HandoffError("Invalid daemon response shape.")
                return message

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.socket.settimeout(self.timeout)
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self.receive(deadline)
            if "method" in message:
                if "id" in message:
                    self.send({"jsonrpc": "2.0", "id": message["id"], "error": {
                        "code": -32601, "message": "Handoff client does not execute server requests."}})
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise HandoffError(f"Daemon {method}: {message['error'].get('message', 'request failed')}")
            if "result" not in message:
                raise HandoffError("Daemon response is missing its result.")
            return message["result"]

    def __exit__(self, *args):
        if self.socket is None:
            return
        with contextlib.suppress(OSError):
            self.send_frame(8, struct.pack("!H", 1000))
        self.socket.close()


def daemon_pid(home):
    endpoint = Path(home) / "app-server-control" / "app-server-control.sock"
    try:
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(2)
            probe.connect(str(endpoint))
            peer_pid, peer_uid, _ = struct.unpack("3i", probe.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer_uid != os.getuid():
            return None
        if Path(f"/proc/{peer_pid}/exe").resolve().name != "codex":
            return None
        return peer_pid
    except OSError:
        return None


def thread_states(home, thread_id):
    database = state_database(home)
    if database is None:
        raise HandoffError("Cannot snapshot the daemon session before releasing it: no state database.")
    with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "thread_spawn_edges" in tables:
            rows = connection.execute(
                "WITH RECURSIVE subtree(id) AS (SELECT ? UNION SELECT child_thread_id "
                "FROM thread_spawn_edges JOIN subtree ON parent_thread_id=subtree.id) "
                "SELECT threads.id, archived FROM threads JOIN subtree ON threads.id=subtree.id",
                (thread_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT id, archived FROM threads WHERE id=?", (thread_id,)).fetchall()
    if not rows or thread_id not in dict(rows):
        raise HandoffError("The daemon session is missing from its state database; nothing was changed.")
    return dict(rows)


def save_journal(path, thread_id, states):
    atomic_json(path, {"thread_id": thread_id, "states": states})


def restore_threads(client, journal, states, path):
    current = thread_states(client.home, journal["thread_id"])
    added = current.keys() - states.keys()
    if added:
        states = {**states, **dict.fromkeys(added, 0)}
        save_journal(path, journal["thread_id"], states)
    failures = []
    for thread_id, was_archived in states.items():
        if not was_archived and current.get(thread_id):
            try:
                client.call("thread/unarchive", {"threadId": thread_id})
            except (HandoffError, OSError) as error:
                failures.append(str(error))
    if failures:
        raise HandoffError("Session release needs recovery. Retry the same cs command. " + "; ".join(failures))
    restored = thread_states(client.home, journal["thread_id"])
    if any(restored.get(thread_id) != was_archived for thread_id, was_archived in states.items()):
        raise HandoffError("Session archive state was not restored. Retry the same cs command to recover.")


def recover_handoff(binary, home, directory, thread_id):
    uuid.UUID(thread_id)
    path = directory / f"{thread_id}.recovery.json"
    if not path.exists():
        return
    if path.is_symlink():
        raise HandoffError("Refusing a handoff recovery symlink.")
    try:
        journal = json.loads(path.read_text())
        if journal["thread_id"] != thread_id or thread_id not in journal["states"]:
            raise ValueError
        for saved_id, archived in journal["states"].items():
            uuid.UUID(saved_id)
            if archived not in (0, 1):
                raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HandoffError("Invalid handoff recovery record; no sessions were changed.") from None
    with DaemonClient(binary, home) as client:
        restore_threads(client, journal, journal["states"], path)
    path.unlink()


def release_daemon_writer(binary, home, directory, thread_id):
    pid = daemon_pid(home)
    if pid is None or not owns_writer(pid, home, thread_id):
        return False
    states = thread_states(home, thread_id)
    if states[thread_id]:
        raise HandoffError("This daemon session is archived; restore it before requesting handoff.")
    path = directory / f"{thread_id}.recovery.json"
    if path.is_symlink():
        raise HandoffError("Refusing a handoff recovery symlink.")
    print(f"cs: releasing {thread_id} from the shared daemon; preserving its history…", flush=True)
    with DaemonClient(binary, home) as client:
        if getattr(client, "peer_pid", pid) != pid:
            raise HandoffError("The daemon restarted during handoff; retry.")
        loaded, cursor, seen = [], None, set()
        while True:
            page = client.call("thread/loaded/list", {"cursor": cursor} if cursor else {})
            loaded.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if thread_id in loaded or not cursor:
                break
            if cursor in seen:
                raise HandoffError("Daemon returned a repeated session-list cursor.")
            seen.add(cursor)
        if thread_id not in loaded or not owns_writer(pid, home, thread_id):
            return not writer_busy(home, thread_id)
        save_journal(path, thread_id, states)
        try:
            client.call("thread/archive", {"threadId": thread_id})
        finally:
            restore_threads(client, {"thread_id": thread_id}, states, path)
        path.unlink()
    if writer_busy(home, thread_id):
        raise HandoffError("Another client reacquired the session during handoff; retry cs resume.")
    return True
