#!/usr/bin/env bash
# flowleaf bootstrap: install + configure + verify. Safe to re-run.
#   curl -fsSL https://raw.githubusercontent.com/Illuminfti/flowleaf/main/install.sh | bash
set -euo pipefail

echo "🍃 installing flowleaf..."
if command -v uv >/dev/null 2>&1; then
  uv tool install flowleaf || uv tool upgrade flowleaf
elif command -v pipx >/dev/null 2>&1; then
  pipx install flowleaf || pipx upgrade flowleaf
else
  python3 -m pip install --user --upgrade "flowleaf[yaml]"
fi

echo "⚙️  writing config..."
flowleaf init || true

echo "✅ verifying (offline)..."
flowleaf self-test --offline
echo
echo "flowleaf ready. Set an API key (e.g. export OPENAI_API_KEY=...) then:"
echo "  flowleaf self-test --online"
echo "  flowleaf run --nl \"audit my repo across 3 lenses\""
