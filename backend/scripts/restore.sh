#!/usr/bin/env bash
#
# Restore a backup, and check that what came back is actually there.
#
#   ./scripts/restore.sh backups/hrm-20261016T090000Z.dump \
#       --into postgresql://postgres@localhost:5432/hrm_restore_test
#
# Run this on a schedule against a scratch database. A backup nobody has
# restored is not a backup, it is a file — and the moment you discover a dump
# is unreadable must not be the moment you need it.
#
# It refuses to restore over a database that already has data unless you pass
# --force, because the obvious mistake here is pointing it at production.

set -euo pipefail

DUMP="${1:-}"
TARGET=""
FORCE=0

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --into) TARGET="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$DUMP" || -z "$TARGET" ]]; then
  echo "Usage: $0 <dump file> --into <postgres url> [--force]" >&2
  exit 1
fi
[[ -f "$DUMP" ]] || { echo "No such dump: $DUMP" >&2; exit 1; }

# ---- decrypt if needed ----------------------------------------------------
WORK="$DUMP"
CLEANUP=""
if [[ "$DUMP" == *.gpg ]]; then
  : "${BACKUP_PASSPHRASE:?encrypted dump, but BACKUP_PASSPHRASE is not set}"
  WORK="$(mktemp)"
  CLEANUP="$WORK"
  gpg --batch --yes --quiet --decrypt --passphrase "$BACKUP_PASSPHRASE" \
      --output "$WORK" "$DUMP"
fi
trap '[[ -n "$CLEANUP" ]] && rm -f "$CLEANUP"' EXIT

# ---- refuse to flatten something that matters -----------------------------
EXISTING="$(psql "$TARGET" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo 0)"
if [[ "$EXISTING" -gt 0 && "$FORCE" -ne 1 ]]; then
  echo "The target already has $EXISTING table(s)." >&2
  echo "Restoring would overwrite them. Point this at a scratch database," >&2
  echo "or pass --force if you are certain." >&2
  exit 1
fi

echo "Restoring $DUMP"
pg_restore --clean --if-exists --no-owner --no-privileges \
           --dbname "$TARGET" "$WORK" 2>&1 | grep -v "^pg_restore: warning" || true

# ---- prove something actually came back -----------------------------------
echo
echo "Checking the restore"
FAILED=0
check() {
  local label="$1" sql="$2" expect="$3"
  local got
  got="$(psql "$TARGET" -tAc "$sql" 2>/dev/null || echo ERROR)"
  if [[ "$got" == "ERROR" ]]; then
    printf '  %-34s FAILED (query errored)\n' "$label"; FAILED=1
  elif [[ "$expect" == "nonzero" && "$got" -eq 0 ]]; then
    printf '  %-34s EMPTY (%s rows)\n' "$label" "$got"; FAILED=1
  else
    printf '  %-34s %s\n' "$label" "$got"
  fi
}

check "tables in public schema" \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" nonzero
check "employees" "SELECT count(*) FROM users" nonzero
check "  of which active" "SELECT count(*) FROM users WHERE is_active" any
check "attendance records" "SELECT count(*) FROM attendances" any
check "payroll records" "SELECT count(*) FROM payrolls" any
check "issued documents" "SELECT count(*) FROM employee_documents" any
check "migration version present" \
  "SELECT count(*) FROM alembic_version" nonzero

echo
echo "Documents point at files in object storage, which this dump does not"
echo "contain. Restore the matching files-*.tar.gz as well, or every"
echo "attachment in the restored system is a broken link."

if [[ "$FAILED" -ne 0 ]]; then
  echo
  echo "RESTORE INCOMPLETE — something above came back empty or errored." >&2
  exit 1
fi
echo
echo "Restore looks sound."
