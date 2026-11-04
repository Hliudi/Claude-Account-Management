"""Quiescent, backed-up provider migration for a conversation and its descendants.

Rollout line lengths and ordinals are preserved, including whitespace padding,
so Codex's paginated-history byte offsets remain valid. No model calls are made.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import uuid

from cs_session import HandoffError, request_handoff, startup_gate
from cs_storage import atomic_json, state_database


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_copy(source, target):
    fd, name = tempfile.mkstemp(prefix=".cs-switch-", dir=Path(target).parent)
    try:
        with os.fdopen(fd, "wb") as out, Path(source).open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, target)
        directory_fd = os.open(Path(target).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(name).unlink(missing_ok=True)


def family(connection, thread_id):
    columns = {r[1] for r in connection.execute("PRAGMA table_info(threads)")}
    if not {"id", "rollout_path", "model_provider"} <= columns:
        raise HandoffError("Unsupported thread database; provider switch was not applied.")
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    query = ("WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT child_thread_id "
             "FROM thread_spawn_edges JOIN tree ON parent_thread_id=tree.id) "
             "SELECT threads.* FROM threads JOIN tree USING(id)") if "thread_spawn_edges" in tables else (
                 "SELECT * FROM threads WHERE id=?")
    return {r["id"]: dict(r) for r in connection.execute(query, (thread_id,))}


def rollout_file(home, row):
    """Resolve Codex's logical JSONL path, preferring plain over compressed."""
    path = Path(row["rollout_path"])
    if path.name.endswith(".jsonl.zst"):
        path = path.with_suffix("")
    if path.suffix != ".jsonl":
        raise HandoffError(f"Unsupported rollout format for session {row['id']}.")
    for candidate in (path, Path(str(path) + ".zst")):
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(home.resolve()):
            raise HandoffError(f"Unsafe rollout path for session {row['id']}.")
        if candidate.is_file():
            return candidate
    raise HandoffError(f"Missing history for session {row['id']}; neither JSONL nor its compressed copy exists.")


def zstd_copy(source, target, decompress=False):
    binary = shutil.which("zstd")
    if not binary:
        raise HandoffError("Compressed Codex history needs the zstd executable; no provider was changed.")
    with target.open("xb") as out:
        os.fchmod(out.fileno(), 0o600)
        result = subprocess.run([binary, "-q", *( ["-d"] if decompress else []), "-c", "--", str(source)],
                                stdout=out, stderr=subprocess.DEVNULL, timeout=120)
        if result.returncode:
            raise HandoffError("Cannot decode/encode compressed history; original history is unchanged.")
        out.flush()
        os.fsync(out.fileno())


def clear_item(item):
    """Replace provider-bound reasoning items, including their IDs and metadata.

    Clearing only encrypted_content leaves a server item reference behind. Keep
    the visible summary as ordinary assistant text instead. List positions stay
    stable for parallel compaction metadata arrays.
    """
    if not isinstance(item, dict):
        raise HandoffError("Unsupported history item; nothing was migrated.")
    if item.get("type") != "reasoning":
        return 0
    summary = "\n".join(part["text"] for part in item.get("summary", [])
                        if isinstance(part, dict) and isinstance(part.get("text"), str))
    item.clear()
    item.update(type="message", role="assistant", content=[{
        "type": "output_text", "text": summary or "[Earlier internal reasoning omitted during provider handoff.]"}])
    return 1


def stage_rollout(path, target, thread_id, provider, clear_history, legacy):
    changes = 0
    opaque = 0
    found_meta = False
    with path.open("rb") as inp, target.open("xb") as out:
        os.fchmod(out.fileno(), 0o600)
        for raw in inp:
            if not raw.strip():
                out.write(raw)
                continue
            try:
                record = json.loads(raw)
                payload = record.get("payload", {})
                kind = record.get("type")
                changed = False
                # Older forks prepend their own metadata to a copied parent
                # rollout. Only the first metadata record identifies this file.
                if kind == "session_meta" and not found_meta:
                    if payload.get("id") != thread_id:
                        raise HandoffError("Rollout identity mismatch; nothing was migrated.")
                    found_meta = True
                    if legacy and payload.get("model_provider") != provider:
                        payload["model_provider"] = provider
                        changed = True
                if clear_history and kind == "response_item":
                    opaque += opaque_item(payload)
                    count = clear_item(payload)
                    changes += count
                    changed |= bool(count)
                if clear_history and kind == "compacted":
                    for field in ("replacement_history", "guardian_history"):
                        for item in payload.get(field) or []:
                            opaque += opaque_item(item)
                            count = clear_item(item)
                            changes += count
                            changed |= bool(count)
                if changed:
                    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode()
                    # Keeping byte offsets also preserves every projection cache.
                    # Fail closed if an unusual compact legacy metadata line has
                    # insufficient space for a longer provider name.
                    if not raw.endswith(b"\n") or len(encoded) + 1 > len(raw):
                        raise HandoffError("Rollout metadata has no room for an offset-preserving update; "
                                           "nothing was migrated. Use a shorter account name.")
                    raw = encoded + b" " * (len(raw) - len(encoded) - 1) + b"\n"
                out.write(raw)
            except (ValueError, TypeError, AttributeError):
                raise HandoffError("Invalid rollout record (contents hidden); nothing was migrated.") from None
        out.flush()
        os.fsync(out.fileno())
    if not found_meta:
        raise HandoffError("Rollout metadata is missing; nothing was migrated.")
    return changes, opaque


def opaque_item(item):
    if not isinstance(item, dict):
        return 0
    return int(bool((item.get("encrypted_content") and item.get("type") != "reasoning")
                    or item.get("encrypted_function_args")
                    or any(isinstance(item.get(field), list) and any(
                        isinstance(v, dict) and v.get("encrypted_content") for v in item[field])
                        for field in ("content", "output"))))


