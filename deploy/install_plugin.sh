#!/usr/bin/env bash
# Installs this checkout as the nutribot plugin of a Hermes profile (what `hermes plugins install` does from GitHub).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${HERMES_PROFILE_DIR:-$HOME/.hermes/profiles/nutrition}"
DEST="$PROFILE/plugins/nutrition-log"
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ "${1:-}" = "--dry-run" ]; then
  echo "would install into $DEST (backup: $PROFILE/backups/nutrition-log.$STAMP)"
  exit 0
fi
# Backups live outside plugins/ so Hermes never loads them as a plugin.
mkdir -p "$PROFILE/backups"
[ -d "$DEST" ] && cp -R "$DEST" "$PROFILE/backups/nutrition-log.$STAMP"
rsync -a --delete --exclude __pycache__ --exclude .pytest_cache --exclude .git \
  --exclude tests --exclude local --exclude secrets --exclude migration \
  "$ROOT/" "$DEST/"
echo "installed; backup: $PROFILE/backups/nutrition-log.$STAMP"
