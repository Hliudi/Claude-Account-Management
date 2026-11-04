#!/usr/bin/env python3
"""`cs` — unified Codex account switch: official default login + sub2api proxies.

Companion to `cx` (isolated official OpenAI accounts). `cs` is the
one place to switch between the *official* default Codex login and one or more
`proxy` accounts (e.g. sub2api gateways, which are base_url + API key, not an
official device-code login).

Design:
  * `native`  -> plain `codex`, using whatever login already exists in the
                 default CODEX_HOME (~/.codex). No credentials are stored here.
  * `proxy`   -> a named gateway: provider overrides plus CS_PROXY_API_KEY,
                 scoped to that one invocation only.

Installed interactive launchers coordinate exclusive writers and preserve the
same session history when switching between native and proxy providers.

Credentials are stored outside any git checkout, mode 600, in ~/.config/codex-switch.
Never print auth contents. Standard library only.
"""
import argparse
import contextlib
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

from cs_session import HandoffError, ownership_dir, request_handoff, run_interactive, startup_gate
from cs_storage import atomic_json, state_database

class CSDir:
    ROOT = Path(os.environ.get("CS_DIR", Path.home() / ".config" / "codex-switch"))
    # Default codex home is ~/.codex unless the user overrides CODEX_HOME.
    APP = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    CONF = ROOT / "accounts.json"


def die(msg):
    print(f"cs: {msg}", file=sys.stderr)
    sys.exit(1)


def private(path, directory=False):
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass


def outside_git(path):
    path = path.resolve()
    for parent in (path, *path.parents):
        if (parent / ".git").exists() or ((parent / "HEAD").is_file() and (parent / "objects").is_dir()):
            die("Refusing to store credentials inside a Git repository.")


def ensure_root():
    outside_git(CSDir.ROOT)
    CSDir.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    private(CSDir.ROOT, True)


def read_conf():
    if not CSDir.CONF.exists():
        return {"accounts": {}, "current": None}
    try:
        data = json.loads(CSDir.CONF.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError, OSError):
        die(f"Invalid account file {CSDir.CONF.name} (contents hidden).")
    try:
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
            raise ValueError
        for name, account in data["accounts"].items():
            if not isinstance(account, dict) or account.get("name") != name:
                raise ValueError
            validate_account(account)
        current = data.get("current")
        if current is not None and (not isinstance(current, str) or current not in data["accounts"]):
            raise ValueError
    except (ValueError, TypeError):
        die("Invalid account file (contents hidden).")
    return data


def write_conf(data):
    outside_git(CSDir.CONF.parent)
    ensure_root()
    atomic_json(CSDir.CONF, data)