def recover(connection, journal_path, rows, home):
    """Rollback an interrupted migration only while all affected writers are held."""
    if not journal_path.exists():
        return
    if journal_path.is_symlink():
        raise HandoffError("Refusing a provider recovery symlink.")
    try:
        journal = json.loads(journal_path.read_text())
        entries = journal["entries"]
        for entry in entries:
            row = rows[entry["id"]]
            path = Path(entry["path"])
            backup = Path(entry["backup"])
            if (path.is_symlink() or path.resolve() != rollout_file(home, row).resolve()
                    or not path.resolve().is_relative_to(home.resolve())
                    or backup.is_symlink() or not backup.resolve().is_relative_to(journal_path.parent.resolve())
                    or digest(backup) != entry["before_hash"]
                    or digest(path) not in (entry["before_hash"], entry["after_hash"])):
                raise ValueError
    except (ValueError, KeyError, TypeError, OSError):
        raise HandoffError("Provider recovery needs inspection; history changed or backup is invalid. "
                           "No recovery files were deleted.") from None
    connection.execute("BEGIN IMMEDIATE")
    try:
        for entry in entries:
            private_copy(entry["backup"], entry["path"])
            connection.execute("UPDATE threads SET model_provider=? WHERE id=?",
                               (entry["provider"], entry["id"]))
            if "model" in rows[entry["id"]]:
                connection.execute("UPDATE threads SET model=? WHERE id=?", (entry.get("model"), entry["id"]))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    journal_path.unlink()


def prepare_resume(binary, home, directory, thread_id, provider, model=None, repair_history=False):
    if not thread_id:
        return
    home, directory = Path(home), Path(directory)
    database = state_database(home)
    if database is None:
        if repair_history:
            raise HandoffError("No state database available for history repair.")
        return
    with contextlib.closing(sqlite3.connect(database)) as connection, contextlib.ExitStack() as locks:
        connection.row_factory = sqlite3.Row
        rows = family(connection, thread_id)
        if thread_id not in rows:
            raise HandoffError("Session missing from state database; nothing was migrated.")
        # Root was released by the launcher's startup gate. Release descendants
        # too, including children hosted by another managed terminal/daemon.
        for child in sorted(rows):
            uuid.UUID(child)
            if child != thread_id:
                locks.enter_context(startup_gate(directory, child))
                request_handoff(directory, home, child, binary=binary)
        if set(family(connection, thread_id)) != set(rows):
            raise HandoffError("The session family changed during handoff; retry.")
        lockdir = home / "thread-writer-locks"
        lockdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for child in sorted(rows):
            fd = os.open(lockdir / f"{child}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stream = locks.enter_context(os.fdopen(fd, "rb"))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HandoffError("A session writer reacquired the family; retry. No history was changed.") from None
        journal_path = directory / f"{thread_id}.provider-recovery.json"
        recover(connection, journal_path, rows, home)
        rows = family(connection, thread_id)
        # Forked children may contain opaque items copied from a different
        # provider even when their own saved provider already matches.
        switching = any(r["model_provider"] != provider for r in rows.values())
        selected = list(rows.values()) if switching or repair_history else []
        if not selected:
            return
        backup_dir = directory / ("provider-backup-" + uuid.uuid4().hex)
        backup_dir.mkdir(mode=0o700)
        entries, changed_items, opaque_items = [], 0, 0
        for row in selected:
            path = rollout_file(home, row)
            compressed = path.suffix == ".zst"
            backup = backup_dir / (row["id"] + (".jsonl.zst" if compressed else ".jsonl"))
            staged = backup_dir / (row["id"] + ".staged")
            private_copy(path, backup)
            decoded = backup_dir / (row["id"] + ".decoded") if compressed else backup
            transformed = backup_dir / (row["id"] + ".transformed") if compressed else staged
            if compressed:
                zstd_copy(backup, decoded, decompress=True)
            changed, opaque = stage_rollout(decoded, transformed, row["id"], provider, True,
                                            row.get("history_mode", "legacy") == "legacy")
            if compressed:
                zstd_copy(transformed, staged)
                decoded.unlink()
                transformed.unlink()
            changed_items += changed
            opaque_items += opaque
            entries.append({"id": row["id"], "path": str(path), "backup": str(backup),
                            "staged": str(staged), "provider": row["model_provider"], "model": row.get("model"),
                            "before_hash": digest(backup), "after_hash": digest(staged)})
        atomic_json(journal_path, {"entries": entries})
        connection.execute("BEGIN IMMEDIATE")
        try:
            if family(connection, thread_id) != rows:
                raise HandoffError("Session metadata changed during preparation; retry.")
            for entry in entries:
                if digest(entry["path"]) != entry["before_hash"]:
                    raise HandoffError("Rollout changed during preparation; provider switch stopped.")
            for entry in entries:
                private_copy(entry["staged"], entry["path"])
                connection.execute("UPDATE threads SET model_provider=? WHERE id=?", (provider, entry["id"]))
                if entry["provider"] != provider and entry["id"] != thread_id and "model" in rows[entry["id"]]:
                    connection.execute("UPDATE threads SET model=? WHERE id=?", (model, entry["id"]))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        journal_path.unlink()
        for entry in entries:
            Path(entry["staged"]).unlink()
        print(f"cs: prepared {len(entries)} session(s) for {provider}; cleared {changed_items} encrypted reasoning item(s). "
              f"Original history backup: {backup_dir}", flush=True)
        if opaque_items:
            print("cs: encrypted summaries/tool payloads are preserved; if the destination rejects those, "
                  "use the original provider to produce a plain-text handoff.", flush=True)
