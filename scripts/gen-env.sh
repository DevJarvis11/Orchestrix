#!/usr/bin/env bash
# Create .env from .env.example with fresh random secrets. Never overwrites a value: when .env
# already exists, only keys it lacks are appended (with fresh secrets), so new milestones can
# add settings without touching the user's keys.
#   __GENERATE__   -> 48 hex chars      __GENERATE64__ -> 64 hex chars (e.g. AES-256 keys)
set -euo pipefail
cd "$(dirname "$0")/.."

render() {
  local line="$1"
  while [[ "$line" == *__GENERATE64__* ]]; do
    line="${line/__GENERATE64__/$(openssl rand -hex 32)}"
  done
  while [[ "$line" == *__GENERATE__* ]]; do
    line="${line/__GENERATE__/$(openssl rand -hex 24)}"
  done
  printf '%s\n' "$line"
}

if [[ ! -f .env ]]; then
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  while IFS= read -r line; do render "$line"; done < .env.example > "$tmp"
  install -m 600 "$tmp" .env
  echo "Wrote .env (mode 600). Set REMOTE_LLM_API_KEY in it before using the VPN model."
  exit 0
fi

added=0
while IFS= read -r line; do
  [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]] || continue
  key="${BASH_REMATCH[1]}"
  if ! grep -q "^${key}=" .env; then
    [[ $added -eq 0 ]] && printf '\n# --- added by make env %s ---\n' "$(date -I)" >> .env
    render "$line" >> .env
    added=$((added + 1))
  fi
done < .env.example
if [[ $added -gt 0 ]]; then echo "Appended $added new key(s) to .env."; else echo ".env is up to date."; fi
