#!/usr/bin/env bash
# flow bootstrap: install + configure + verify. Safe to re-run.
#   curl -fsSL https://raw.githubusercontent.com/Illuminfti/flow/main/install.sh | bash
set -euo pipefail

echo "🍃 installing flow..."
SRC="git+https://github.com/Illuminfti/flow"
if command -v uv >/dev/null 2>&1; then
  uv tool install "$SRC"
elif command -v pipx >/dev/null 2>&1; then
  pipx install "$SRC" || pipx install --force "$SRC"
else
  python3 -m pip install --user --upgrade "flow[yaml] @ $SRC"
fi

echo "⚙️  writing config..."
flow init || true

echo "✅ verifying (offline)..."
flow self-test --offline
echo
echo "flow ready. Set an API key (e.g. export OPENAI_API_KEY=...) then:"
echo "  flow self-test --online"
echo "  flow run --nl \"audit my repo across 3 lenses\""
