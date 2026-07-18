#!/usr/bin/env bash
set -euo pipefail

REMOVE_BRIDGE=0
REMOVE_SKILL=0
HOST="all"
SCOPE="user"

usage() {
  cat <<'USAGE'
Usage: ./scripts/uninstall.sh [--bridge] [--skill [--host generic|kimi|all] [--scope user|project]]

Without flags, removes nothing. Selectively remove the bridge venv+launcher
and/or one or both installed Skills. By default --skill targets both user-level
discovery roots and both hosts.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bridge)
      REMOVE_BRIDGE=1
      shift
      ;;
    --skill)
      REMOVE_SKILL=1
      shift
      ;;
    --host)
      HOST="${2:?missing value for --host}"
      shift 2
      ;;
    --scope)
      SCOPE="${2:?missing value for --scope}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$HOST" in generic|kimi|all) ;; *) echo "Unsupported host: $HOST" >&2; exit 2 ;; esac
case "$SCOPE" in user|project) ;; *) echo "Unsupported scope: $SCOPE" >&2; exit 2 ;; esac

if [[ $REMOVE_BRIDGE -eq 0 && $REMOVE_SKILL -eq 0 ]]; then
  usage
  exit 2
fi

removed=()

if [[ "$REMOVE_BRIDGE" -eq 1 ]]; then
  DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
  BIN_HOME="${XDG_BIN_HOME:-$HOME/.local/bin}"
  rm -rf "$DATA_HOME/venice-media-skill"
  rm -f "$BIN_HOME/venice-media" "$BIN_HOME/venice-media-keychain"
  removed+=("bridge venv" "launcher" "Keychain launcher")
fi

remove_skill() {
  local destination="$1"
  if [[ -e "$destination" ]]; then
    rm -rf "$destination"
    removed+=("skill:$destination")
  fi
}

if [[ "$REMOVE_SKILL" -eq 1 ]]; then
  if [[ "$SCOPE" == "user" ]]; then
    if [[ "$HOST" == "generic" || "$HOST" == "all" ]]; then
      remove_skill "$HOME/.agents/skills/venice-media"
    fi
    if [[ "$HOST" == "kimi" || "$HOST" == "all" ]]; then
      remove_skill "${KIMI_CODE_HOME:-$HOME/.kimi-code}/skills/venice-media"
    fi
  else
    PROJECT_DIR="${PROJECT_DIR:-$PWD}"
    PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
    if [[ "$HOST" == "generic" || "$HOST" == "all" ]]; then
      remove_skill "$PROJECT_DIR/.agents/skills/venice-media"
    fi
    if [[ "$HOST" == "kimi" || "$HOST" == "all" ]]; then
      remove_skill "$PROJECT_DIR/.kimi-code/skills/venice-media"
    fi
  fi
fi

echo "Removed: ${removed[*]:-nothing}"
echo "Runtime cache/state/output directories were not deleted automatically."
