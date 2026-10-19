#!/bin/sh
# One-time switch of the server's Obsidian Sync copy to two-way, so captured notes reach your devices.
# Run it in a shell on the server, with the sync turned off in Railway (KNOWLEDGE_SYNC_ENABLED=false):
#   sh deploy/ob-two-way.sh
#
# Why a fresh copy: while download-only, the cleanup deleted things from the server's copy (loose notes at the
# vault root, folders before they were skipped). The sync tool may remember those deletions, and in two-way mode it
# could send them to the real vault. So the current copy is moved aside (kept as a backup, nothing deleted) and a
# new, empty copy is connected with the skip list and "notes only" set before anything downloads. It downloads
# everything while still download-only, is checked against the old copy, and only then is switched to two-way.
# The first two-way sync must report no changes; otherwise the copy goes straight back to download-only.
# The vault password is typed at the prompt; it is never passed as an argument or stored in this repo.
set -eu

VAULT="${KNOWLEDGE_SOURCE_ROOT:-/data/vault}"
export HOME="${KNOWLEDGE_HOME:-/data/home}"
SYNC_DIR="$HOME/.config/obsidian-headless/sync"
EXTRA="${KNOWLEDGE_EXCLUDED_FILE:-/data/excluded-folders-extra.txt}"
FOLDER="${KNOWLEDGE_INCLUDED_FOLDERS:-Knowledge}"
FINISH=false
[ "${1:-}" = "finish" ] && FINISH=true
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="${VAULT}-pullonly-$STAMP"
CONFIG_BACKUP="$HOME/.config/obsidian-headless/sync-pullonly-$STAMP"

as_agent() { if [ "$(id -u)" = "0" ]; then runuser -u agent -- env HOME="$HOME" "$@"; else "$@"; fi; }
stop() { echo; echo "STOPPED: $1" >&2; exit 1; }
mode() { as_agent ob sync-config --path "$VAULT" | sed -n 's/^ *Sync mode: *//p' | head -1; }
setting() { python -c "import json,glob,sys; print(json.load(open(glob.glob(sys.argv[1] + '/*/config.json')[0])).get(sys.argv[2]))" "$SYNC_DIR" "$1"; }
fingerprint() { (cd "$1/$FOLDER" && find . -type f -name '*.md' -exec sha256sum {} + | sort); }

if [ "$FINISH" = "true" ]; then
  # Resume after a first run stopped before step 5: check the new copy against the backup again, then steps 5 and 6.
  echo "Finishing: checks"
  [ "${KNOWLEDGE_SYNC_ENABLED:-false}" = "true" ] && stop "the sync is still on. Set KNOWLEDGE_SYNC_ENABLED=false in Railway first."
  BACKUP="$(ls -d "${VAULT}"-pullonly-* 2>/dev/null | tail -1)"
  [ -n "$BACKUP" ] || stop "no backup copy ${VAULT}-pullonly-* found; run the full script instead."
  [ "$(mode)" = "pull-only" ] || [ "$(mode)" = "bidirectional" ] || stop "the sync tool's mode is unclear ($(mode))."
  [ "$(setting allowTypes)" = "[]" ] || stop "the new copy is not set to notes only."
  as_agent ob sync-config --path "$VAULT" --mode pull-only
  as_agent ob sync --path "$VAULT"
  fingerprint "$BACKUP" > /tmp/old-notes.txt
  fingerprint "$VAULT" > /tmp/new-notes.txt
  cmp -s /tmp/old-notes.txt /tmp/new-notes.txt || stop "the new copy's $FOLDER notes differ from the backup. Nothing was switched."
  echo "The new copy matches the backup: $(wc -l < /tmp/new-notes.txt) notes in $FOLDER, same content."
fi
if [ "$FINISH" = "false" ]; then
echo "Step 1 of 6: checks"
[ "${KNOWLEDGE_SYNC_ENABLED:-false}" = "true" ] && stop "the sync is still on. Set KNOWLEDGE_SYNC_ENABLED=false in Railway, wait for the redeploy, then run this again."
running() { for f in /proc/[0-9]*/cmdline; do tr -c '[:print:]' ' ' < "$f" 2>/dev/null; echo; done | grep -Eq "[o]b sync|[s]ync-loop"; }
running && stop "a sync is still running on the server. Check KNOWLEDGE_SYNC_ENABLED=false took effect (redeploy), then run this again."
[ -d "$VAULT/$FOLDER" ] || stop "$VAULT/$FOLDER is missing, so there is nothing to compare the new copy with."
[ -s "$EXTRA" ] || stop "the skip list $EXTRA is missing or empty."
[ "$(ls -d "$SYNC_DIR"/*/ 2>/dev/null | wc -l)" = "1" ] || stop "expected exactly one sync setting folder in $SYNC_DIR."
VAULT_NAME="$(setting vaultName)"
OLD_NOTES="$(fingerprint "$VAULT" | wc -l)"
echo "Vault: $VAULT_NAME. Notes in $FOLDER now: $OLD_NOTES."

