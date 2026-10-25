#!/usr/bin/env bash
# Create .env from .env.example with fresh random secrets. Never overwrites an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists; leaving it alone."
  exit 0
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
while IFS= read -r line; do
  while [[ "$line" == *__GENERATE__* ]]; do
    line="${line/__GENERATE__/$(openssl rand -hex 24)}"
  done
  printf '%s\n' "$line"
done < .env.example > "$tmp"
install -m 600 "$tmp" .env
echo "Wrote .env (mode 600). Set REMOTE_LLM_API_KEY in it before using the VPN model."
