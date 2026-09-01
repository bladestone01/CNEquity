#!/usr/bin/env bash
# B1 — Install (or reinstall) the launchd agent that runs the daily pipeline.
# Generates a plist from the template with this repo's absolute path, drops it
# in ~/Library/LaunchAgents, and loads it. Idempotent: re-run to update.
#
# Usage: scripts/install_scheduler.sh
# Uninstall: scripts/uninstall_scheduler.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.cnequity.daily"
TEMPLATE="$REPO_ROOT/scripts/launchd/$LABEL.plist.template"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST="$DEST_DIR/$LABEL.plist"
SOURCE_VANTAGE="${CNE_SOURCE_VANTAGE:-local}"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "install_scheduler: launchd is macOS-only. On Linux use cron:" >&2
  echo "  15 11 * * *  $REPO_ROOT/scripts/daily_pipeline.sh" >&2
  exit 1
fi
if [[ ! -x "$REPO_ROOT/.venv/bin/cne" ]]; then
  echo "install_scheduler: $REPO_ROOT/.venv/bin/cne not found — create the venv first." >&2
  exit 1
fi
if [[ ! "$SOURCE_VANTAGE" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "install_scheduler: CNE_SOURCE_VANTAGE must match [A-Za-z0-9._-]+" >&2
  exit 1
fi

mkdir -p "$DEST_DIR" "$REPO_ROOT/data/cnequity/logs"
sed \
  -e "s#__REPO_ROOT__#$REPO_ROOT#g" \
  -e "s#__SOURCE_VANTAGE__#$SOURCE_VANTAGE#g" \
  "$TEMPLATE" >"$DEST"

# Reload if already present.
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "install_scheduler: loaded $LABEL"
echo "  plist:    $DEST"
echo "  schedule: daily 11:15 host-local time (edit the plist template to move it)"
echo "  source vantage: $SOURCE_VANTAGE"
echo "  logs:     $REPO_ROOT/data/cnequity/logs/"
echo "  verify:   launchctl list | grep cnequity"
echo "  test now: launchctl start $LABEL"
