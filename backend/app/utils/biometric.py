"""
Pulling badge records off the office fingerprint device.

The device is a small Flask service exposing CSV exports. It is read-only from
our side and treated as a source of *presence*, not of hours: nearly half its
records show one scan for the day because people badge in and not out, so a
duration derived from it would be wrong more often than right.
"""
import csv
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.biometric import BiometricIdentity, BiometricScan

logger = logging.getLogger(__name__)

# The device reports local Pakistan time with no offset marker.
DEVICE_UTC_OFFSET = timedelta(hours=5)


def _to_utc(day: date, clock: str) -> Optional[datetime]:
    try:
        t = datetime.strptime(clock.strip(), "%H:%M:%S").time()
    except (ValueError, AttributeError):
        return None
    return datetime.combine(day, t) - DEVICE_UTC_OFFSET


def fetch_month(month: str) -> str:
    """
    Raw CSV for one month, "YYYY-MM".

    The monthly export is a few hundred bytes against ~32KB for the year, which
    is what makes polling every couple of minutes reasonable rather than rude.
    """
    url = f"{settings.BIOMETRIC_BASE_URL.rstrip('/')}/exportPDFMonth"
    params = {"mac": settings.BIOMETRIC_DEVICE_MAC, "date": month}
    with httpx.Client(timeout=settings.BIOMETRIC_TIMEOUT_SECONDS) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.text


def parse(csv_text: str) -> list:
    """Device rows, cleaned. Enrolment names carry stray padding."""
    out = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        name = (row.get("name") or "").strip()
        day = (row.get("date") or "").strip()
        if not name or not day:
            continue
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        first = _to_utc(d, row.get("InTime", ""))
        last = _to_utc(d, row.get("OutTime", ""))
        if first is None:
            continue
        out.append({"device_name": name, "scan_date": d,
                    "first_scan": first, "last_scan": last or first})
    return out


# Labels the device emits that are not people: the door-release button, the
# scheduled restart, and the halt toggles. Matched on the normalised label, so
# "Halt ON with Button (Fingerprint)" is caught the same as "Halt On".
SYSTEM_LABELS = {
    "button", "admin", "halt on", "halt off",
    "halt on with button", "halt off with button", "restarting 3 am",
}


def normalise_label(label: str) -> str:
    """
    The person's enrolment name, without the method the device appends.

    "Kamran (Fingerprint)" and "Kamran" are the same enrolment. Three identities
    were mapped before this endpoint existed and carry the suffix in the stored
    name, so matching has to go through here on both sides or they silently stop
    matching.
    """
    return re.sub(r"\s*\(.*?\)\s*$", "", (label or "")).strip()


def fetch_logs() -> list:
    """
    Every scan event the device holds, newest history included.

    One request returns the lot — about 11,000 events and 1.2 MB at present.
    There is no since-parameter to narrow it, and the log ids are not ordered,
    so the whole set is fetched and the storage below is written to be
    idempotent instead.
    """
    url = f"{settings.BIOMETRIC_LOGS_URL.rstrip('/')}/getLogsByMac"
    params = {"mac": settings.BIOMETRIC_DEVICE_MAC}
    with httpx.Client(timeout=max(60.0, settings.BIOMETRIC_TIMEOUT_SECONDS)) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()


def parse_logs(events: list, since: Optional[date] = None) -> list:
    """
    Raw events to one row per person per day: first scan, last scan, how many.

    Days are local days. A punch at half past midnight belongs to the night it
    happened on, and grouping by the UTC date would move every scan before 05:00
    into the previous day — for a Pakistani office that is most of nobody, but
    it is exactly the kind of quiet off-by-one that makes an attendance report
    untrustworthy.
    """
    buckets: dict = {}
    for event in events:
        if not isinstance(event, (list, tuple)) or len(event) < 4:
            continue
        _log_id, _mac, label, stamp = event[0], event[1], event[2], event[3]
        name = normalise_label(label)
        if not name or name.lower() in SYSTEM_LABELS:
            continue
        try:
            # The device sends an explicit offset, so this is exact rather than
            # assumed — unlike the CSV export, which sends none.
            moment = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone(DEVICE_UTC_OFFSET))
        local_day = moment.astimezone(timezone(DEVICE_UTC_OFFSET)).date()
        if since and local_day < since:
            continue
        utc = moment.astimezone(timezone.utc).replace(tzinfo=None)

        key = (name, local_day)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {"device_name": name, "scan_date": local_day,
                            "first_scan": utc, "last_scan": utc, "scans": 1}
        else:
            bucket["first_scan"] = min(bucket["first_scan"], utc)
            bucket["last_scan"] = max(bucket["last_scan"], utc)
            bucket["scans"] += 1
    return sorted(buckets.values(), key=lambda r: (r["scan_date"], r["device_name"]))


