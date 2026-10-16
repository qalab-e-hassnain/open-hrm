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

# ---- is the file itself intact? -------------------------------------------
# Checked before restoring, because a damaged dump and a dump that restores
# badly need different responses — fetch it again, or fall back to an older
# backup — and pg_restore cannot tell you which it was.
if [[ -f "$DUMP.sha256" ]]; then
  echo "Verifying checksum"
  if command -v sha256sum >/dev/null 2>&1; then
    CHECK=(sha256sum -c --status)
  else
    CHECK=(shasum -a 256 -c -s)
  fi
  if (cd "$(dirname "$DUMP")" && "${CHECK[@]}" "$(basename "$DUMP").sha256"); then
    echo "  intact"
  else
    echo "  CHECKSUM MISMATCH — $DUMP is not the file that was backed up." >&2
    echo "  Do not restore it. Fetch it again from wherever it is stored;" >&2
    echo "  if the copy there is also damaged, use an older backup." >&2
    exit 1
  fi
else
  echo "NOTE: no $(basename "$DUMP").sha256 beside the dump, so this script" >&2
  echo "cannot tell a damaged file from a bad restore. Backups taken with" >&2
  echo "the current scripts/backup.sh have one." >&2
fi

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
#
# pg_restore's exit status is kept. The previous version of this line ended
# in `|| true`, which meant a dump that failed to restore half its tables
# reported success — and the only time anybody runs this for real is when the
# database is already gone, which is the worst possible moment to find out
# that "Restore looks sound" was measuring nothing.
#
# Warnings are filtered, errors are not, and the distinction matters: a
# restore into a fresh database always warns about roles it cannot find and
# about DROP statements for objects that are not there yet (that is what
# --clean --if-exists does on an empty target). Those are noise. Anything
# pg_restore calls an error is a table, constraint or row that did not come
# back.
#
# PIPESTATUS is used rather than `set -o pipefail` because the pipeline ends
# in grep, and grep exits 1 when it filters everything out — which is the
# normal, successful case here.
RESTORE_LOG="$(mktemp)"
trap '[[ -n "$CLEANUP" ]] && rm -f "$CLEANUP"; rm -f "$RESTORE_LOG"' EXIT

set +e
pg_restore --clean --if-exists --no-owner --no-privileges \
           --exit-on-error \
           --dbname "$TARGET" "$WORK" > "$RESTORE_LOG" 2>&1
RESTORE_STATUS=$?
set -e

grep -v "^pg_restore: warning" "$RESTORE_LOG" || true

ERRORS="$(grep -c "^pg_restore: error" "$RESTORE_LOG" || true)"
if [[ "$RESTORE_STATUS" -ne 0 || "$ERRORS" -gt 0 ]]; then
  echo >&2
  echo "RESTORE FAILED — pg_restore exited $RESTORE_STATUS with $ERRORS error(s)." >&2
  echo "The checks below are not run, because there is no point reporting row" >&2
  echo "counts from a database that did not finish restoring." >&2
  echo >&2
  echo "Full output: $RESTORE_LOG (kept until this script exits)" >&2
  grep "^pg_restore: error" "$RESTORE_LOG" | head -20 >&2
  exit 1
fi

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

# ---- does the restored schema match the code that will run on it? ---------
# "The restore worked" and "the application will start" are different
# questions. A dump from before a migration restores perfectly and then fails
# on the first request with a missing column, so the version is compared
# rather than merely counted.
RESTORED_VERSION="$(psql "$TARGET" -tAc \
  "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || true)"
EXPECTED_VERSION=""
if [[ -f alembic.ini ]]; then
  # `alembic heads` only reads the versions directory — it opens no
  # connection — but it still imports alembic/env.py, which constructs the
  # settings object and so insists on DATABASE_URL and SECRET_KEY being
  # present. They are supplied here rather than inherited: the URL is the
  # database we just restored into, and the key is a throwaway because
  # nothing in this code path signs anything. Without them the check is
  # skipped, and a silently skipped check is how a script comes to be
  # trusted for something it never did.
  #
  # Both the console script and `python -m alembic` are tried: a deployment
  # that installed requirements.txt without activating its virtualenv has the
  # module but not the entry point on PATH.
  for runner in "alembic" "python3 -m alembic" "python -m alembic"; do
    command -v "${runner%% *}" >/dev/null 2>&1 || continue
    EXPECTED_VERSION="$(
      DATABASE_URL="$TARGET" \
      SECRET_KEY="restore-script-reads-revisions-only-signs-nothing" \
      $runner heads 2>/dev/null | head -1 | awk '{print $1}' || true)"
    [[ -n "$EXPECTED_VERSION" ]] && break
  done
fi
if [[ -z "$EXPECTED_VERSION" ]]; then
  echo "  (could not read this checkout's expected migration version — run"
  echo "   this from the backend directory, with its dependencies installed,"
  echo "   to have the restored version checked against the code)"
fi

printf '  %-34s %s\n' "migration version" "${RESTORED_VERSION:-none}"
if [[ -n "$EXPECTED_VERSION" && -n "$RESTORED_VERSION" ]]; then
  if [[ "$RESTORED_VERSION" == "$EXPECTED_VERSION" ]]; then
    printf '  %-34s %s\n' "  matches this checkout" "yes"
  else
    printf '  %-34s %s\n' "  matches this checkout" "NO (code expects $EXPECTED_VERSION)"
    echo
    echo "The dump predates this checkout's migrations. The restore itself is"
    echo "fine, but the application will fail on a missing column until you"
    echo "run:  alembic upgrade head"
    echo "Do that against the restored database before pointing an API at it."
  fi
fi

# A table count alone does not notice that half the schema is missing, so
# check the ones the system cannot run without are all present.
MISSING=""
for t in users payroll_runs payroll_entries payrolls attendances leaves \
         employee_documents notifications shifts; do
  present="$(psql "$TARGET" -tAc \
    "SELECT count(*) FROM information_schema.tables \
     WHERE table_schema='public' AND table_name='$t'" 2>/dev/null || echo 0)"
  [[ "$present" -eq 1 ]] || MISSING="$MISSING $t"
done
if [[ -n "$MISSING" ]]; then
  printf '  %-34s %s\n' "core tables" "MISSING:$MISSING"
  FAILED=1
else
  printf '  %-34s %s\n' "core tables" "all present"
fi

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
