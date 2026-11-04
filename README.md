# Codex Account Manager

Switch Codex providers, continue the same coding session, or keep official accounts in an isolated CLI home. Python 3.11+, no third-party Python packages. Switching compressed Codex history also uses the `zstd` executable.

[中文说明](README.zh-CN.md) · [Agent instructions](AGENTS.md) · [MIT license](LICENSE)

| Command | Purpose | Platforms |
| --- | --- | --- |
| `cs` | Shared-session handoff between official login and Responses API gateways | Linux |
| `cx` | Manually switch official accounts in an isolated CLI home | Linux, macOS, Windows |

## Shared sessions with cs

Clone or download this repository, then run from the checkout:

```bash
./cs install
source ~/.bashrc
cs add gateway
cs use gateway
cs resume
```

Choose a proxy when adding a gateway, enter its API root and hidden API key, and specify its model if different from your default. The gateway must implement the Responses API. Credentials live outside the repository in `~/.config/codex-switch`, with private permissions.

The installer links `cs` and an official-only `codex` wrapper into `~/.local/bin`. It retains the original Codex/HUD launcher as the downstream executable and adds the bin directory to Bash startup files. Existing Git-checkout installations use code updates immediately. Both `codex resume` and `cs resume` open the cross-provider picker: `codex` always continues with official login, while `cs` uses the selected account.

### Continue the same conversation

```bash
codex resume                     # official login
cs resume                        # choose the same session; request ownership
# Enter /exit in cs to release its writer.
codex resume <same-session-id>   # official login, including newly saved proxy history
```

Both commands accept a full session ID or a number from `cs sessions`. `resume --last` searches the current directory across providers; `--all` searches every directory. The managed picker includes conversations last saved by a proxy; `codex resume` and `cs native resume` both continue them with official login. The downstream unwrapped Codex picker can still filter by provider. Session numbers are scoped to their home and database; refresh the list after changing state directories.

Launch banners and terminal titles show `CODEX OFFICIAL` or `CS PROXY / <account>`, with the gateway host printed at startup and in the managed picker. A gateway's configured protocol name can remain OpenAI for compatibility; use the explicit launch identity to identify the credential source. `cs who` distinguishes the current process's inherited launch identity from the account selected for the next `cs` invocation; selecting a proxy does not switch plain `codex` to that account.

Managed terminals run without the shared daemon. Taking over an active managed session requests the original terminal to exit before starting the next writer. `/exit` releases ownership. The session ID and saved history remain the same.

Resuming through `cs` or the installed `codex` wrapper switches the saved provider for the whole conversation tree, including existing children and grandchildren. Restored children use the destination's model when their provider changes. `cs use` only selects the account for a future launch; it does not change running terminals. Child work resumes when Codex restores or addresses that child, rather than being automatically reissued by `cs`.

Before a provider change, `cs` releases and checks every writer in that tree, backs up its rollouts privately under `CS_DIR/ownership`, and replaces provider-bound **reasoning** items with ordinary assistant summaries. This removes their ciphertext, server IDs and replay metadata, including copies in compaction and guardian checkpoints. Existing messages, tool calls/results, session IDs, archive states and pagination offsets are retained. The model loses the opaque reasoning state; the original remains in the backup. Compressed `.jsonl.zst` histories are decoded, processed and recompressed without changing their logical byte offsets. Interrupted changes keep a recovery journal and are recovered on the next managed resume.

If a previous switch already left an `invalid_encrypted_content` error, exit the failing terminal and use:

```bash
cs native resume --repair-history <session-id>  # repair and return to official login
cs Krill resume --repair-history <session-id>   # repair using a saved gateway
```

Repair covers descendants too and makes no model request itself. Encrypted compaction summaries and encrypted tool/message payloads are preserved because they can be the only record of earlier work. If the destination rejects those as well, return to the original provider to produce a plain-text handoff for a new conversation. Missing/corrupt rollouts, compressed history without an available `zstd` executable, unsupported database layouts, a writer that cannot be released, or legacy metadata without room for an offset-preserving provider update stop the launch instead of partially switching accounts. Local mock tests verify routing and replay handling, not every gateway's encrypted payload compatibility.

For a local shared daemon writer, including an older CLI or App chat, handoff temporarily archives and immediately restores the target session and its descendants through the public lifecycle API. Existing windows can briefly receive archive notifications. Recovery journals preserve original archive states; retry the same command if restoration is interrupted. The daemon and unrelated conversations continue running.

