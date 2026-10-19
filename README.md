<div align="center">

# ca

**Manage your own Claude and Codex accounts — terminal and VS Code, on every machine.**

[![ci](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml/badge.svg)](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![platform](https://img.shields.io/badge/platform-linux%20%C2%B7%20macOS%20%C2%B7%20windows-black)](#platforms)

[Quickstart](#quickstart) · [Codex](#codex-accounts) · [Commands](#commands) · [VS Code](#vs-code-extension) · [Troubleshooting](#troubleshooting) · [For agents](AGENTS.md) · [中文](README.zh-CN.md)

</div>

---

Claude commands remain `ca ...`. Use **`cx ...`** for the independent Codex account manager; see [Codex accounts](#codex-accounts). You can use either manager on its own. The longer `ca codex ...` spelling remains supported.

```console
$ ca usage
✳ work
  5h    █░░░░░░░░░   2%   resets Sep 20 05:20
  week  ██████████  97%   resets Sep 23 07:00
✳ research
  5h    ██░░░░░░░░  18%   resets Sep 20 06:10
  week  █░░░░░░░░░   2%   resets Sep 23 21:00

$ ca research --continue      # weekly limit hit on `work`? same conversation, other account
```

Conversation history lives in `~/.claude/` on the machine, not in the account, so `--continue` and `--resume` carry straight over to the other account.

- **Official credentials only.** Tokens come from `claude setup-token`. No proxy, no gateway, nothing between you and Anthropic.
- **Scoped to one command.** `ca <name>` exports the token for that invocation; plain `claude` still uses the account you logged in with.
- **Tokens stay put.** `~/.config/claude-accts`, mode 600, outside the repo — and `ca` refuses to store them inside a git checkout.

> [!IMPORTANT]
> For accounts **you own**, switched by hand. Sharing accounts, or pooling them behind an automatic router, breaks the Anthropic usage terms.

## Quickstart

```bash
# 1. install (every machine)
git clone https://github.com/Hliudi/Claude-Account-Management.git ~/.claude-accts
~/.claude-accts/ca install            # Windows: double-click install.cmd

# 2. one token per account, on any machine with a browser
claude setup-token                    # log in as account A, copy the token
claude setup-token                    # log in as account B

# 3. store them (input is hidden) and check they work
ca add work
ca add research
ca test
```

`ca install` puts `ca` on your `PATH`, creates the token directory, and — in an interactive terminal — walks you through step 3 right away. Tokens last about a year.

Already set up elsewhere? Copy everything over ssh instead: `ca push user@host-a host-b`.

## Commands

| Command | What it does |
| --- | --- |
| `ca <name> [args…]` | Run Claude Code as that account; args pass through to `claude` |
| `ca <name> --continue` | Same, but first shows which conversation is about to resume (`-c` works too) |
| `ca install` | Install the command, then add accounts interactively |
| `ca setup-token` | Mint a token — works on machines that only have the VS Code extension |
| `ca add <name>` | Store a token (hidden input, or piped on stdin) |
| `ca ls` | List accounts, masked tokens, and the selected one |
| `ca who` | Which account this session is spending — works inside a running session too |
| `ca usage [name…]` | 5-hour and weekly limits for each account |
| `ca test [name…]` | One real call per account to verify the tokens |
| `ca rm <name>` | Forget an account |
| `ca use [name\|--off]` | Pick the account the VS Code extension uses |
| `ca vscode [--apply]` | Set up the VS Code wrapper |
| `ca push <host>…` | Deploy script + tokens to other machines over ssh |
| `ca update` | `git pull` this checkout — every machine installed from it follows |

Account names are yours to pick: letters, digits, `_`, `-`, as long as they aren't subcommands. Everything after the name is Claude Code's own CLI, short flags included: `ca work -c`, `ca work -r`, `ca work --model opus`.

## Platforms

| Platform | Build | Install |
| --- | --- | --- |
| Linux, WSL, servers | `ca` (bash) | `./ca install` |
| macOS | `ca` (bash 3.2 and up) | `./ca install` |
| Windows | `ca-win.ps1` (PowerShell 5.1 / 7) | double-click `install.cmd` |
| Windows + Git Bash | `ca` (bash) | `./ca install` |

Both builds share one token directory, so either works on a Windows box.

Codex management additionally requires **Python 3.11+** on `PATH` (standard library only). Claude commands keep their existing requirements. Keep `cx`, `cx.cmd` and `codex_accounts.py` beside the scripts when installing from a downloaded copy. Git-checkout installations forward to this checkout and use updates immediately.

## Codex accounts

`cx` manages an **isolated CLI home**. Codex App, the IDE extension and plain `codex` keep their existing login. `cx` sets `CODEX_HOME` only for the child CLI process; it does not change your user environment or the App's `auth.json`.

```bash
cx init                         # optional: prepare the isolated home now
cx login work                   # device-code login; check email and type y
cx login personal               # another account; use a private browser window
cx ls
cx work                         # start the CLI as work
cx personal resume --last        # continue the latest chat in this project
# cx personal resume            # choose a saved chat instead
```

Initial setup copies your existing configuration, instructions, skills, rules, profile files and local conversation history into independent CLI files. Session SQLite databases are copied using a consistent backup, and their transcript paths are redirected to the CLI copies. App credentials, daemon state, sandbox secrets, browser state and plugin logins are not imported. Interactive CLI sessions use `--no-daemon` to avoid attaching to the App's background server.

All `cx` accounts share this one CLI home, so changing the CLI account keeps its configuration and saved context. `resume` restores an existing chat; starting `cx work` alone starts a new chat. The initial App import is a **one-time snapshot**, not live synchronization: later App messages and CLI messages do not flow into each other. Client-version differences, active writes or account-specific services can affect whether a particular imported chat resumes; cross-account cache discounts are not guaranteed.

| Command | What it does |
| --- | --- |
| `cx init` | Initialize the independent CLI home and show both paths; runs automatically on first use |
| `cx login <name>` | Official device-code login; show the returned email and require y/yes before saving |
| `cx login <name> --browser` | Explicit browser callback login, with the same email confirmation |
| `cx login <name> --device-auth` | Explicit device-code login (the default) |
| `cx login <name> --with-api-key` | Read an API key from stdin; no email is available for this login |
| `cx add <name>` | Save the current isolated CLI login; never silently import the App login (`save` is an alias) |
| `cx ls` / `cx who` | List accounts / identify the isolated CLI account |
| `cx use <name>` | Select the account for subsequent `cx` launches; App and plain `codex` stay unchanged |
| `cx <name> [args…]` | Select and launch the local CLI, forwarding Codex arguments |
| `cx use --off` | Restore the login from before the first isolated CLI switch, or clear CLI auth if initially absent |
| `cx rm <name>` | Delete a saved account; switch away first if it is selected during a managed switch |
| `cx test` | Run local `codex login status` in the isolated home; no model call or server-side verification |

Exit an existing `cx` session before selecting a different CLI account. The App may remain open. App launching, shared-server/remote commands and overrides of the credential/database/log locations are refused through `cx`; use the normal tools separately for those workflows.

Open the device-code URL in a private/incognito browser window so the regular browser can keep its primary account. Device-code login may need enabling in ChatGPT security settings or workspace permissions. Cancellation, missing email or end-of-input does not save or overwrite an account, and temporary login files are cleaned up. `--force` allows replacing a named account but never skips email confirmation. API-key login retains its stdin flow without email confirmation.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CA_CODEX_DIR` | `~/.config/codex-accts` | Named credentials and CLI switch backup |
| `CX_HOME` | Sibling `codex-cli` directory beside `CA_CODEX_DIR` | Independent CLI configuration, auth, history, databases and logs |
| `CX_APP_HOME` | Inherited `CODEX_HOME`, otherwise `~/.codex` | Read-only source for the initial import |

The three directories must be outside Git for stored credentials and must be separate and non-nested. `CODEX_SQLITE_HOME`, credential storage, database and log paths are scoped to the child CLI. Existing copied `config.toml` content is retained; explicit CLI overrides use file storage and independent state/log paths. Organization-enforced policies still apply. See [official state/environment documentation](https://learn.chatgpt.com/docs/config-file/environment-variables).

Existing named accounts remain usable. Legacy `switch-state.json` backups from the earlier shared-login implementation are preserved and never restored into the App automatically. If you already changed the App login with the old version, confirm/sign in to your main account in the App itself; the new isolated mode leaves its current login alone. Windows uses user-only ACLs and Unix uses directory mode 700/file mode 600. Credential files are plaintext secrets; do not share or sync them.

There is no automatic account rotation, Codex quota query, Codex credential transfer through `ca push`, or simultaneous execution of multiple `cx` accounts. API/access-token environment overrides must be removed before launching a saved account. Custom providers and external plugins may require their own configuration or separate authorization.

**Only the VS Code extension installed?** That works. The extension ships its own `claude` binary, and `ca` falls back to it when the CLI is not on `PATH` — so `ca setup-token`, `ca test` and `ca <name>` all run. Account switching inside the extension never needed the CLI in the first place.

## VS Code extension

The extension spawns Claude itself, so it follows a *selected* account rather than an environment variable you type in a terminal.

**Linux · macOS · Remote-SSH** — one-time setup, then switch freely:

```bash
ca vscode --apply     # installs a wrapper, points claudeCode.claudeProcessWrapper at it
                      # then run "Developer: Reload Window" once
ca use research       # new and reopened chats use `research`; running chats keep theirs
ca use --off          # back to the /login account
```

Over Remote-SSH, run it on the remote machine — it writes `~/.vscode-server/data/Machine/settings.json`. When a settings file contains comments, `ca` leaves it untouched and prints the line to paste.

**Windows** — the extension can't launch a `.cmd` wrapper, so `ca use <name>` sets the user-level `CLAUDE_CODE_OAUTH_TOKEN` instead. Quit VS Code completely and reopen it; new terminals pick the account up too.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `ca: command not found` right after installing | The `PATH` line lands in your shell rc — open a new terminal, or `source ~/.zshrc` |
| `ca test` shows `401 OAuth access token is invalid` | Token expired or revoked: `claude setup-token` again, then `ca add <name>` |
| VS Code still uses the old account | New chat needed (Linux/macOS), or a full quit and reopen (Windows). `ca ls` shows which is selected with `*` |
| `ca vscode --apply` skipped my settings file | It has comments — paste the printed line into `Preferences: Open User Settings (JSON)` yourself |
| `refusing to keep tokens there` | `CA_DIR` points inside a git repository. Leave it at the default or set it elsewhere |
| First reply after switching is slow or costly | Prompt caching is per account, so the context is re-read once. Switch between tasks, not mid-thread |

## Reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `CA_DIR` | `~/.config/claude-accts` | Where tokens and the selection live |
| `CA_BIN_DIR` | `~/.local/bin` (Windows: `%LOCALAPPDATA%\claude-accts\bin`) | Where the command is installed |
| `CA_LANG` | system locale | `zh` or `en` to force the output language |
| `CA_YES` | unset | Any value skips the `--continue` confirmation |
| `NO_COLOR` | unset | Any value disables colored output |

Worth knowing:

- **Which account am I on?** `ca who`. Inside a running Claude Code session, type `!ca who` — the `!` prefix runs a shell command without leaving the session. It reports the account the token belongs to; where only `CA_ACCOUNT` is left in the environment it says "Launched as …", since an inherited value can outlive the session that set it. The terminal tab is renamed too.
- **`--continue` asks first.** It prints the conversation's title, how long ago it was active, and the last message, then waits for Enter. `CA_YES=1` skips the prompt.
- **`/usage` shows no limit bars after switching.** A `setup-token` credential lacks the `user:profile` scope the client needs for that view, so it falls back to a local cost summary. Use `ca usage` instead: it reads the limits from the API's rate-limit response headers, which work under any credential. It costs one Haiku token per account.
- `--continue` resumes conversations from *that* machine; history doesn't travel between machines.
- A `setup-token` credential may lack claude.ai connector permissions — run `/login` on that machine if something is missing.

## License

MIT
