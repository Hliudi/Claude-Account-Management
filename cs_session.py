"""Cooperative Linux terminal ownership for a shared Codex history."""

import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import socket
import sys
import termios
import time
import tty
import uuid

from cs_storage import atomic_json


class HandoffError(RuntimeError):
    pass


class TerminalLabel:
    def __init__(self, label):
        self.label = label.encode()
        self.pending = b""

    def feed(self, data):
        if not self.label:
            return data
        self.pending += data
        output = bytearray()
        while self.pending:
            start = self.pending.find(b"\x1b]")
            if start < 0:
                keep = 1 if self.pending.endswith(b"\x1b") else 0
                output.extend(self.pending[:-keep] if keep else self.pending)
                self.pending = self.pending[-keep:] if keep else b""
                break
            output.extend(self.pending[:start])
            self.pending = self.pending[start:]
            if len(self.pending) < 4:
                break
            if self.pending[:4] not in (b"\x1b]0;", b"\x1b]2;"):
                output.extend(self.pending[:2])
                self.pending = self.pending[2:]
                continue
            endings = [(position, terminator) for terminator in (b"\x07", b"\x1b\\")
                       if (position := self.pending.find(terminator, 4)) >= 0]
            if not endings:
                if len(self.pending) > 8192:
                    output.extend(self.pending)
                    self.pending = b""
                break
            end, terminator = min(endings)
            title = self.pending[4:end]
            output.extend(self.pending[:4])
            if title:
                output.extend(b"[" + self.label + b"] " + title)
            output.extend(terminator)
            self.pending = self.pending[end + len(terminator):]
        return bytes(output)

    def flush(self):
        pending, self.pending = self.pending, b""
        return pending


def receive_json(connection):
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(4096 - len(data))
        if not chunk:
            if data:
                break
            raise HandoffError("Handoff connection closed before its response was complete.")
        data.extend(chunk)
        if len(data) >= 4096 and b"\n" not in data:
            raise HandoffError("Handoff message exceeded the size limit.")
    value = json.loads(data.split(b"\n", 1)[0])
    if not isinstance(value, dict):
        raise HandoffError("Invalid handoff message.")
    return value


def reply_and_close(connection, value):
    try:
        with contextlib.suppress(OSError):
            connection.sendall(json.dumps(value).encode() + b"\n")
    finally:
        connection.close()


def write_all(descriptor, data):
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if not written:
            raise OSError("Terminal write made no progress.")
        remaining = remaining[written:]


def stop_child(child_pid, timeout=3, initial_signal=signal.SIGHUP):
    if initial_signal is not None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child_pid, initial_signal)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(child_pid, os.WNOHANG)
        if waited:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child_pid, signal.SIGKILL)
    return os.waitstatus_to_exitcode(os.waitpid(child_pid, 0)[1])


def writer_busy(home, thread_id):
    path = Path(home) / "thread-writer-locks" / f"{thread_id}.lock"
    try:
        with path.open("rb") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def owns_writer(pid, home, thread_id):
    expected = Path(home).resolve() / "thread-writer-locks" / f"{thread_id}.lock"
    try:
        for entry in Path(f"/proc/{pid}/fd").iterdir():
            try:
                if entry.resolve() != expected:
                    continue
                info = Path(f"/proc/{pid}/fdinfo/{entry.name}").read_text()
                if any(line.startswith("lock:") and "FLOCK" in line and "WRITE" in line
                       for line in info.splitlines()):
                    return True
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except (FileNotFoundError, PermissionError):
        pass
    return False


def ownership_dir(root, home):
    digest = hashlib.sha256(os.fsencode(Path(home).resolve())).hexdigest()[:16]
    directory = Path(root) / "ownership" / digest
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


@contextlib.contextmanager
def startup_gate(directory, thread_id, timeout=30):
    if thread_id is None:
        yield None
        return
    uuid.UUID(thread_id)
    path = directory / f"{thread_id}.gate"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise HandoffError("Another launch is still opening this session. Retry shortly.")
                time.sleep(0.1)
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def request_handoff(directory, home, thread_id, timeout=20, binary=None):
    if binary and thread_id:
        from cs_daemon import recover_handoff
        recover_handoff(binary, home, directory, thread_id)
    if not thread_id or not writer_busy(home, thread_id):
        return
    for record in directory.glob("*.json"):
        try:
            metadata = json.loads(record.read_text())
            if not owns_writer(int(metadata["child_pid"]), home, thread_id):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        try:
            print(f"cs: requesting release of {thread_id} from the other terminal…", flush=True)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(timeout)
                client.connect(str(record.with_suffix(".sock")))
                client.sendall(json.dumps({"thread_id": thread_id}).encode() + b"\n")
                result = receive_json(client)
            if not result.get("released"):
                raise HandoffError(result.get("error", "The other terminal did not release the session."))
            if writer_busy(home, thread_id):
                raise HandoffError("The original writer has not released its lock yet. Retry shortly.")
            return
        except (OSError, ValueError) as error:
            raise HandoffError("Cannot contact the managed writer; use /exit in its terminal and retry.") from error
    if binary:
        from cs_daemon import release_daemon_writer
        if release_daemon_writer(binary, home, directory, thread_id):
            return
    if writer_busy(home, thread_id):
        raise HandoffError(
            f"Session {thread_id} belongs to an unmanaged Codex/App process. "
            "Use /exit in its original terminal, or close that chat in the App, then retry. "
            "New sessions launched through the installed cs/codex wrappers support automatic handoff. "
            "No lock was removed and no unrelated process was stopped."
        )