Standalone unwrapped terminals that do not use the shared daemon must exit once before takeover. Taking over interrupts active work in the target session and its descendants; unsent drafts are not saved history. A blocking dialog or remapped quit shortcut may require `/exit` in the original terminal. Remote daemon takeover is not supported.

### Commands

| Command | Behavior |
| --- | --- |
| `cs` | Start with the selected account |
| `cs add <name>` | Save a native or proxy account interactively |
| `cs add --force <name>` | Replace an account; cancellation preserves the previous one |
| `cs ls`, `cs who`, `cs use <name>` | List, inspect or select accounts |
| `cs rm <name>` | Remove a saved account |
| `cs sessions [N] [--all]` | List sessions; `--all` also includes archived/sub-agent entries |
| `cs resume [id]`, `cs continue` | Resume a chosen session or the latest session in this directory |
| `cs <name> [args...]` | Run a particular account without changing the selection |
| `cs exec resume <id> "prompt"` | Non-interactive resume with ownership checks |
| `cs native [args...]` | Use official login explicitly |

`cs run <name> exec resume ...` and `codex exec resume ...` also use coordinated resume. Use `--` before a prompt that starts with `-`. The picker supports Unicode filtering, arrows, PageUp/PageDown and Esc/Ctrl+C cancellation.

State discovery follows `config.toml`'s `sqlite_home`, then `CODEX_SQLITE_HOME`, then `CODEX_HOME`. Configure the directory there instead of a transient `-c sqlite_home` override when resuming.

### Poe

```bash
cs add poe --preset poe
cs use poe
cs resume
```

Enter the API key through the hidden prompt and provide an explicit Poe bot/model name. The preset selects `https://api.poe.com/v1`, Responses, an independent API key and HTTP streaming. [Poe documents streaming Responses and function tools](https://creator.poe.com/api-reference/createResponse). Model-specific tool support, parameters and context limits still need live validation. Use `--model` or `-c model_context_window=...` when needed.

## Isolated official accounts with cx

Install from the checkout:

```bash
./cx install                     # Linux/macOS; add ~/.local/bin to PATH if needed
```

On Windows, run `install.cmd` and open a new terminal. You can also use `cx.cmd` directly from the checkout.

```bash
cx init
cx login personal
cx login work
cx ls
cx personal resume --last
cx work resume --last
```

`cx login` uses official device-code login, displays the returned email, and requires confirmation before saving. Browser login is available with `--browser`; API-key login uses `--with-api-key` and stdin. Never paste credentials into an issue or chat.

All `cx` accounts share one isolated CLI home. The App and ordinary shared-session commands retain their own login. Initialization imports a one-time independent copy of configuration, instructions, skills/rules and session history, using SQLite backup for databases. It excludes App authentication, daemon state, browser sessions and plugin logins. Later changes do not synchronize between homes.

| Command | Behavior |
| --- | --- |
| `cx add <name>` | Save the current isolated login |
| `cx use <name>` | Select an isolated account |
| `cx use --off` | Restore the previous isolated login |
| `cx who`, `cx ls` | Inspect selected/saved accounts |
| `cx <name> [args...]` | Run Codex under that account |
| `cx test` | Check local login status without a model request |
| `cx rm <name>` | Remove an unselected account |

Exit other `cx` sessions before switching. `cx` does not connect to the App daemon, and credentials are scoped to child processes.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CS_DIR` | `~/.config/codex-switch` | Shared-session provider accounts and handoff state |
| `CODEX_CLI_PATH` | Original launcher recorded by `cs install` | Downstream Codex executable |
| `CA_CODEX_DIR` | `~/.config/codex-accts` | Isolated account storage |
| `CX_HOME` | Sibling `codex-cli` directory | Isolated CLI state |
| `CX_APP_HOME` | Inherited `CODEX_HOME` or `~/.codex` | Read-only initial import source |
| `CX_BIN_DIR` | `~/.local/bin`; local app-data bin on Windows | `cx` installation destination |

The isolated account directory, CLI home and import source must be separate and non-nested. Stored credentials must stay outside Git and shared/cloud-synced directories.

## Validation

On Linux with Python 3.11+:

```bash
python3 -m unittest discover -s tests -v
```

On macOS/Windows run `python -m unittest discover -s tests -p test_codex_accounts.py -v`. CI tests isolated accounts on all three platforms and shared-session functionality on Linux.

Set `CS_TEST_CODEX_BIN=/path/to/codex` to include real CLI/HUD terminal tests. They use temporary homes, synthetic keys and a local mock Responses server to verify bidirectional resume, tool execution/results, daemon handoff and recovery. These tests do not validate a real gateway account or live Poe model. They never use your existing credentials or make paid model calls.