# How far back a scheduled run looks. The device holds eighteen months and
# almost none of it changes, so re-reading the lot every four hours is work for
# nothing. Pass since=None explicitly to take the whole history.
SCHEDULED_WINDOW_DAYS = 45


def sync_logs(db: Session, since: Optional[date] = None,
              full: bool = False) -> dict:
    """
    Pull the event log and store a day per person.

    Replaces the monthly CSV export as the scheduled source: the events carry
    their own timezone, they cover the device's whole history rather than one
    month, and a day with three punches is distinguishable from a day with one,
    which the aggregated export could not express.
    """
    try:
        events = fetch_logs()
    except Exception as exc:
        logger.exception("Biometric log sync failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                "events": 0, "days": 0, "matched": 0, "unmapped": []}

    if since is None and not full:
        since = date.today() - timedelta(days=SCHEDULED_WINDOW_DAYS)
    rows = parse_logs(events, since=since)
    # Identities are matched on the normalised name so the three mapped with a
    # "(Fingerprint)" suffix keep working against labels that no longer carry one.
    identities = {}
    for ident in db.query(BiometricIdentity).all():
        identities.setdefault(normalise_label(ident.device_name).lower(), ident)

    # Every existing row for the range, in one query. Looking each one up
    # individually meant 2,500 round trips to a pooled remote database for a
    # job that runs every four hours on a tier with an hour of CPU a day.
    existing = {}
    for scan in db.query(BiometricScan).filter(
            BiometricScan.scan_date >= rows[0]["scan_date"]).all() if rows else []:
        existing[(scan.device_name, scan.scan_date)] = scan

    stored = updated = matched = changed = 0
    unmapped: dict = {}
    now = datetime.utcnow()

    for row in rows:
        name = row["device_name"]
        ident = identities.get(name.lower())
        if ident is None:
            ident = BiometricIdentity(device_name=name, scan_count=0)
            db.add(ident)
            db.flush()
            identities[name.lower()] = ident
        if ident.employee_id is None and not ident.ignored:
            unmapped[name] = unmapped.get(name, 0) + row["scans"]

        ident.first_seen = min(ident.first_seen or row["scan_date"], row["scan_date"])
        ident.last_seen = max(ident.last_seen or row["scan_date"], row["scan_date"])

        scan = existing.get((ident.device_name, row["scan_date"]))
        if scan is None:
            scan = BiometricScan(device_name=ident.device_name,
                                 scan_date=row["scan_date"])
            db.add(scan)
            existing[(ident.device_name, row["scan_date"])] = scan
            stored += 1
        else:
            updated += 1
        # One punch all day means the person badged in and never out, so the
        # duration is unknown rather than zero.
        fresh = (ident.employee_id, row["first_scan"], row["last_scan"],
                 row["scans"] == 1)
        current = (scan.employee_id, scan.first_scan, scan.last_scan, scan.single_scan)
        if fresh != current:
            (scan.employee_id, scan.first_scan,
             scan.last_scan, scan.single_scan) = fresh
            # Only stamped when something actually moved. Touching it on every
            # pass would mark all 2,500 rows dirty and emit an UPDATE apiece,
            # four times a day, for a device whose history stopped changing
            # months ago.
            scan.synced_at = now
            changed += 1
        if ident.employee_id:
            matched += 1

    db.flush()
    # One grouped count rather than one per identity.
    tallies = dict(db.query(BiometricScan.device_name,
                            func.count(BiometricScan.id))
                   .group_by(BiometricScan.device_name).all())
    for ident in identities.values():
        ident.scan_count = tallies.get(ident.device_name, 0)
    db.commit()

    return {"ok": True, "events": len(events), "days": len(rows),
            "new": stored, "unchanged": updated - changed, "changed": changed,
            "matched": matched,
            "unmapped": sorted(unmapped.items(), key=lambda kv: -kv[1])[:20],
            "covering": (f"{rows[0]['scan_date']} .. {rows[-1]['scan_date']}"
                         if rows else None)}


def sync_month(db: Session, month: Optional[str] = None) -> dict:
    """
    Pull one month and store it.

    Upserts on (device_name, date) because the device is polled repeatedly and
    re-reports the same days; an insert-only sync would multiply every record by
    the number of polls.

    Unmapped names are recorded as identities with no employee so they surface
    for a decision, rather than being silently discarded — a new joiner enrolled
    on the device is exactly this case, and it should be visible.
    """
    month = month or datetime.utcnow().strftime("%Y-%m")
    try:
        rows = parse(fetch_month(month))
    except Exception as exc:
        logger.exception("Biometric sync failed for %s", month)
        return {"month": month, "ok": False, "error": str(exc)[:200],
                "scanned": 0, "matched": 0, "unmapped": []}

    identities = {i.device_name: i for i in db.query(BiometricIdentity).all()}
    stored = matched = 0
    unmapped = set()

    for row in rows:
        name = row["device_name"]
        ident = identities.get(name)
        if ident is None:
            ident = BiometricIdentity(device_name=name, scan_count=0)
            db.add(ident)
            db.flush()
            identities[name] = ident
        if ident.employee_id is None and not ident.ignored:
            unmapped.add(name)

        # Keep the identity's activity window current — it is what makes an
        # unmapped name reviewable ("28 scans, last seen August").
        ident.first_seen = min(ident.first_seen or row["scan_date"], row["scan_date"])
        ident.last_seen = max(ident.last_seen or row["scan_date"], row["scan_date"])

        scan = db.query(BiometricScan).filter(
            BiometricScan.device_name == name,
            BiometricScan.scan_date == row["scan_date"],
        ).first()
        if scan is None:
            scan = BiometricScan(device_name=name, scan_date=row["scan_date"])
            db.add(scan)
            stored += 1
        scan.employee_id = ident.employee_id
        scan.first_scan = row["first_scan"]
        scan.last_scan = row["last_scan"]
        scan.single_scan = row["first_scan"] == row["last_scan"]
        scan.synced_at = datetime.utcnow()
        if ident.employee_id:
            matched += 1

    for name, ident in identities.items():
        ident.scan_count = db.query(BiometricScan).filter(
            BiometricScan.device_name == name).count()
    db.commit()

    return {"month": month, "ok": True, "rows": len(rows), "new": stored,
            "matched": matched, "unmapped": sorted(unmapped)}


def promote_scans_to_attendance(db: Session, day: date) -> dict:
    """
    Turn a day's badge records into portal attendance, where there is none.

    The device and the portal have been two separate records of the same fact,
    and only one of them is what the dashboard, the hours and the payroll read.
    Somebody who badges in at the door and never opens the portal simply did not
    exist as far as their attendance was concerned.

    Gap-filling only, deliberately. A portal session already on the day is left
    exactly as it is — the person was there and said so, and a second session
    raised beside it would double-count the day. Nothing here ever edits or
    replaces what somebody entered themselves.

    A single punch leaves the session open rather than guessing a departure, so
    the standard auto-checkout closes it at the employee's own hours — the same
    treatment as a portal check-in somebody forgot to close, because that is
    the same situation.
    """
    from app.models.attendance import Attendance
    from app.models.user import User
    from app.utils.attendance_session import close_expired_sessions

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    scans = (db.query(BiometricScan)
             .filter(BiometricScan.scan_date == day,
                     BiometricScan.employee_id.isnot(None)).all())
    if not scans:
        return {"day": str(day), "created": 0, "skipped": 0, "employees": []}

    already = {a.employee_id for a in db.query(Attendance).filter(
        Attendance.check_in_time >= start, Attendance.check_in_time < end).all()}

    created, skipped, names = 0, 0, []
    for scan in scans:
        employee = db.query(User).filter(User.id == scan.employee_id).first()
        if not employee or not employee.is_active:
            continue
        # Nobody expects these two on a device in the office.
        if getattr(employee, "attendance_exempt", False) or \
                getattr(employee, "works_remotely", False):
            continue
        if employee.id in already:
            skipped += 1
            continue

        session = Attendance(
            employee_id=employee.id,
            check_in_time=scan.first_scan,
            check_out_time=None if scan.single_scan else scan.last_scan,
            source="device",
            notes=f"Raised from the office fingerprint device ({scan.device_name}).",
        )
        if session.check_out_time:
            session.duration_hours = round(
                (session.check_out_time - session.check_in_time).total_seconds() / 3600, 2)
        db.add(session)
        already.add(employee.id)
        created += 1
        names.append(employee.full_name)

    db.commit()
    # An open session raised for a past day meets the same cap a forgotten
    # portal check-in would.
    close_expired_sessions(db)
    return {"day": str(day), "created": created, "skipped": skipped,
            "employees": names}


def promote_recent(db: Session, days: int = 2) -> dict:
    """
    Fill the gap for today and the day before, and no further back.

    Deliberately short. Attendance feeds hours, overtime and ultimately pay, so
    quietly manufacturing months of it from a device log is not something a
    scheduled sweep should do on its own — a backfill over a longer range is
    available as a decision somebody makes.
    """
    out = []
    for i in range(max(1, days)):
        out.append(promote_scans_to_attendance(db, date.today() - timedelta(days=i)))
    return {"days": out,
            "created": sum(d["created"] for d in out),
            "skipped": sum(d["skipped"] for d in out)}


def reconcile_day(db: Session, day: date) -> list:
    """
    Compare the device against portal attendance for one day.

    Reports disagreements only. Three kinds matter, and they mean different
    things: badged but never checked in (forgot the portal), checked in but
    never badged (was not in the office), and badged once (did not badge out,
    so the device cannot corroborate the hours).

    People exempt from attendance, and remote workers, are excluded — neither
    is expected to appear on a device in the office.
    """
    from app.models.attendance import Attendance
    from app.models.user import User

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    scans = {s.employee_id: s for s in db.query(BiometricScan).filter(
        BiometricScan.scan_date == day, BiometricScan.employee_id.isnot(None)).all()}
    portal = {}
    for a in db.query(Attendance).filter(
            Attendance.check_in_time >= start, Attendance.check_in_time < end).all():
        portal.setdefault(a.employee_id, []).append(a)

    issues = []
    staff = db.query(User).filter(
        User.is_active == True,          # noqa: E712
        User.attendance_exempt == False,  # noqa: E712
    ).all()
    for u in staff:
        badged = u.id in scans
        clocked = u.id in portal
        if u.works_remotely:
            # No badge is expected; only a missing portal check-in is notable.
            if not clocked:
                issues.append({"employee_id": u.id, "employee_name": u.full_name,
                               "issue": "remote_no_checkin",
                               "detail": "Remote worker with no portal check-in."})
            continue
        if badged and not clocked:
            s = scans[u.id]
            issues.append({"employee_id": u.id, "employee_name": u.full_name,
                           "issue": "badged_not_checked_in",
                           "detail": f"Badged at {(s.first_scan + DEVICE_UTC_OFFSET):%H:%M} "
                                     f"but never checked in on the portal."})
        elif clocked and not badged:
            issues.append({"employee_id": u.id, "employee_name": u.full_name,
                           "issue": "checked_in_not_badged",
                           "detail": "Checked in on the portal with no badge record."})
        elif badged and scans[u.id].single_scan:
            issues.append({"employee_id": u.id, "employee_name": u.full_name,
                           "issue": "single_scan",
                           "detail": "Badged in but not out, so the device cannot "
                                     "corroborate the hours."})
    return issues
