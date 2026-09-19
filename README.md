# ca

Switch between your own Claude subscription accounts — in the terminal and in the VS Code extension, on every machine you work on.

[中文说明](README.zh-CN.md)

```console
$ ca ls
  work           …k7Fq2A  2026-09-20
* research       …p2Xm9A  2026-09-20

$ ca research --continue      # weekly limit hit on `work`? same conversation, other account
```

Conversation history lives in `~/.claude/` on your machine, not in the account, so `--continue` and `--resume` carry straight over.

- Official `claude setup-token` credentials. No proxy, no gateway, nothing between you and Anthropic.
- `ca <name>` exports the token for that one command. Plain `claude` still uses the account you logged in with.
- Tokens stay in `~/.config/claude-accts`, mode 600, outside this repo. `ca` refuses to keep them inside a git checkout.

> For accounts **you own**, switched by hand. Sharing accounts or pooling them behind an automatic router breaks the Anthropic usage terms.

## Install

```bash
git clone https://github.com/Hliudi/Claude-Account-Management.git ~/.claude-accts
~/.claude-accts/ca install          # Windows: double-click install.cmd
```

The installer puts `ca` on your `PATH`, then asks for each account's token (input stays hidden). Get a token by running `claude setup-token` once per account, on any machine with a browser; it lasts about a year.

`ca update` pulls the latest version — everything installed from a checkout runs the checkout.

| Platform | Build | Install |
| --- | --- | --- |
| Linux, WSL, servers | `ca` (bash) | `./ca install` |
| macOS | `ca` (bash 3.2 and up) | `./ca install` |
| Windows | `ca-win.ps1` (PowerShell 5.1 / 7) | double-click `install.cmd` |
| Windows + Git Bash | `ca` (bash) | `./ca install` |

Both builds share the same token directory, so a Windows box can use either.

## Use

```bash
ca work                  # start Claude Code as `work`
ca research --continue   # resume the last conversation as `research`
ca ls                    # accounts, masked tokens, selected account
ca test                  # one real call per account, to check the tokens
ca add <name>            # store another token
ca rm <name>             # forget one
ca push host-a host-b    # copy script + tokens to other machines over ssh
```

Names are yours to pick (letters, digits, `_`, `-`) as long as they aren't subcommands. Anything after the name goes to `claude` untouched.

## VS Code extension

The extension spawns Claude itself, so it reads a selected account instead of an environment variable you type.

**Linux, macOS, Remote-SSH** — one-time setup, then switch freely:

```bash
ca vscode --apply        # installs a wrapper and points claudeCode.claudeProcessWrapper at it
                         # then run "Developer: Reload Window" once
ca use research          # new chats (and reopened ones) use `research`; running chats keep theirs
ca use --off             # back to the /login account
```

Over Remote-SSH, run it on the remote machine: it writes `~/.vscode-server/data/Machine/settings.json`. If a settings file has comments, `ca` leaves it alone and prints the line to paste.

**Windows** — the extension can't run a `.cmd` wrapper, so `ca use <name>` sets the user-level `CLAUDE_CODE_OAUTH_TOKEN` instead. Quit VS Code completely and reopen it; new terminals pick the account up too.

## Good to know

- Usage and limits are counted per account by the server, so `/usage` reflects the account you switched to. The email shown by `claude auth status` still comes from the local `/login` credentials.
- The first turn after a switch re-reads the whole context (prompt caching is per account). Switch at a natural break in long conversations.
- `--continue` resumes conversations from *that* machine; history doesn't travel between machines.
- A `setup-token` credential may not carry claude.ai connector permissions. Run `/login` on that machine if something is missing.
- Output follows `NO_COLOR`, and speaks Chinese when your locale does (`CA_LANG=zh` or `CA_LANG=en` to force).
- `CA_DIR` and `CA_BIN_DIR` override where tokens and the command go.

## License

MIT