echo
echo "Step 2 of 6: keep the current copy as a backup (nothing is deleted)"
mv "$VAULT" "$BACKUP"
mv "$(ls -d "$SYNC_DIR"/*/)" "$CONFIG_BACKUP"
mkdir -p "$VAULT"
chown agent:agent "$VAULT" 2>/dev/null || true
echo "Old copy: $BACKUP"
echo "Old sync settings: $CONFIG_BACKUP"
echo "To undo before step 5: remove $VAULT and the new folder in $SYNC_DIR, then move both back."

echo
echo "Step 3 of 6: connect a new, empty copy (type the vault encryption password if it asks)"
as_agent ob sync-setup --vault "$VAULT_NAME" --path "$VAULT" --device-name kb-server
EXCLUDED="$(paste -sd, "$EXTRA")"
as_agent ob sync-config --path "$VAULT" --mode pull-only --conflict-strategy conflict --excluded-folders "$EXCLUDED"
as_agent python deploy/notes_only.py "$SYNC_DIR"
[ "$(mode)" = "pull-only" ] || stop "the new copy is not download-only (the tool says: $(mode))."
[ "$(setting allowTypes)" = "[]" ] || stop "the new copy is not set to notes only."
echo "Settings checked: download-only, notes only, $(paste -sd, "$EXTRA" | tr ',' '\n' | wc -l) folders skipped."

echo
echo "Step 4 of 6: first download, still download-only"
as_agent ob sync --path "$VAULT"
fingerprint "$BACKUP" > /tmp/old-notes.txt
fingerprint "$VAULT" > /tmp/new-notes.txt
if ! cmp -s /tmp/old-notes.txt /tmp/new-notes.txt; then
  echo "Differences (path and content hash):"
  diff /tmp/old-notes.txt /tmp/new-notes.txt | head -20
  stop "the new copy's $FOLDER notes differ from the old copy. Nothing was switched to two-way. If a note was just edited on a device, run 'ob sync --path $VAULT' once and compare again; otherwise undo as in step 2."
fi
echo "The new copy matches the old one: $(wc -l < /tmp/new-notes.txt) notes in $FOLDER, same content."

echo
fi
echo
echo "Step 5 of 6: switch the new copy to two-way"
as_agent ob sync-config --path "$VAULT" --mode bidirectional
[ "$(mode)" = "bidirectional" ] || stop "the switch did not take (the tool says: $(mode)). The copy is still download-only."

echo
echo "Step 6 of 6: first two-way sync (it must change nothing)"
STATUS=0
OUTPUT="$(as_agent ob sync --path "$VAULT" 2>&1)" || STATUS=$?
echo "$OUTPUT"
# Only what the tool prints after "Detecting changes" counts: a clean sync prints nothing there but "Fully synced"
# and "Disconnected". The header above it (settings, skipped folder names) is not a change.
AFTER="$(echo "$OUTPUT" | sed -n '/Detecting changes/,$p' | sed 1d | grep -v '^ *$' | grep -Ev '^(Fully synced|Disconnected from server)$' || true)"
if [ "$STATUS" -ne 0 ] || ! echo "$OUTPUT" | grep -q "^Fully synced" || [ -n "$AFTER" ]; then
  as_agent ob sync-config --path "$VAULT" --mode pull-only
  stop "the first two-way sync failed or reported changes (listed above). The copy is back to download-only; tell Claude what it printed."
fi

echo
echo "Done. The server's copy is two-way and changed nothing in your vault."
echo "Now in Railway set KNOWLEDGE_SYNC_MODE=bidirectional and KNOWLEDGE_SYNC_ENABLED=true (one redeploy)."
echo "Keep $BACKUP until a captured note has reached your devices; then it can be removed."