def validate_account(account):
    if account.get("type") not in ("native", "proxy"):
        raise ValueError("Account type must be native or proxy.")
    if not isinstance(account.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", account["name"]):
        raise ValueError("Invalid account name.")
    if account["type"] == "proxy":
        url = urlsplit(account.get("base_url", ""))
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Base URL must be an HTTP(S) API root without credentials, query or fragment.")
        if url.path.rstrip("/").endswith(("/responses", "/chat/completions")):
            raise ValueError("Use the API root (for example https://api.poe.com/v1), not an endpoint.")
        if not isinstance(account.get("api_key"), str) or not account["api_key"].strip():
            raise ValueError("A proxy API key is required.")
        if account.get("wire_api", "responses") != "responses":
            raise ValueError("This Codex version requires the Responses API.")
    for field in ("model", "review_model", "provider_name", "model_catalog_url"):
        if field in account and not isinstance(account[field], str):
            raise ValueError("Invalid account settings.")
    for field in ("requires_openai_auth", "supports_websockets"):
        if field in account and not isinstance(account[field], bool):
            raise ValueError("Provider capability settings must be booleans.")


def valid_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", name or ""):
        die("Account names must start with a letter/digit/_, followed by letters, digits, _ or -.")
    if name in {"ls", "list", "who", "whoami", "use", "add", "rm", "test", "help",
                "sessions", "sess", "threads", "resume", "continue", "cont", "run", "install", "native", "exec", "e", "review"}:
        die("That name is reserved for a cs subcommand.")
    return name


def mask(secret):
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return secret[:4] + "*" * (len(secret) - 8) + secret[-4:]


def find_codex():
    path = os.environ.get("CODEX_CLI_PATH")
    saved = CSDir.ROOT / "launcher.json"
    if not path and saved.exists():
        try:
            path = json.loads(saved.read_text())["binary"]
            if not isinstance(path, str) or not path:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            die("Invalid launcher configuration. Set CODEX_CLI_PATH to your original Codex launcher and run cs install.")
    if not path:
        for name in ("codex",):
            found = shutil.which(name)
            if found:
                path = found
                break
    if not path:
        die("Cannot find the codex CLI. Install it and put codex on PATH.")
    if "/" not in path:
        path = shutil.which(path)
    if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
        die("The saved Codex launcher is unavailable. Set CODEX_CLI_PATH to the executable and run cs install.")
    if Path(path).resolve() == Path(__file__).with_name("codex-cs").resolve():
        die("Codex launcher points back to cs. Set CODEX_CLI_PATH to the original Codex/HUD launcher.")
    return path


# codex 0.16x ignores OPENAI_BASE_URL. Custom gateways are configured through
# `model_providers.<id>` + `model_provider`, injected as `-c` overrides so the
# user's ~/.codex/config.toml is never modified. `wire_api` must be "responses".
PROXY_ENV_KEY = "CS_PROXY_API_KEY"

def provider_id(name):
    # A safe TOML bare-ish id derived from the account name.
    return "cs_" + name


def build_command(account, args):
    """Return (argv_without_binary, extra_env) for the given account."""
    try:
        validate_account(account)
    except (ValueError, TypeError):
        die("Invalid account settings; check the URL, API key and provider type (contents hidden).")
    position = 0
    while position < len(args):
        token = args[position]
        if token == "--":
            break
        value = ""
        if token in ("-c", "--config") and position + 1 < len(args):
            position += 1
            value = args[position]
        elif token.startswith("--config="):
            value = token.partition("=")[2]
        elif token.startswith("-c") and token != "-c":
            value = token[2:]
        key = value.partition("=")[0].strip()
        if key in ("model_provider", "openai_base_url") or key.startswith("model_providers."):
            die("Choose the provider through cs accounts; routing overrides would conflict with the displayed account.")
        position += 1
    env = os.environ.copy()
    env["CODEX_HOME"] = str(CSDir.APP.expanduser().resolve())
    if account["type"] != "proxy":
        # native: explicitly drop proxy overrides so the default login wins.
        env.pop("OPENAI_BASE_URL", None)
        env.pop(PROXY_ENV_KEY, None)
        env.pop("CS_ACCOUNT", None)
        return ["-c", 'model_provider="openai"', *args], env

    pid = provider_id(account["name"])
    base_url = account["base_url"].rstrip("/")
    # Build the provider table. Honour the extra fields a gateway config may need
    # (model_catalog_url, requires_openai_auth) so the generated override matches
    # what the gateway documents.
    fields = [
        "name=%s" % json.dumps(account.get("provider_name") or account["name"]),
        "base_url=%s" % json.dumps(base_url),
        "env_key=%s" % json.dumps(PROXY_ENV_KEY),
        'wire_api="responses"',
    ]
    if account.get("model_catalog_url"):
        fields.append("model_catalog_url=%s" % json.dumps(account["model_catalog_url"]))
    if account.get("requires_openai_auth"):
        fields.append("requires_openai_auth=true")
    if "supports_websockets" in account:
        fields.append("supports_websockets=" + str(account["supports_websockets"]).lower())
    provider = "model_providers.%s={%s}" % (pid, ",".join(fields))

    # `model_provider` selects our provider; a model name override may be needed
    # because the user's config.toml pins a model the gateway may not serve.
    overrides = ["-c", provider, "-c", 'model_provider="%s"' % pid]
    if account.get("model"):
        overrides += ["-c", 'model=%s' % json.dumps(account["model"])]
    if account.get("review_model"):
        overrides += ["-c", 'review_model=%s' % json.dumps(account["review_model"])]
    # `disable_response_storage` is ignored by codex 0.16x, so it is not injected.
    env[PROXY_ENV_KEY] = account["api_key"]
    # Any pre-existing official token vars must not shadow the gateway key.
    env.pop("CODEX_API_KEY", None)
    env.pop("CODEX_ACCESS_TOKEN", None)
    env.pop("OPENAI_BASE_URL", None)
    env.pop("OPENAI_API_KEY", None)
    # Put our overrides first so any user-supplied flags (e.g. --model) win.
    return overrides + list(args), env


def launch(account, args, thread_id=None, exec_mode=False, repair_history=False):
    binary = find_codex()
    full_args, env = build_command(account, args)
    env["CS_ACCOUNT"] = account["name"]
    env["CS_ACCOUNT_TYPE"] = account["type"]
    env["CS_MODEL_PROVIDER"] = provider_id(account["name"]) if account["type"] == "proxy" else "openai"
    env["CS_SESSION_LABEL"] = "CS PROXY / " + account["name"] if account["type"] == "proxy" else "CODEX OFFICIAL"
    if account["type"] == "proxy":
        env["CS_GATEWAY_URL"] = account["base_url"]
    else:
        env.pop("CS_GATEWAY_URL", None)
    if has_help(args):
        return subprocess.call([binary, *full_args], env=env)
    print("[" + env["CS_SESSION_LABEL"] + "] " + account_description(account), file=sys.stderr, flush=True)
    ensure_root()
    def prepare(directory):
        from cs_switch import prepare_resume
        prepare_resume(binary, CSDir.APP, directory, thread_id, env["CS_MODEL_PROVIDER"],
                       resume_model(account, args), repair_history=repair_history)

    if exec_mode:
        directory = ownership_dir(CSDir.ROOT, CSDir.APP)
        with startup_gate(directory, thread_id):
            request_handoff(directory, CSDir.APP, thread_id, binary=binary)
            prepare(directory)
            return subprocess.call([binary, *full_args], env=env)
    if any(arg == "--remote" or arg.startswith("--remote=") for arg in args):
        die("Managed sessions use a local writer; --remote is not supported.")
    if "--no-daemon" not in full_args:
        full_args = ["--no-daemon", *full_args]
    result = run_interactive(binary, full_args, env, CSDir.APP, CSDir.ROOT, thread_id,
                             prepare=prepare)
    if account["type"] == "proxy" and thread_id:
        print(f"Official continuation: codex resume {thread_id}", file=sys.stderr)
    return result


def resume_model(account, args):
    """Model inherited by restored children, including the caller's CLI override."""
    config_path = CSDir.APP / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    except (ValueError, UnicodeError):
        die("Cannot parse Codex config.toml (contents hidden).")
    profile = None
    for index, token in enumerate(args):
        if token == "--":
            break
        if token in ("-p", "--profile") and index + 1 < len(args):
            profile = args[index + 1]
        elif token.startswith("--profile="):
            profile = token.partition("=")[2]
    defaults = config.get("profiles", {}).get(profile, {})
    model = account.get("model") or defaults.get("model") or config.get("model")
    for index, token in enumerate(args):
        if token == "--":
            break
        if token in ("-m", "--model") and index + 1 < len(args):
            model = args[index + 1]
        elif token.startswith("--model="):
            model = token.partition("=")[2]
        else:
            value = (args[index + 1] if token in ("-c", "--config") and index + 1 < len(args)
                     else token.partition("=")[2] if token.startswith("--config=")
                     else token[2:] if token.startswith("-c") else "")
            if value.partition("=")[0].strip() == "model":
                model = tomllib.loads(value)["model"]
    return model


def cmd_add(name, force=False, preset=None):
    ensure_root()
    valid_name(name)
    conf = read_conf()
    if name in conf["accounts"] and not force:
        die(f"Account '{name}' already exists. Remove it first, or use --force.")
    print(f"Adding Poe account '{name}' (Responses API)." if preset == "poe" else
          f"Adding account '{name}'. Type: (n)ative official default, or (p)roxy / Responses API.")
    kind = "p" if preset == "poe" else ""
    while kind not in ("n", "p"):
        try:
            kind = input("Type [n/p]: ").strip().lower()
        except EOFError:
            die("Cancelled; account not saved.")
    if kind == "n":
        account = {"name": name, "type": "native"}
    else:
        base_url = "https://api.poe.com/v1" if preset == "poe" else ""
        while not base_url:
            base_url = input("Base URL (e.g. https://gateway.example/v1): ").strip()
        api_key = getpass.getpass("API key (input hidden): ").strip()
        if not api_key:
            die("No API key entered; account not saved.")
        model = input("Model ID / Poe bot name (empty = use config default): ").strip()
        if preset == "poe" and not model:
            die("Poe requires an explicit bot/model name; account not saved.")
        account = {"name": name, "type": "proxy", "base_url": base_url, "api_key": api_key}
        if model:
            account["model"] = model
        if preset == "poe":
            account.update(preset="poe", provider_name="Poe", requires_openai_auth=False,
                           supports_websockets=False, wire_api="responses")
        try:
            validate_account(account)
        except ValueError as error:
            die(str(error))
    conf["accounts"][name] = account
    if conf.get("current") is None:
        conf["current"] = name
    write_conf(conf)
    kind_str = "official default" if account["type"] == "native" else "proxy/sub2api"
    print(f"Saved account: {name} ({kind_str})")


def cmd_ls():
    conf = read_conf()
    acc = conf.get("accounts", {})
    if not acc:
        print("No cs accounts. Use `cs add <name>`.")
        return
    for name in sorted(acc):
        a = acc[name]
        mark = "*" if name == conf.get("current") else " "
        if a["type"] == "proxy":
            extra = f"  model={a['model']}" if a.get("model") else ""
            print(f"{mark} {name}  (proxy: {a['base_url']}  key={mask(a['api_key'])}{extra})")
        else:
            print(f"{mark} {name}  (official default login)")


def cmd_who():
    conf = read_conf()
    cur = conf.get("current")
    active = os.environ.get("CS_ACCOUNT")
    if active:
        active_type = os.environ.get("CS_ACCOUNT_TYPE")
        active_account = conf.get("accounts", {}).get(active)
        if active_type == "native" or (not active_type and active == "native"):
            active_account = {"name": active, "type": "native"}
        elif active_type == "proxy" and os.environ.get("CS_GATEWAY_URL"):
            active_account = {"name": active, "type": "proxy", "base_url": os.environ["CS_GATEWAY_URL"]}
        if active_account is not None:
            print("This process was launched as: " + account_description(active_account))
    if cur and cur in conf.get("accounts", {}):
        print("Next cs launch: " + account_description(conf["accounts"][cur]))
    else:
        print("Next cs launch: no account selected.")
    print("codex: official OpenAI; cs native resume: managed official handoff.")


def account_description(account):
    if account["type"] == "native":
        return "Official OpenAI login (provider=openai)"
    host = urlsplit(account["base_url"]).hostname
    return f"Proxy account={account['name']} gateway={host} (provider={provider_id(account['name'])})"


def cmd_use(name):
    conf = read_conf()
    if name not in conf.get("accounts", {}):
        die(f"No such account '{name}'. Use `cs add {name}`.")
    conf["current"] = name
    write_conf(conf)
    print(f"Selected cs account: {name}")


def cmd_rm(name):
    conf = read_conf()
    acc = conf.get("accounts", {})
    if name not in acc:
        die(f"No such account '{name}'.")
    if conf.get("current") == name:
        conf["current"] = None
    del acc[name]
    write_conf(conf)
    print(f"Removed cs account: {name}")


def cmd_run(name, args):
    conf = read_conf()
    acc = conf.get("accounts", {})
    if name not in acc:
        die(f"No such account '{name}'. Use `cs add {name}` first.")
    return account_cli(acc[name], args)


def state_db(cwd=None):
    # Codex keeps thread metadata in state_<n>.sqlite under CODEX_HOME. Pick the
    # newest one in case codex has bumped the schema number.
    return state_database(CSDir.APP, cwd=cwd)


def cmd_sessions(limit=40, show_all=False):
    """List codex sessions from the shared state database, regardless of which
    provider last ran them. Rows are numbered; `cs resume <N>` continues the Nth.
    This is the cross-provider answer to the picker, which only shows the current
    provider's sessions (built-in `openai` cannot be overridden)."""
    if limit < 1:
        die("Session limit must be positive.")
    rows = load_sessions(show_all=show_all)

    import datetime
    pick = []          # ordered ids, index == displayed number
    print("  #   provider        updated            title")
    print("  " + "-" * 76)
    for r in rows:
        if not show_all:
            if r["agent_path"] or r["thread_source"] not in ("user", None):
                continue
            if r["archived"]:
                continue
        ts = r["updated_at_ms"]
        when = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "?"
        title = (r["name"] or r["title"] or r["first_user_message"] or "").strip().replace("\n", " ")
        if len(title) > 42:
            title = title[:41] + "..."
        prov = r["model_provider"] or "?"
        n = len(pick) + 1
        pick.append(r["id"])
        print(f"  {n:<3} {prov:<14}  {when}  {title}")
        if n >= limit:
            break

    # Persist the numbering so `cs resume <N>` can resolve it.
    try:
        ensure_root()
        idx = CSDir.ROOT / "last-sessions.json"
        database = state_db()
        atomic_json(idx, {"home": str(CSDir.APP.resolve()),
                          "database": str(database.resolve()) if database else None, "ids": pick})
    except OSError:
        pass

    if not pick:
        print("  (no sessions)")
        return

    print()
    print("Continue one of these:")
    print("  cs resume <#>                 via the selected cs account (e.g. tun)")
    print("  codex resume <#>             via the installed official-login wrapper")
    print("  (or give a full session id, or use --all to include sub-agents)")


def resolve_index(token):
    """Map a small 1-based number from `cs sessions` back to its session id."""
    if not token.isdigit():
        return None
    idx = CSDir.ROOT / "last-sessions.json"
    if not idx.exists():
        return None
    try:
        ids = json.loads(idx.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if isinstance(ids, dict):
        if ids.get("home") != str(CSDir.APP.resolve()):
            return None
        database = state_db()
        if ids.get("database") != (str(database.resolve()) if database else None):
            return None
        ids = ids.get("ids")
    elif CSDir.APP.resolve() != (Path.home() / ".codex").resolve() or os.environ.get("CODEX_SQLITE_HOME"):
        return None
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        return None
    n = int(token)
    if 1 <= n <= len(ids):
        return ids[n - 1]
    return None


def load_sessions(show_all=False, cwd=None, database_cwd=None):
    """Return a list of session dicts from the shared codex state DB, newest first.

    Only top-level user sessions are included unless show_all is set. If cwd is
    given, sessions from that directory sort first (like codex's picker) but all
    sessions remain listed.
    """
    import sqlite3
    db = state_db(database_cwd)
    if not db:
        return []
    try:
        with contextlib.closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            columns = {row[1] for row in con.execute("PRAGMA table_info(threads)")}
            if not {"id", "cwd"} <= columns:
                die("Unsupported Codex session database schema.")
            defaults = {"name": "NULL", "title": "''", "first_user_message": "''", "model_provider": "''",
                        "archived": "0", "thread_source": "NULL", "agent_path": "NULL",
                        "updated_at_ms": "updated_at * 1000" if "updated_at" in columns else "0"}
            fields = ["id", "cwd"] + [name if name in columns else f"{default} AS {name}"
                                       for name, default in defaults.items()]
            rows = con.execute("SELECT " + ", ".join(fields) + " FROM threads ORDER BY updated_at_ms DESC").fetchall()
    except sqlite3.Error:
        die("Cannot read the Codex session database (details hidden).")

    out = []
    for r in rows:
        if not show_all:
            if r["agent_path"] or r["thread_source"] not in ("user", None):
                continue
            if r["archived"]:
                continue
        out.append({k: r[k] for k in r.keys()})
    if cwd is not None:
        out.sort(key=lambda s: 0 if s["cwd"] == cwd else 1)
    return out


def _fmt_title(s, width):
    title = (s["name"] or s["title"] or s["first_user_message"] or "").strip()
    title = " ".join(title.split())
    if len(title) > width:
        title = title[:width - 3] + "..."
    return title or "(untitled)"


def read_picker_key(fd):
    import codecs
    import select
    first = os.read(fd, 1)
    if not first or first == b"\x03":
        return "esc"
    if first == b"\x1b":
        sequence = b""
        while len(sequence) < 6 and select.select([fd], [], [], 0.05)[0]:
            sequence += os.read(fd, 1)
            if len(sequence) > 1 and 0x40 <= sequence[-1] <= 0x7E:
                break
        return {b"[A": "up", b"[B": "down", b"[5~": "pgup", b"[6~": "pgdn"}.get(sequence, "esc")
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text = decoder.decode(first)
    while not text and select.select([fd], [], [], 0.05)[0]:
        chunk = os.read(fd, 1)
        if not chunk:
            break
        text += decoder.decode(chunk)
    return text or decoder.decode(b"", final=True)


def pick_session(show_all=False, cwd=None, account=None):
    """Interactive full-screen session picker across ALL providers.

    Keys: Up/Down or k/j = move, PgUp/PgDn = page, Enter = select, q/Esc = cancel,
    and typing filters by title/provider/cwd. Returns the chosen session id or None.
    """
    sessions = load_sessions(show_all=show_all, cwd=cwd, database_cwd=cwd)
    if not sessions:
        return None
    if not sys.stdin.isatty():
        die("Not a terminal; give a session id, or use `cs sessions` to list them.")

    import datetime
    import select
    import termios
    import tty

    query = ""
    selected = 0
    scroll = 0

    def visible():
        if not query:
            return sessions
        q = query.lower()
        return [s for s in sessions
                if q in _fmt_title(s, 999).lower()
                or q in (s["model_provider"] or "").lower()
                or q in (s["cwd"] or "").lower()
                or q in s["id"].lower()]

    def render():
        nonlocal scroll
        cols = shutil.get_terminal_size((90, 24)).columns
        lines = shutil.get_terminal_size((90, 24)).lines
        view_h = max(5, lines - 6)
        items = visible()
        nonlocal_selected = min(selected, max(0, len(items) - 1))
        if nonlocal_selected < scroll:
            scroll = nonlocal_selected
        elif nonlocal_selected >= scroll + view_h:
            scroll = nonlocal_selected - view_h + 1

        buf = []
        buf.append("\x1b[2J\x1b[H")  # clear + home
        header = f" Select a session to resume   ({len(items)}/{len(sessions)})"
        if query:
            header += f"   filter: {query}"
        buf.append(f"\x1b[1;36m{header}\x1b[0m\r\n")
        if account:
            buf.append(account_description(account) + "\r\n")
        buf.append("\x1b[2m up/down = move   type = filter   Enter = resume   Esc/Ctrl+C = cancel\x1b[0m\r\n")
        buf.append("-" * min(cols, 100) + "\r\n")
        for i in range(scroll, min(scroll + view_h, len(items))):
            s = items[i]
            ts = s["updated_at_ms"]
            when = datetime.datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M") if ts else "     ?"
            prov = (s["model_provider"] or "?")[:10]
            here = "\u25b8" if s["cwd"] == os.getcwd() else " "
            title_w = max(10, cols - 32)
            line = f" {here}{when}  {prov:<10}  {_fmt_title(s, title_w)}"
            if i == nonlocal_selected:
                buf.append(f"\x1b[7m{line}\x1b[0m\r\n")
            else:
                buf.append(line + "\r\n")
        if not items:
            buf.append("  (no match)\r\n")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    result = None
    try:
        tty.setraw(fd)
        while True:
            render()
            key = read_picker_key(fd)
            items = visible()
            view_h = max(5, shutil.get_terminal_size((90, 24)).lines - 6)
            if key == "up":
                selected = max(0, selected - 1)
            elif key == "down":
                selected = min(max(0, len(items) - 1), selected + 1)
            elif key == "pgup":
                selected = max(0, selected - view_h)
            elif key == "pgdn":
                selected = min(max(0, len(items) - 1), selected + view_h)
            elif key in ("\r", "\n"):
                if items:
                    result = items[min(selected, len(items) - 1)]["id"]
                break
            elif key == "esc":
                result = None
                break
            elif key in ("\x7f", "\x08"):
                query = query[:-1]
                selected = 0
                scroll = 0
            elif key and key.isprintable():
                query += key
                selected = 0
                scroll = 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    return result


VALUE_FLAGS = {"-c", "--config", "-m", "--model", "-p", "--profile", "-C", "--cd",
               "-s", "--sandbox", "-a", "--ask-for-approval", "--enable", "--disable",
               "--add-dir", "--local-provider", "-i", "--image", "-o", "--output-last-message",
               "--output-schema", "--color", "--remote", "--remote-auth-token-env",
               "--thread-source", "--cyber-access-program", "--permissions"}


def has_help(args):
    position = 0
    while position < len(args):
        token = args[position]
        if token == "--":
            return False
        if token in ("--help", "-h", "--version", "-V"):
            return True
        position += 2 if token in VALUE_FLAGS else 1
    return False


def split_prefix(args):
    prefix, rest = [], list(args)
    while rest and rest[0].startswith("-") and rest[0] != "--":
        flag = rest.pop(0)
        prefix.append(flag)
        if flag in VALUE_FLAGS:
            if not rest:
                die(f"{flag} requires a value.")
            prefix.append(rest.pop(0))
    return prefix, rest


def selected_account():
    conf = read_conf()
    account = conf.get("accounts", {}).get(conf.get("current"))
    if not account:
        die("No cs account selected. Use `cs use <name>` first.")
    return account


def cmd_resume(args, account=None):
    import uuid
    account = account or selected_account()
    if has_help(args):
        return launch(account, ["resume", *args])
    flags, positional = [], []
    options = {"--last": False, "--all": False, "--exec": False, "--repair-history": False,
               "--include-non-interactive": False}
    rest = list(args)
    literal = False
    while rest:
        token = rest.pop(0)
        if token == "--":
            literal = True
            positional.extend(rest)
            break
        if token in options:
            options[token] = True
        elif token.startswith("-") and token != "-":
            flags.append(token)
            if token in VALUE_FLAGS:
                if not rest:
                    die(f"{token} requires a value.")
                flags.append(rest.pop(0))
        else:
            positional.append(token)
    for index, flag in enumerate(flags):
        value = flags[index + 1] if flag in ("-c", "--config") else flag.partition("=")[2] if flag.startswith("--config=") else ""
        if value.split("=", 1)[0].strip() == "sqlite_home":
            die("Configure sqlite_home in CODEX_HOME/config.toml, or CODEX_SQLITE_HOME when unset, so session lookup and Codex agree.")
    if len(positional) > (1 if options["--last"] else 2):
        die("Provide one session ID and at most one quoted prompt, or --last and one quoted prompt.")
    cwd = os.getcwd()
    for index, flag in enumerate(flags):
        if flag in ("-C", "--cd"):
            cwd = str(Path(flags[index + 1]).resolve())
        elif flag.startswith("--cd="):
            cwd = str(Path(flag.partition("=")[2]).resolve())
    if options["--last"]:
        sessions = load_sessions(show_all=options["--include-non-interactive"], database_cwd=cwd)
        if not options["--all"]:
            sessions = [session for session in sessions if session["cwd"] and Path(session["cwd"]).resolve() == Path(cwd)]
        if not sessions:
            die("No saved session in this directory. Use --all to search other directories.")
        positional.insert(0, sessions[0]["id"])
    elif not positional:
        if options["--exec"]:
            die("Non-interactive resume requires a session ID or --last.")
        chosen = pick_session(show_all=options["--include-non-interactive"], cwd=cwd, account=account)
        if not chosen:
            print("cs: no session selected.")
            return 0
        positional = [chosen]
    token = positional[0]
    if token.isdigit():
        token = resolve_index(token)
        if not token:
            die("Unknown session number. Run `cs sessions` again or provide the full session ID.")
    try:
        thread_id = str(uuid.UUID(token))
    except ValueError:
        matches = [session for session in load_sessions(database_cwd=cwd) if session.get("name") == token]
        if len(matches) != 1:
            die("Use a full session ID, a listed number, or a unique saved session name.")
        thread_id = matches[0]["id"]
    positional[0] = thread_id
    if len(positional) > 1 and (literal or positional[1].startswith("-")):
        positional.insert(1, "--")
    command = ["exec", *flags, "resume", *positional] if options["--exec"] else [*flags, "resume", *positional]
    extra = {"repair_history": True} if options["--repair-history"] else {}
    return launch(account, command, thread_id=thread_id, exec_mode=options["--exec"], **extra)


def account_cli(account, args):
    prefix, rest = split_prefix(args)
    if has_help(args):
        return launch(account, args)
    if rest and rest[0] == "resume":
        return cmd_resume(prefix + rest[1:], account=account)
    if rest and rest[0] in ("exec", "e"):
        exec_prefix, exec_rest = split_prefix(rest[1:])
        if exec_rest and exec_rest[0] == "resume":
            return cmd_resume(prefix + exec_prefix + ["--exec"] + exec_rest[1:], account=account)
        return launch(account, prefix + ["exec"] + rest[1:], exec_mode=True)
    if rest and rest[0] == "review":
        return launch(account, args, exec_mode=True)
    headless = {"app", "app-server", "exec-server", "mcp", "mcp-server", "login",
                "logout", "features", "completion", "debug", "apply", "update", "self-update",
                "remote-control", "agents", "queue", "sandbox"}
    if rest and rest[0] in headless:
        if account["type"] != "native":
            die("Use plain codex for administrative commands; cs proxy accounts support interactive, resume, exec and review.")
        binary = find_codex()
        os.execvpe(binary, [binary, *args], os.environ.copy())
    return launch(account, args)


def native_cli(args):
    # Use the shared picker for bare resume too: Codex's provider-scoped picker
    # hides conversations after a proxy becomes their last saved provider.
    return account_cli({"name": "native", "type": "native"}, args)


def cmd_install():
    ensure_root()
    binary = Path(find_codex()).absolute()
    source = Path(__file__).resolve().parent
    bindir = Path.home() / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    if binary == bindir / "codex" and binary.is_symlink():
        target = Path(os.readlink(binary))
        binary = target if target.is_absolute() else binary.parent / target
    links = [("cs", source / "cs"), ("codex", source / "codex-cs")]
    for name, target in links:
        destination = bindir / name
        if not target.is_file() or (destination.exists() and not destination.is_symlink()):
            die(f"Cannot install launcher {destination}; check the source and existing executable.")
    config = CSDir.ROOT / "launcher.json"
    if config.is_symlink():
        die("Refusing to overwrite a launcher config symlink.")
    atomic_json(config, {"binary": str(binary)})
    for name, target in links:
        target.chmod(target.stat().st_mode | 0o111)
        destination = bindir / name
        if destination.exists() and not destination.is_symlink():
            die(f"Refusing to replace {destination}; it is not a symlink.")
        temporary = bindir / f".{name}-cs-install"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, destination)
    marker = '# >>> cs session handoff >>>'
    block = '\n' + marker + '\nexport PATH="$HOME/.local/bin:$PATH"\n# <<< cs session handoff <<<\n'
    for name in (".bashrc", ".profile"):
        path = Path.home() / name
        content = path.read_text() if path.exists() else ""
        if marker not in content:
            path.write_text(content + block)
    print("Installed cs/codex session handoff. Run `source ~/.bashrc` once in existing terminals.")
    print("Shared daemon sessions support handoff; standalone unwrapped terminals must exit once.")


def main(argv):
    if argv and argv[0] == "--native-cli":
        return native_cli(argv[1:])
    if not argv:
        return launch(selected_account(), [])
    if argv[0] in ("help", "-h", "--help"):
        print("""cs — unified Codex account switch (official + sub2api proxy)

Usage:
  cs                            start using the selected account
  cs install                    install coordinated cs and codex launchers
  cs native [args...]            use official login with session handoff
  cs add <name>                 add an account (interactive: native or proxy)
  cs add --force <name>         overwrite an existing account
  cs add <name> --preset poe    add Poe API key and an explicit Poe bot/model name
  cs ls|list                    list accounts (keys masked)
  cs who|whoami                 show the selected account
  cs use <name>                 select the default account for `cs`
  cs rm <name>                  remove an account
  cs sessions [N] [--all]       list codex sessions (shows resume commands)
  cs resume                     open a picker with ALL sessions (any provider)
  cs resume --repair-history ID back up and clear encrypted reasoning for a session tree
  cs resume --last              continue the most recent session directly
  cs resume <id> [prompt...]    resume a specific session
  cs continue                   shortcut for `cs resume --last`
  cs exec resume <id> [prompt]   non-interactive resume with the same handoff
  cs <name> [args...]           run codex as that account (args pass through)
  cs run <name> [args...]       same as `cs <name> [args...]`

Account types:
  native   official OpenAI login (default ~/.codex/auth.json)
  proxy    Responses API gateway (including Poe): base_url + API key, injected via
           model_providers/model_provider (-c overrides, config.toml untouched).
           The gateway must speak the OpenAI **Responses** API (wire_api=responses).
           If your config.toml pins a model the gateway lacks, give a model name
           when adding, or pass --model.

Sessions share their original ID and history. After `cs install`, both cs and
codex use dedicated local writers. Resuming an active managed session requests
the other terminal to exit before taking ownership. /exit releases the writer.
Local shared daemon/App writers are released through archive/unarchive, restoring
the original archive states. Standalone unwrapped terminals must exit once.
`codex resume` (or `cs native resume`) explicitly restores the official provider.
Add --exec to cs resume for non-interactive work. Linux is required for handoff.

Credentials live in ~/.config/codex-switch (mode 600), never inside a git repo.
""")
        return 0

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "install":
        cmd_install()
    elif cmd == "native":
        return account_cli({"name": "native", "type": "native"}, rest)
    elif cmd in ("ls", "list"):
        cmd_ls()
    elif cmd in ("who", "whoami"):
        cmd_who()
    elif cmd == "add":
        parser = argparse.ArgumentParser(prog="cs add")
        parser.add_argument("name")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--preset", choices=["poe"])
        options = parser.parse_args(rest)
        try:
            cmd_add(options.name, force=options.force, preset=options.preset)
        except EOFError:
            die("Cancelled; account not saved.")
    elif cmd == "use":
        if not rest:
            cmd_who()
        else:
            cmd_use(rest[0])
    elif cmd == "rm":
        if not rest:
            die("usage: cs rm <name>")
        cmd_rm(rest[0])
    elif cmd == "run":
        if not rest:
            die("usage: cs run <name> [args...]")
        return cmd_run(rest[0], rest[1:])
    elif cmd in ("sessions", "sess", "threads"):
        limit = 40
        show_all = False
        for a in rest:
            if a == "--all":
                show_all = True
            elif a.isdigit():
                limit = int(a)
        cmd_sessions(limit=limit, show_all=show_all)
    elif cmd == "resume":
        return cmd_resume(rest)
    elif cmd in ("continue", "cont"):
        # Shortcut for `cs resume --last`: continue the most recent session.
        return cmd_resume(["--last"] + rest)
    elif cmd in ("exec", "e", "review") or cmd.startswith("-"):
        return account_cli(selected_account(), argv)
    else:
        # cs <name> [args...]
        return cmd_run(cmd, rest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, HandoffError) as e:
        die(f"operation failed: {e}")
