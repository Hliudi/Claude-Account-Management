# Codex Account Manager

This repository provides two independent tools:

- `cs`: official/proxy provider selection and shared-session handoff on Linux.
- `cx`: isolated official Codex accounts on Linux, macOS and Windows.

Read `README.md` and `README.zh-CN.md` for usage. Python 3.11+ and the standard library are required.

## Credentials

- Never request API keys, tokens or auth files in chat. Account setup uses hidden terminal input or official browser/device login.
- Never print credentials, write them into the repository, commit them or include them in diagnostics. Store credentials outside Git with owner-only permissions.
- Use only synthetic credentials and temporary homes in tests. Do not make paid requests or change live account logins merely to test code.
- Preserve the user's existing configuration, account files, unrelated processes and unfinished work.

## Shared sessions

- `cs_accounts.py` handles configuration, arguments, the picker and launcher installation.
- `cs_session.py` coordinates managed terminal writers through a local socket and PTY.
- `cs_daemon.py` releases shared daemon writers through archive/unarchive, preserving session IDs and archive states. Keep recovery journals until restoration succeeds.
- `cs_storage.py` handles private atomic writes and database discovery.
- Never remove a live writer-lock file or stop an entire shared daemon to take over one conversation. Check actual lock ownership before and after handoff.
- Interactive managed writers use `--no-daemon`; both official and proxy resume commands retain the original history and explicitly choose the provider.
- Changing a provider must scope its key and overrides to the child process. Keep the original official auth/config and the existing HUD launcher.
- Poe uses the Responses API preset. Local mock tests do not establish compatibility with every live bot or API account.

## Isolated accounts

- `codex_accounts.py` implements `cx`; `cx`, `cx.ps1` and `cx.cmd` are standalone launchers.
- `cx login` defaults to device-code login in a temporary home. Display the returned email and require y/yes before saving; cancellation, EOF or missing email must not overwrite an account. `--force` does not bypass confirmation. API-key login retains its stdin flow.
- `CA_CODEX_DIR`, `CX_HOME` and `CX_APP_HOME` must be separate and non-nested. The import source is read-only; never restore legacy shared-login backups into it.
- Initialize with independent copies of configuration/history and SQLite backup, redirecting copied rollout paths. Do not import App credentials, daemon state, browser sessions, sandbox secrets or plugin logins. Import is a one-time snapshot, not live synchronization.
- Scope `CODEX_HOME`, `CODEX_SQLITE_HOME`, file credential storage and database/log overrides to the isolated child. Do not connect `cx` to the App daemon or change organization policies.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

Run all tests on Linux. On macOS/Windows run `python -m unittest discover -s tests -p test_codex_accounts.py -v`; shared-session tests require Linux APIs.

Set `CS_TEST_CODEX_BIN` to an installed Codex executable to include real CLI/HUD terminal tests. These use a local mock Responses server, temporary homes and synthetic keys. Verify bidirectional handoff, history preservation, tool execution, daemon recovery and `exec resume` when changing those behaviors.

Run Bash syntax checks for `cs`, `cx` and `codex-cs`; keep Windows launcher parsing and platform-specific CI working. Preserve the MIT license.
