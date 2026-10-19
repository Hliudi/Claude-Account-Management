# Setting up `ca` on this machine

Instructions for a coding agent (Claude Code, Codex, …) asked to set up Claude account switching here. Human-facing docs: [README.md](README.md).

## What this tool does

`ca <name>` runs Claude Code with `CLAUDE_CODE_OAUTH_TOKEN` set to a stored token, for that one command. Tokens come from `claude setup-token` and live in `~/.config/claude-accts` (mode 600). Nothing else on the machine is reconfigured.

## What you can do, and what the human must do

| Step | Who |
| --- | --- |
| Clone and install | you |
| Generate a token (`ca setup-token`, or `claude setup-token` — needs a browser login) | the human |
| Paste the token into `ca add` | the human, at their terminal |
| Verify, select accounts, set up VS Code | you |

**Never ask for a token to be pasted into the chat, and never write one into a file, a commit, or an environment variable you echo.** `ca add` reads it with hidden input. If you must run it yourself, pipe from a file the human wrote and delete that file afterwards.

## If only the VS Code extension is installed

That is enough. The extension ships its own `claude` binary and `ca` falls back to it, so `ca setup-token`, `ca test` and `ca <name>` work without the CLI. Switching accounts inside the extension never needed the CLI.

## Install

```bash
git clone https://github.com/Hliudi/Claude-Account-Management.git ~/.claude-accts
~/.claude-accts/ca install </dev/null      # </dev/null keeps it non-interactive
export PATH="$HOME/.local/bin:$PATH"       # the rc line only applies to new shells
ca ls                                       # confirm it runs
```

Windows (PowerShell):

```powershell
git clone https://github.com/Hliudi/Claude-Account-Management.git $HOME\.claude-accts
& $HOME\.claude-accts\ca-win.ps1 install
```

Then tell the human, verbatim:

> Run `claude setup-token` once per account, then `ca add <name>` and paste each token when prompted (input stays hidden).

## Verify

```bash
ca test            # one real API call per account; ✓ means the token works
```

A `401 OAuth access token is invalid` means the token is expired or was mistyped — the human regenerates it with `claude setup-token`.

## Which account is in use

```bash
ca who      # this shell / session, plus the account selected for VS Code
```

`ca <name>` exports `CA_ACCOUNT`, so `ca who` also answers correctly from inside a running Claude session.

```bash
ca usage           # 5-hour and weekly limits per account, read from API response headers
```

`/usage` inside Claude cannot show limit bars under a `setup-token` credential (it lacks the `user:profile` scope) — expected, not a misconfiguration. Use `ca usage`.

## Everyday use

```bash
ca work --continue      # resume the last conversation on this machine as `work`
ca research             # new conversation as `research`
```

Conversation history is local to the machine (`~/.claude/`), not tied to the account, so switching accounts keeps `--continue` working.

## VS Code extension (optional)

```bash
ca vscode --apply       # installs the wrapper and configures claudeCode.claudeProcessWrapper
ca use research         # extension uses `research` for new and reopened chats
```

Then tell the human to run **Developer: Reload Window** once. Over Remote-SSH, run the commands on the remote machine. On Windows, skip `ca vscode`: `ca use <name>` is enough, followed by a full VS Code restart.

## Deploying to more machines

From a machine that already has the tokens, with ssh access to the target:

```bash
ca push user@host          # copies script + tokens over ssh, installs on the far side
```

Otherwise repeat the install steps there; tokens are per machine and the human pastes them again.

## Codex account management

The same installation supports `cx ...` (also `ca codex ...`), implemented in `codex_accounts.py` (Python 3.11+, standard library). Existing `ca ...` commands continue to manage Claude independently.

- `cx add <name>` saves the current file-based login; `cx login <name>` defaults to official device-code login in an isolated temporary home. It displays the returned email and requires y/yes confirmation before saving; missing email, cancellation or EOF must not overwrite accounts. `--browser` explicitly selects browser callback login; `--device-auth` remains supported. `--force` does not bypass confirmation. API-key login has no email and keeps its stdin flow. Browser/device login, save confirmation and any API-key input belong to the human at their terminal. Never request credentials in chat.
- `cx use <name>` selects only the isolated CLI account. `cx <name> [args...]` launches with child-only CODEX_HOME/CODEX_SQLITE_HOME, file auth and independent database/log overrides. Interactive CLI uses --no-daemon. Never write the App auth/config, globally export CODEX_HOME, share writable state via links or connect to the App daemon. Exit other cx sessions before switching; App may stay open.
- `cx use --off` restores only the prior isolated CLI login. CA_CODEX_DIR defaults to ~/.config/codex-accts; CX_HOME defaults to sibling codex-cli. CX_APP_HOME (inherited CODEX_HOME or ~/.codex) is the read-only import source. The three paths must be non-nested. Old shared switch-state.json backups must never be restored into the App automatically. Named accounts remain reusable.
- cx init (automatic on first use) makes independent copies of config, instructions, skills/rules and session history once; use SQLite backup for history databases and redirect rollout paths to copied transcripts. Do not copy App auth, daemon state, browser sessions, sandbox secrets or plugin logins. No live App/CLI synchronization is promised. cx add saves only an existing isolated CLI login.
- File storage and private database/log paths are scoped CLI overrides; copied and App config remain untouched. Do not change organization policies or user environment. Do not upload, log, commit or share auth files or switch backups.
- Verify changes with `python -m unittest discover -s tests -v`; tests use only synthetic credentials and temporary homes. Never switch, log out or make paid model calls with the user's live accounts merely to test code.

## Claude account rules

- Don't put tokens in the repo, a shared filesystem, a container image, or a cloud sync folder. `ca` refuses a `CA_DIR` inside a git checkout.
- Don't set `CLAUDE_CODE_OAUTH_TOKEN` in `settings.json`, a shell rc, or CI config; `ca` scopes it to one command on purpose.
- Only for accounts the human owns, switched by hand. Never build automatic rotation or account pooling on top of this — that breaks the Anthropic usage terms.
- Nothing here needs sudo.
