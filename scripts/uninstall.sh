#!/usr/bin/env bash
set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="${XDG_BIN_HOME:-$HOME/.local/bin}"
rm -rf "$DATA_HOME/venice-media-skill"
rm -f "$BIN_HOME/venice-media"
rm -rf "$HOME/.agents/skills/venice-media"
rm -rf "${KIMI_CODE_HOME:-$HOME/.kimi-code}/skills/venice-media"

echo "Removed the bridge, launcher, and user-level skills."
echo "Runtime cache/state/output directories were not deleted automatically."