def run_interactive(binary, args, env, home, root, thread_id=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise HandoffError("Interactive sessions need a terminal; use --exec for non-interactive work.")
    directory = ownership_dir(root, home)
    with startup_gate(directory, thread_id) as gate:
        request_handoff(directory, home, thread_id, binary=binary)
        return _terminal(binary, args, env, home, directory, thread_id, gate)


def _terminal(binary, args, env, home, directory, thread_id, gate):
    endpoint = directory / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"
    if len(os.fsencode(endpoint)) >= 108:
        raise HandoffError("CS_DIR is too long for a local handoff socket; choose a shorter path.")
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(endpoint))
    endpoint.chmod(0o600)
    listener.listen(4)
    terminal_fd = sys.stdin.fileno()
    original = termios.tcgetattr(terminal_fd)
    old_signals = {}
    child_pid = None
    master = None
    pending = None
    requested_thread = None
    handed_off = False
    exit_status = None
    drain_deadline = None
    quit_started = None
    last_interrupt = 0
    stop_requested = False
    record = endpoint.with_suffix(".json")
    title_label = TerminalLabel(env.get("CS_SESSION_LABEL", ""))

    def resize(signum=None, frame=None):
        if master is not None:
            size = fcntl.ioctl(terminal_fd, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(master, termios.TIOCSWINSZ, size)

    def stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    try:
        child_pid, master = pty.fork()
        if child_pid == 0:
            try:
                listener.close()
                if gate is not None:
                    gate.close()
                os.execvpe(binary, [binary, *args], env)
            except BaseException as error:
                print(f"cs: cannot start Codex: {error}", file=sys.stderr)
                os._exit(127)
        atomic_json(record, {"child_pid": child_pid})
        resize()
        if title_label.label:
            write_all(sys.stdout.fileno(), b"\x1b]0;[" + title_label.label + b"]\x07")
        tty.setraw(terminal_fd)
        for signum, handler in [(signal.SIGWINCH, resize), (signal.SIGTERM, stop),
                                (signal.SIGHUP, stop), (signal.SIGINT, stop)]:
            old_signals[signum] = signal.signal(signum, handler)
        while True:
            if exit_status is None:
                waited, status = os.waitpid(child_pid, os.WNOHANG)
                if waited:
                    exit_status = os.waitstatus_to_exitcode(status)
                    drain_deadline = time.monotonic() + 1
            if drain_deadline is not None and time.monotonic() > drain_deadline:
                break
            if gate is not None and owns_writer(child_pid, home, thread_id):
                fcntl.flock(gate, fcntl.LOCK_UN)
                gate = None
            now = time.monotonic()
            if stop_requested and quit_started is None:
                quit_started = now
            if quit_started is not None and exit_status is None:
                if now - quit_started > 12:
                    if pending is not None:
                        reply_and_close(pending, {"released": False, "error":
                            "The original terminal did not exit. Finish its dialog or use /exit, then retry."
                        })
                        pending = None
                    quit_started = None
                    if stop_requested:
                        break
                elif now - last_interrupt > 0.4:
                    write_all(master, b"\x03")
                    last_interrupt = now
            sources = [master] if exit_status is not None else [terminal_fd, master, listener]
            ready, _, _ = select.select(sources, [], [], 0.1)
            for source in ready:
                if source == listener:
                    connection, _ = listener.accept()
                    connection.settimeout(1)
                    try:
                        request = receive_json(connection)
                        target = str(uuid.UUID(request["thread_id"]))
                        if pending is not None or not owns_writer(child_pid, home, target):
                            reply_and_close(connection, {"released": False, "error": "Session ownership changed; retry."})
                            continue
                        pending = connection
                        requested_thread = target
                        quit_started = time.monotonic()
                    except (OSError, ValueError, KeyError, TypeError, HandoffError):
                        connection.close()
                elif source == terminal_fd:
                    data = os.read(terminal_fd, 65536)
                    if not data:
                        stop_requested = True
                    elif quit_started is None:
                        write_all(master, data)
                else:
                    try:
                        data = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        data = b""
                    if data:
                        write_all(sys.stdout.fileno(), title_label.feed(data))
                    else:
                        if exit_status is None:
                            exit_status = stop_child(child_pid, initial_signal=None)
                        drain_deadline = 0
                        break
            if drain_deadline == 0:
                break
    finally:
        with contextlib.suppress(OSError):
            write_all(sys.stdout.fileno(), title_label.flush())
        with contextlib.suppress(termios.error):
            termios.tcsetattr(terminal_fd, termios.TCSADRAIN, original)
        for signum, handler in old_signals.items():
            signal.signal(signum, handler)
        if master is not None:
            os.close(master)
        if child_pid and exit_status is None:
            exit_status = stop_child(child_pid)
        if pending is not None:
            handed_off = exit_status == 0 and not writer_busy(home, requested_thread)
            reply_and_close(pending, {"released": handed_off})
        listener.close()
        record.unlink(missing_ok=True)
        endpoint.unlink(missing_ok=True)
    if handed_off:
        print(f"\ncs: released {requested_thread}; continued in the other terminal.")
    return 128 - exit_status if exit_status is not None and exit_status < 0 else (exit_status or 0)
