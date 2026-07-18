#!/usr/bin/env bash
set -euo pipefail

HOST="generic"
SCOPE="user"
PROJECT_DIR=""

usage() {
  cat <<'USAGE'
Usage: ./scripts/install.sh [--host generic|kimi|all] [--scope user|project] [--project-dir PATH]

Installs an isolated Python bridge and copies the Agent Skill. No API key is stored.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:?missing value for --host}"
      shift 2
      ;;
    --scope)
      SCOPE="${2:?missing value for --scope}"
      shift 2
      ;;
    --project-dir)
      PROJECT_DIR="${2:?missing value for --project-dir}"
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

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.11+ is required." >&2
  exit 1
fi
"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"Python 3.11+ is required; found {sys.version.split()[0]}")
PY

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="${XDG_BIN_HOME:-$HOME/.local/bin}"
INSTALL_ROOT="$DATA_HOME/venice-media-skill"
VENV="$INSTALL_ROOT/venv"
mkdir -p "$INSTALL_ROOT" "$BIN_HOME"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install --upgrade "$ROOT"

cat > "$BIN_HOME/venice-media" <<LAUNCHER
#!/usr/bin/env bash
exec "$VENV/bin/venice-media" "\$@"
LAUNCHER
chmod 0755 "$BIN_HOME/venice-media"

copy_skill() {
  local destination="$1"
  mkdir -p "$(dirname "$destination")"
  local staging
  staging="$(mktemp -d "$(dirname "$destination")/.venice-media.XXXXXX")"
  cp -R "$ROOT/skills/venice-media/." "$staging/"
  test -f "$staging/SKILL.md"
  local backup="${destination}.rollback"
  rm -rf "$backup"
  if [[ -e "$destination" ]]; then mv "$destination" "$backup"; fi
  if mv "$staging" "$destination"; then
    rm -rf "$backup"
  else
    if [[ -e "$backup" ]]; then mv "$backup" "$destination"; fi
    exit 1
  fi
}

if [[ "$SCOPE" == "user" ]]; then
  if [[ "$HOST" == "generic" || "$HOST" == "all" ]]; then copy_skill "$HOME/.agents/skills/venice-media"; fi
  if [[ "$HOST" == "kimi" || "$HOST" == "all" ]]; then
    copy_skill "${KIMI_CODE_HOME:-$HOME/.kimi-code}/skills/venice-media"
  fi
else
  if [[ -z "$PROJECT_DIR" ]]; then
    PROJECT_DIR="$PWD"
  fi
  PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
  if [[ "$HOST" == "generic" || "$HOST" == "all" ]]; then copy_skill "$PROJECT_DIR/.agents/skills/venice-media"; fi
  if [[ "$HOST" == "kimi" || "$HOST" == "all" ]]; then
    copy_skill "$PROJECT_DIR/.kimi-code/skills/venice-media"
  fi
fi

cat <<EOF2
Installed Venice Media Skill.

Executable: $BIN_HOME/venice-media
Bridge venv: $VENV

Ensure this directory is on PATH:
  $BIN_HOME

Then export VENICE_API_KEY in the shell that launches your AI CLI and run:
  venice-media doctor --online
EOF2
