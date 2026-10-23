#!/usr/bin/env bash
# `cs` — unified Codex account switch (official default + sub2api proxy).
# Self-contained: finds a Python 3.11+ from the conda envs, falling back to
# system python3/python, so it works regardless of the shell's active env.
set -euo pipefail

# Resolve symlinks so SRC_DIR points at the real checkout, not ~/.local/bin.
resolve() { local p="$1" l; while [[ -L "$p" ]]; do l="$(readlink "$p")"; [[ "$l" == /* ]] && p="$l" || p="$(dirname "$p")/$l"; done; echo "$(cd "$(dirname "$p")" && pwd -P)/$(basename "$p")"; }
SELF="$(resolve "${BASH_SOURCE[0]}")"
SRC_DIR="$(dirname "$SELF")"
SCRIPT="$SRC_DIR/cs_accounts.py"

[[ -f "$SCRIPT" ]] || { echo 'Missing cs_accounts.py; reinstall from the complete checkout.' >&2; exit 1; }

# Prefer tan-tools (3.11+), then any conda env with 3.11+, then system python3/python.
candidates=()
if [[ -x "$HOME/miniconda3/envs/tan-tools/bin/python3" ]]; then
  candidates+=("$HOME/miniconda3/envs/tan-tools/bin/python3")
fi
for py in "$HOME"/miniconda3/envs/*/bin/python3; do
  [[ "$py" == "$HOME/miniconda3/envs/tan-tools/bin/python3" ]] && continue
  candidates+=("$py")
done
for py in python3 python; do
  if command -v "$py" >/dev/null; then
    candidates+=("$py")
  fi
done

for py in "${candidates[@]}"; do
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    exec "$py" "$SCRIPT" "$@"
  fi
done

echo 'cs requires Python 3.11+. Activate a conda env (e.g. tan-tools) or install Python 3.11+.' >&2
exit 1
