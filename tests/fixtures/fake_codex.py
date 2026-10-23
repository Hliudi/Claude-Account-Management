#!/usr/bin/env python3
import fcntl
import json
import os
from pathlib import Path
import sys
import termios
import tty

home = Path(os.environ["CODEX_HOME"])
args = sys.argv[1:]
thread_id = args[args.index("resume") + 1] if "resume" in args else os.environ["TEST_THREAD_ID"]
provider = next(value.split("=", 1)[1].strip('"') for value in args if value.startswith("model_provider="))
directory = home / "thread-writer-locks"
directory.mkdir(exist_ok=True)
lock = (directory / f"{thread_id}.lock").open("a+")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
original = termios.tcgetattr(0)
tty.setraw(0)
history = home / "transcript.jsonl"
previous = history.read_text() if history.exists() else ""
print(f"READY {provider} previous={len(previous.splitlines())}", flush=True)
buffer = b""
try:
    while True:
        data = os.read(0, 1)
        if data == b"\x03":
            break
        if data == b"\r":
            if buffer == b"/exit":
                break
            with history.open("a") as stream:
                stream.write(json.dumps({"thread_id": thread_id, "provider": provider,
                                         "message": buffer.decode()}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            print("SAVED", flush=True)
            buffer = b""
        else:
            buffer += data
finally:
    termios.tcsetattr(0, termios.TCSADRAIN, original)
    lock.close()
