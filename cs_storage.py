"""Private atomic files and Codex state database discovery."""

import json
import os
from pathlib import Path
import re
import tempfile
import tomllib


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise OSError(f"Refusing to overwrite a symlink: {path.name}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def state_database(home, cwd=None):
    home = Path(home).expanduser().resolve()
    directory = None
    config = home / "config.toml"
    if config.exists():
        try:
            directory = tomllib.loads(config.read_text()).get("sqlite_home")
        except (ValueError, UnicodeError):
            raise OSError("Cannot parse Codex config.toml (contents hidden).") from None
    base = home
    if not directory:
        directory = os.environ.get("CODEX_SQLITE_HOME", "").strip()
        base = Path(cwd or os.getcwd())
    if directory:
        directory = Path(directory).expanduser()
        if not directory.is_absolute():
            directory = base / directory
    else:
        directory = home
    candidates = [(int(match.group(1)), path) for path in directory.glob("state_*.sqlite")
                  if (match := re.fullmatch(r"state_(\d+)\.sqlite", path.name)) and path.is_file()]
    return max(candidates, default=(0, None))[1]
