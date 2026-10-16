#!/usr/bin/env bash
#
# Back up a deployment: the database, and the files that are not in it.
#
#   ./scripts/backup.sh                      # into ./backups
#   BACKUP_DIR=/mnt/backups ./scripts/backup.sh
#   BACKUP_PASSPHRASE=... ./scripts/backup.sh # encrypted
#
# Two things matter here and both are easy to get wrong.
#
# The first is that a database dump is NOT a backup of this system. Profile
# pictures, CNIC scans, receipts and signed documents live in object storage,
# and the database holds only their names. Restore the database alone and
# every document in the system is a broken link. This script backs up both
# and says so if it cannot reach the storage.
#
# The second is that a backup nobody has restored is not a backup, it is a
# file. scripts/restore.sh exists so that restoring is a command you have
# run before rather than one you are reading for the first time during an
# incident. Run it against a scratch database on a schedule.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEEP_DAYS="${KEEP_DAYS:-30}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  if [[ -f .env ]]; then
    # shellcheck disable=SC2046
    export $(grep -E '^(DATABASE_URL|SUPABASE_URL|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_STORAGE_BUCKET)=' .env | xargs -0 2>/dev/null || true)
    DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- || true)"
  fi
fi
: "${DATABASE_URL:?DATABASE_URL is not set and no .env was found}"

mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/hrm-$STAMP.dump"

echo "Backing up the database"
# Custom format: compressed, and restorable table by table, which matters
# when you need one table back and not the whole thing.
pg_dump --format=custom --no-owner --no-privileges --file="$DUMP" "$DATABASE_URL"
echo "  $DUMP ($(du -h "$DUMP" | cut -f1))"

# ---- the files the database only holds names of ---------------------------
if [[ -n "${SUPABASE_URL:-}" && -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]]; then
  echo "Backing up uploaded files"
  FILES_DIR="$BACKUP_DIR/files-$STAMP"
  mkdir -p "$FILES_DIR"
  if python3 scripts/backup_files.py --out "$FILES_DIR"; then
    tar -czf "$FILES_DIR.tar.gz" -C "$(dirname "$FILES_DIR")" "$(basename "$FILES_DIR")"
    rm -rf "$FILES_DIR"
    echo "  $FILES_DIR.tar.gz ($(du -h "$FILES_DIR.tar.gz" | cut -f1))"
  else
    echo "  WARNING: could not back up files. The database dump alone will" >&2
    echo "  restore every document as a broken link." >&2
  fi
else
  echo "WARNING: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set." >&2
  echo "Uploaded documents are NOT in this backup. A restore from it will" >&2
  echo "bring back every record with its attachments missing." >&2
fi

# ---- encryption -----------------------------------------------------------
if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
  echo "Encrypting"
  for f in "$DUMP" "$BACKUP_DIR/files-$STAMP.tar.gz"; do
    [[ -f "$f" ]] || continue
    gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase "$BACKUP_PASSPHRASE" --output "$f.gpg" "$f"
    rm -f "$f"
    echo "  $f.gpg"
  done
else
  echo "NOTE: not encrypted. This dump contains every employee's salary," >&2
  echo "bank account and national ID number. Set BACKUP_PASSPHRASE, or make" >&2
  echo "sure whatever you copy it to is encrypted at rest." >&2
fi

# ---- retention ------------------------------------------------------------
find "$BACKUP_DIR" -maxdepth 1 -name 'hrm-*' -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name 'files-*' -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true

echo
echo "Done. Backups older than $KEEP_DAYS days removed."
echo "Now prove it works:  ./scripts/restore.sh $DUMP --into postgresql://.../scratch"
