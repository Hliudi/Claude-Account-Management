<div align="center">

# ca

**Switch between your own Claude subscription accounts — terminal and VS Code, on every machine.**

[![ci](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml/badge.svg)](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![platform](https://img.shields.io/badge/platform-linux%20%C2%B7%20macOS%20%C2%B7%20windows-black)](#platforms)

[Quickstart](#quickstart) · [Commands](#commands) · [VS Code](#vs-code-extension) · [Troubleshooting](#troubleshooting) · [For agents](AGENTS.md) · [中文](README.zh-CN.md)

</div>

---

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
