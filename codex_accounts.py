#!/usr/bin/env python3
"""Manual Codex account switching. Python 3.11+, standard library only.

Accounts share an isolated CLI home. App files are read only during import.
Never print auth contents or exceptions originating from credential parsing.
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import time
import unicodedata


class AccountError(Exception):
    pass


def fail(message):
    raise AccountError(message)


def private(path, directory=False):
    if os.name == "nt":
        # SID form works on localized Windows. Fail closed if ACL setup fails.
        sid = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
            text=True, check=True,
        ).stdout
        match = re.search(r"S-1-[0-9-]+", sid)
        if not match:
            fail("Cannot determine the Windows user SID.")
        grant = f"*{match[0]}:" + ("(OI)(CI)F" if directory else "F")
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True,
        )
        if result.returncode:
            fail("Cannot restrict credential file permissions.")
    else:
        path.chmod(0o700 if directory else 0o600)


def outside_git(path):
    # Check ancestors too, including a worktree's .git file and symlink targets.
    path = path.resolve()
    for parent in (path, *path.parents):
        if (parent / ".git").exists() or ((parent / "HEAD").is_file()
                                             and (parent / "objects").is_dir()):
            fail("Refusing to store credentials inside a Git repository.")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        fail(f"Invalid JSON credential/state file: {path.name} (contents hidden).")


def write_json(path, value):
    outside_git(path.parent)
    if path.is_symlink():
        fail("Refusing to overwrite a credential symlink.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".ca-", dir=path.parent)
    temp = Path(temp)
    try:
        private(temp)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            fd = None
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if fd is not None:
            os.close(fd)
        temp.unlink(missing_ok=True)


def claims(token):
    try:
        part = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return value if isinstance(value, dict) else {}
    except (AttributeError, IndexError, ValueError, UnicodeError):
        return {}


def login_email(auth):
    # Display metadata from credentials returned by the official CLI. JWT
    # decoding here is for the confirmation screen, not token verification.
    tokens = auth.get("tokens", {})
    for token in (tokens.get("id_token"), tokens.get("access_token")):
        payload = claims(token)
        profile = payload.get("https://api.openai.com/profile", {})
        candidates = [payload.get("email")]
        if isinstance(profile, dict):
            candidates.append(profile.get("email"))
        for email in candidates:
            if (isinstance(email, str) and len(email) <= 254
                    and re.fullmatch(r"[^\s@]+@[^\s@]+", email)
                    and all(not unicodedata.category(char).startswith("C") for char in email)):
                return email
    return None


def confirm_login(auth, name):
    email = login_email(auth)
    if not email:
        fail("Cannot determine the email returned by Codex login; account not saved. Try logging in again.")
    print(f"Logged-in email: {email}", flush=True)
    try:
        answer = input(f"Save this login as '{name}'? [y/N] ")
    except EOFError:
        fail("No confirmation received; account not saved. Run login in an interactive terminal.")
    if answer.strip().lower() not in ("y", "yes"):
        fail("Cancelled; account not saved.")


def identity(auth):
    if not isinstance(auth, dict):
        fail("Unsupported Codex credentials (contents hidden).")
    mode = auth.get("auth_mode")
    key = auth.get("OPENAI_API_KEY")
    if mode in (None, "apikey") and isinstance(key, str) and key.strip():
        return "api", hashlib.sha256(key.encode()).hexdigest()
    tokens = auth.get("tokens")
    if mode in (None, "chatgpt") and isinstance(tokens, dict):
        access = tokens.get("access_token")
        if isinstance(access, str) and access.strip():
            user = claims(tokens.get("id_token")).get("sub") or claims(access).get("sub")
            account = tokens.get("account_id")
            if isinstance(account, str) and account:
                return "chatgpt", account, user
    fail("Unsupported or incomplete Codex credentials. Use an official Codex login.")


RESERVED = {"login", "add", "save", "ls", "list", "who", "whoami", "use", "rm",
            "run", "test", "help", "init"}


def linked(path):
    # Python 3.11 has no Path.is_junction(); catch Windows reparse points too.
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def normal_path(path):
    value = str(Path(path).expanduser().resolve())
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = "\\\\" + value[8:] if value.startswith("\\\\?\\UNC\\") else value[4:]
    return Path(value)


def copy_missing(source, destination):
    """Copy independent files, skipping links, auth and runtime state."""
    if not source.exists() or linked(source):
        return 0
    if destination.exists() and linked(destination):
        fail("Refusing to import into a linked CLI path.")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        return sum(copy_missing(child, destination / child.name) for child in source.iterdir())
    if not source.is_file() or destination.exists():
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".cx-copy-", dir=destination.parent)
    temp = Path(name)
    try:
        private(temp)
        with os.fdopen(fd, "wb") as outgoing:
            fd = None
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, outgoing)
        shutil.copystat(source, temp)
        private(temp)
        if not destination.exists():
            os.replace(temp, destination)
    finally:
        if fd is not None:
            os.close(fd)
        temp.unlink(missing_ok=True)
    return 1


def valid_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", name or ""):
        fail("Account names must start with a letter, digit or _, followed by letters, digits, _ or -.")
    if name.lower() in RESERVED:
        fail("That account name is reserved for a Codex subcommand.")
    if os.name == "nt" and re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", name):
        fail("That account name is reserved by Windows.")
    return name


class Accounts:
    def __init__(self):
        self.root = normal_path(os.environ.get("CA_CODEX_DIR", Path.home() / ".config" / "codex-accts"))
        self.app_home = normal_path(os.environ.get("CX_APP_HOME", os.environ.get("CODEX_HOME", Path.home() / ".codex")))
        self.home = normal_path(os.environ.get("CX_HOME", self.root.parent / "codex-cli"))
        for left, right in ((self.root, self.home), (self.app_home, self.home), (self.app_home, self.root)):
            if left == right or left in right.parents or right in left.parents:
                fail("App home, CX_HOME and CA_CODEX_DIR must be separate, non-nested directories.")
        self.auth = self.home / "auth.json"
        # Preserve old shared-home backups; never restore one into the App.
        self.state = self.root / "cli-switch-state.json"

    def child_env(self, home=None):
        home = self.home if home is None else home
        env = os.environ.copy()
        env.update(CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(home), CX_HOME=str(self.home), CX_APP_HOME=str(self.app_home))
        return env

    def overrides(self, home=None):
        home = self.home if home is None else home
        return ["-c", 'cli_auth_credentials_store="file"',
                "-c", "sqlite_home=" + json.dumps(str(home)),
                "-c", "log_dir=" + json.dumps(str(home / "log"))]

    def snapshot_database(self, source):
        dest = self.home / source.name
        if dest.exists() or linked(source):
            return 0
        fd, name = tempfile.mkstemp(prefix=".cx-db-", dir=self.home)
        os.close(fd)
        temp = Path(name)
        private(temp)
        deadline = time.monotonic() + 30
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                fail("Session database snapshot timed out. Close active App writes and retry `cx init`.")
        try:
            # SQLite backup reads a consistent snapshot including committed WAL
            # pages. Never copy a live database file with ordinary file I/O.
            with contextlib.closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as origin:
                with contextlib.closing(sqlite3.connect(temp)) as target:
                    origin.backup(target, pages=256, progress=progress, sleep=0.05)
                    tables = {r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "threads" in tables:
                        columns = {r[1] for r in target.execute("PRAGMA table_info(threads)")}
                        if "rollout_path" in columns:
                            for rowid, value in target.execute("SELECT rowid, rollout_path FROM threads").fetchall():
                                if not value:
                                    continue
                                path = Path(value)
                                if not path.is_absolute():
                                    path = self.app_home / path
                                try:
                                    relative = normal_path(path).relative_to(self.app_home)
                                except ValueError:
                                    fail("A saved chat references a transcript outside App home; snapshot not imported.")
                                target.execute("UPDATE threads SET rollout_path=? WHERE rowid=?", (str(self.home / relative), rowid))
                    if "remote_control_enrollments" in tables:
                        target.execute("DELETE FROM remote_control_enrollments")
                    target.commit()
                    target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    target.execute("PRAGMA journal_mode=DELETE")
            os.replace(temp, dest)
            return 1
        finally:
            for path in (temp, Path(str(temp) + "-wal"), Path(str(temp) + "-shm")):
                path.unlink(missing_ok=True)

    def prepare(self):
        outside_git(self.home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        private(self.home, True)
        marker = self.home / ".cx-isolated.json"
        if marker.exists():
            data = read_json(marker)
            if (not isinstance(data, dict) or not isinstance(data.get("source"), str)
                    or normal_path(data["source"]) != self.app_home):
                fail("CLI home was initialized from another App home. Set CX_APP_HOME to the original source or choose a new CX_HOME.")
            return
        count = 0
        for name in ("config.toml", "AGENTS.md", "instructions.md", "skills", "rules", "prompts", "agents", "hooks", "sessions", "archived_sessions", "history.jsonl", "session_index.jsonl"):
            count += copy_missing(self.app_home / name, self.home / name)
        for source in self.app_home.glob("*.config.toml"):
            count += copy_missing(source, self.home / source.name)
        # These are history snapshots, never shared database handles. Auth,
        # daemon sockets, sandbox secrets, browser state and plugin logins stay
        # in the App home and are not imported.
        for pattern in ("thread_history_*.sqlite", "state_*.sqlite"):
            for source in self.app_home.glob(pattern):
                count += self.snapshot_database(source)
        write_json(marker, {"version": 1, "source": str(self.app_home)})
        print(f"Initialized isolated CLI home ({count} files copied). App login is unchanged.")
        if (self.root / "switch-state.json").exists():
            print("A legacy shared-login backup was preserved. Isolated mode will not restore or change the App login.")

    @contextlib.contextmanager
    def lock(self):
        outside_git(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private(self.root, True)
        path = self.root / ".lock"
        if path.is_symlink():
            fail("Refusing to open a lock symlink.")
        with path.open("a+b") as stream:
            private(path)
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    fail("Another Codex account operation is running; try again when it finishes.")
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    fail("Another Codex account operation is running; try again when it finishes.")
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def profile(self, name):
        return self.root / (valid_name(name) + ".auth.json")

    def names(self):
        return sorted(p.name[:-10] for p in self.root.glob("*.auth.json"))

    def load(self, path):
        if path.is_symlink():
            fail("Credential symlinks are not supported.")
        if not path.is_file():
            fail(f"No credentials in {path.name}. Use `cx login <name>` or `cx add <name>`.")
        value = read_json(path)
        identity(value)
        return value

    def load_state(self):
        state = read_json(self.state)
        if (not isinstance(state, dict) or not isinstance(state.get("home"), str)
                or "original" not in state):
            fail("Invalid switch backup; leaving the active login unchanged.")
        if state["original"] is not None:
            identity(state["original"])
        return state

    def file_store(self):
        self.prepare()
        # TOML parser prevents accidentally accepting a setting in a nested table.
        config = self.home / "config.toml"
        try:
            if config.exists():
                tomllib.loads(config.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            fail("Cannot parse Codex config.toml (contents hidden); fix it before switching.")
        # Scoped CLI overrides force file storage and independent state/log
        # paths; the copied config and the App's store choice stay untouched.
        outside_git(self.home)

    def current_name(self):
        if not self.auth.exists():
            return None
        current = identity(self.load(self.auth))
        preferred = self.load_state().get("current") if self.state.exists() else None
        names = self.names()
        if preferred in names:
            names.remove(preferred)
            names.insert(0, preferred)
        for name in names:
            if identity(self.load(self.profile(name))) == current:
                return name
        return None

    def sync(self):
        # Save refreshed tokens before switching away. Never replace a profile
        # with an unrelated login made outside this tool.
        if not self.auth.exists():
            return
        name = self.current_name()
        if name:
            saved = self.profile(name)
            # A fresh isolated login may have replaced this profile while the
            # active file still holds older tokens for the same account.
            if self.auth.stat().st_mtime_ns >= saved.stat().st_mtime_ns:
                write_json(saved, self.load(self.auth))
        elif self.state.exists():
            fail("Active login changed outside this tool. Save it with `cx add <name>` before switching.")

    def add(self, name, force=False):
        self.file_store()
        dest = self.profile(name)
        if dest.exists() and not force:
            fail("Account already exists. Use --force to replace its saved credentials.")
        write_json(dest, self.load(self.auth))
        print(f"Saved Codex account: {name}")

    def use(self, name):
        self.file_store()
        if self.state.exists() and normal_path(self.load_state()["home"]) != self.home:
            fail("CX_HOME differs from the saved CLI switch. Restore using the original CX_HOME first.")
        if not name:
            print("Codex account: " + (self.current_name() or "unmanaged / not logged in"))
            return
        if name != "--off":
            self.load(self.profile(name))
        if name == "--off" and not self.state.exists():
            print("No switch to undo.")
            return
        self.sync()
        if not self.state.exists():
            write_json(self.state, {"home": str(self.home), "original": self.load(self.auth) if self.auth.exists() else None})
        state = self.load_state()
        if normal_path(state["home"]) != self.home:
            fail("CX_HOME differs from the saved CLI switch. Restore using the original CX_HOME first.")
        if name == "--off":
            original = state.get("original")
            if original is None:
                self.auth.unlink(missing_ok=True)
            else:
                identity(original)
                # The original account may have been used and refreshed since
                # its backup was made. Restore its most recently saved tokens.
                matches = [self.profile(n) for n in self.names()
                           if identity(self.load(self.profile(n))) == identity(original)]
                if matches:
                    original = self.load(max(matches, key=lambda p: p.stat().st_mtime_ns))
                write_json(self.auth, original)
            self.state.unlink()
            print("Restored the Codex login from before the first CLI switch. App login is unchanged.")
        else:
            # sync may have refreshed the selected profile since target was read.
            write_json(self.auth, self.load(self.profile(name)))
            state["current"] = name
            write_json(self.state, state)
            print(f"Selected Codex account: {name}")
        print("CLI selection only. Codex App and plain `codex` keep their own login.")

    def binary(self):
        # Windows .cmd requires cmd.exe; prefer the npm shim's JS entry instead,
        # avoiding shell quoting and preserving arbitrary forwarded arguments.
        for name in (("codex.exe", "codex.cmd", "codex") if os.name == "nt" else ("codex",)):
            found = shutil.which(name)
            if not found:
                continue
            if Path(found).suffix.lower() == ".cmd":
                script = Path(found).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
                node = shutil.which("node")
                if node and script.is_file():
                    return [node, str(script)]
                continue
            return [found]
        fail("Cannot find Codex CLI. Install it and put codex on PATH.")

    def login(self, name, flags=None, force=False):
        if flags is None:
            flags = ["--device-auth"]
        dest = self.profile(name)
        if dest.exists() and not force:
            fail("Account already exists. Use --force to log in again.")
        binary = self.binary()
        # Isolated official login: never log out or alter the live Codex session.
        with tempfile.TemporaryDirectory(prefix="login-", dir=self.root) as temp:
            temp = Path(temp)
            private(temp, True)
            env = self.child_env(temp)
            if "--device-auth" in flags:
                print("Device-code login: open the displayed link in a private/incognito window and sign in to the intended account.", flush=True)
            result = subprocess.run(binary + self.overrides(temp) + ["login"] + flags, env=env)
            if result.returncode:
                return result.returncode
            auth = self.load(temp / "auth.json")
            api_login = "--with-api-key" in flags
            if (identity(auth)[0] == "api") != api_login:
                fail("Codex returned a different login type than requested; account not saved.")
            if api_login:
                print("Logged in with an API key; no email is available for this login.")
            else:
                confirm_login(auth, name)
            write_json(dest, auth)
        print(f"Saved Codex account: {name}. Select with `cx use {name}`.")
        return 0

    def run(self, name, args):
        if any(os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")):
            fail("API/access-token environment variables can override the selected login. Unset them before launching a saved account.")
        blocked = {"login", "logout", "app", "app-server", "agents", "remote-control", "exec-server", "queue"}
        if any(arg in blocked or arg.startswith("--remote") for arg in args):
            fail("This command can use a different login or shared server. Use cx login/use for accounts and local interactive/resume/exec commands here.")
        if any(re.match(r"^(?:--config=|-c)?\s*(?:sqlite_home|log_dir|cli_auth_credentials_store)\s*=", arg) for arg in args):
            fail("CX manages credential, database and log paths to keep the App isolated.")
        binary = self.binary()
        self.use(name)
        env = self.child_env()
        env["CA_CODEX_ACCOUNT"] = name
        # Interactive sessions (including resume/fork) must not attach to the
        # App's shared background server. exec/review run independently.
        valued_options = {"-c", "--config", "-m", "--model", "-p", "--profile", "-s", "--sandbox",
                          "-a", "--ask-for-approval", "-C", "--cd", "-i", "--image", "--enable",
                          "--disable", "--add-dir", "--local-provider"}
        position = 0
        while position < len(args) and args[position].startswith("-"):
            position += 2 if args[position] in valued_options else 1
        command = args[position] if position < len(args) else None
        direct = [] if command in ("exec", "e", "review") else ["--no-daemon"]
        try:
            return subprocess.run(binary + self.overrides() + direct + args, env=env).returncode
        finally:
            self.sync()


def parser():
    result = argparse.ArgumentParser(
        prog="cx", description="Manage your own Codex accounts (manual switching).",
        epilog="cx <name> [Codex args...] uses an isolated CLI home. App and plain codex keep their login. Exit other cx sessions before switching. Python 3.11+ required.")
    subs = result.add_subparsers(dest="command")
    login = subs.add_parser("login", help="device-code login, then display email and confirm before saving")
    login.add_argument("name")
    login.add_argument("--force", action="store_true")
    method = login.add_mutually_exclusive_group()
    method.add_argument("--device-auth", action="store_true", help="device-code login (default)")
    method.add_argument("--browser", action="store_true", help="explicitly use browser login instead of device-code login")
    method.add_argument("--with-api-key", action="store_true", help="read an API key from stdin (email unavailable)")
    add = subs.add_parser("add", aliases=["save"], help="save the current isolated CLI login (never the App login)")
    add.add_argument("name")
    add.add_argument("--force", action="store_true")
    subs.add_parser("ls", aliases=["list"], help="list saved accounts without displaying credentials")
    subs.add_parser("who", aliases=["whoami"], help="show the account matching the current auth.json")
    use = subs.add_parser("use", help="switch only the isolated CLI login, preserving its config and history")
    group = use.add_mutually_exclusive_group()
    group.add_argument("name", nargs="?")
    group.add_argument("--off", action="store_true", help="restore the login from before the first switch")
    rm = subs.add_parser("rm", help="delete a saved account; selected accounts must be switched away first")
    rm.add_argument("name")
    run = subs.add_parser("run", help="select an account, then launch Codex")
    run.add_argument("name")
    run.add_argument("args", nargs=argparse.REMAINDER)
    subs.add_parser("test", help="run codex login status (local check, no model request)")
    subs.add_parser("init", help="copy configuration and a session snapshot into the isolated CLI home, without App credentials")
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cli = parser()
    if not argv or argv[0] == "help":
        cli.print_help()
        return 0
    if argv[0] not in RESERVED and not argv[0].startswith("-"):
        argv.insert(0, "run")
    args = cli.parse_args(argv)
    accounts = Accounts()
    with (contextlib.nullcontext() if args.command in ("ls", "list", "who", "whoami") else accounts.lock()):
        command = args.command
        if command == "login":
            flags = (["--with-api-key"] if args.with_api_key else
                     [] if args.browser else ["--device-auth"])
            return accounts.login(args.name, flags, args.force)
        if command in ("add", "save"):
            accounts.add(args.name, args.force)
        elif command in ("ls", "list"):
            current = accounts.current_name()
            for name in accounts.names():
                kind = identity(accounts.load(accounts.profile(name)))[0]
                print(f"{'*' if name == current else ' '} {name}  ({'ChatGPT' if kind == 'chatgpt' else 'API key'})")
            if not accounts.names():
                print("No Codex accounts. Run `cx add <name>` or `cx login <name>`.")
        elif command in ("who", "whoami"):
            print("Codex account: " + (accounts.current_name() or "unmanaged / not logged in"))
        elif command == "use":
            accounts.use("--off" if args.off else args.name)
        elif command == "rm":
            path = accounts.profile(args.name)
            if accounts.state.exists() and accounts.current_name() == args.name:
                fail("Switch to another account or run `cx use --off` before removing the selected account.")
            if not path.is_file():
                fail("No saved account with that name.")
            path.unlink()
            print(f"Removed Codex account: {args.name}")
        elif command == "run":
            return accounts.run(args.name, args.args[1:] if args.args[:1] == ["--"] else args.args)
        elif command == "test":
            accounts.file_store()
            return subprocess.run(accounts.binary() + accounts.overrides() + ["login", "status"], env=accounts.child_env()).returncode
        elif command == "init":
            accounts.prepare()
            print(f"CLI home: {accounts.home}\nApp home (read only): {accounts.app_home}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AccountError as error:
        print(f"cx: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        print("cx: file, permission or CLI operation failed (details hidden to protect credentials).", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
