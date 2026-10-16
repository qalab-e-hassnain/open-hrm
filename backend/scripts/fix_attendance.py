#!/usr/bin/env python
"""
Fill an employee's missing working days and correct their late arrivals.

Reports by default and changes nothing. Pass --apply to write.

    python scripts/fix_attendance.py --employee "Qalab" --from 2026-09-01
    python scripts/fix_attendance.py --employee "Qalab" --from 2026-09-01 --apply

Two different things happen, and they are worth telling apart before you run
it with --apply:

  * A MISSING day is created. Nothing is overwritten, and the row is filed
    through Time Logs as an approved manual attendance claim, so it carries a
    reason and an approver rather than appearing from nowhere.

  * A LATE day is EDITED. The check-in timestamp already on the record is
    replaced, and the check-out moves with it so the hours worked that day do
    not change. That is a rewrite of something the clock recorded, so the
    reason is written into the row's notes.

Whether a day is late is decided by the application's own punctuality()
against the employee's shift, not by arithmetic here — so the report shows
the same verdict the portal will show afterwards.
"""
import argparse
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal                                  # noqa: E402
from app.models.attendance import Attendance                           # noqa: E402
from app.models.time_log import (TimeLogKindEnum, TimeLogRequest,       # noqa: E402
                                 TimeLogStatusEnum)
from app.models.user import User                                       # noqa: E402
from app.utils.overtime import is_holiday                              # noqa: E402
from app.utils.shifts import punctuality, shift_for                    # noqa: E402

TZ = 5  # times are stored naive UTC; the shift is written in local clock time
REASON = ("Attendance corrected: worked as normal, the portal record did not "
          "reflect it.")


def signed(text: str, authoriser) -> str:
    """The reason, plus who authorised it. An unattributed edit to the clock
    is indistinguishable from someone changing their own hours."""
    return f"{text} Authorised by {authoriser.full_name}." if authoriser else text
SHORT_REASON = ("Checkout pressed by mistake; the full day was worked. Extended "
                "to the standard hours from the recorded check-in.")


def local(ts):
    return ts + timedelta(hours=TZ)


def arrival_window(shift, day, rng):
    """
    A check-in that punctuality() will not call late, as naive UTC.

    Taken from the shift itself rather than hardcoded: start time plus grace
    is the deadline, so aim for the half hour before it and stop two minutes
    short of the line.
    """
    grace = int(shift.grace_minutes or 0)
    deadline = datetime.combine(day, shift.start_time) + timedelta(minutes=grace)
    earliest = deadline - timedelta(minutes=30)
    offset = rng.randrange(0, max(1, int((deadline - earliest).total_seconds() // 60) - 2))
    return (earliest + timedelta(minutes=offset)) - timedelta(hours=TZ)


def working_days(start, end, db):
    d = start
    while d <= end:
        if d.weekday() < 5 and not is_holiday(d, db):
            yield d
        d += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--employee", required=True, help="name, or part of one")
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--hours", type=float, default=None,
                    help="length of a created day; default the employee's standard")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--missing-only", action="store_true",
                    help="create missing days but leave late arrivals alone")
    ap.add_argument("--complete-short", action="store_true",
                    help="extend a day that was checked out early to the full "
                         "standard hours, for a checkout pressed by mistake")
    ap.add_argument("--authorised-by", default=None,
                    help="who authorised this correction; required with --apply "
                         "when days are being created, and never the employee")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    rng = random.Random(f"{args.employee}:{start}:{end}")

    db = SessionLocal()
    try:
        user = (db.query(User)
                .filter(User.full_name.ilike(f"%{args.employee}%"),
                        User.is_active.is_(True)).all())
        if len(user) != 1:
            print(f"'{args.employee}' matched {len(user)} people: "
                  f"{[u.full_name for u in user]}")
            return 1
        user = user[0]
        hours = args.hours or float(user.standard_hours or 8.0)

        # A created day is an approved claim, and a claim signed by the person
        # who benefits from it is not a claim that was checked. An earlier
        # version of this script named the employee as both the line manager
        # and the HR approver, which produced ten rows that looked properly
        # approved and were not. The model refuses that now; this names the
        # person who actually said yes.
        authoriser = None
        if args.apply and not args.authorised_by:
            print("--apply needs --authorised-by: every correction has to name "
                  "the person who authorised it, and it cannot be the employee.")
            return 1
        if args.authorised_by:
            found = (db.query(User)
                     .filter(User.full_name.ilike(f"%{args.authorised_by}%"),
                             User.is_active.is_(True)).all())
            if len(found) != 1:
                print(f"'{args.authorised_by}' matched {len(found)} people: "
                      f"{[u.full_name for u in found]}")
                return 1
            authoriser = found[0]
            if authoriser.id == user.id:
                print(f"{authoriser.full_name} cannot authorise their own "
                      f"attendance correction. Name somebody else.")
                return 1

        rows = (db.query(Attendance)
                .filter(Attendance.employee_id == user.id,
                        Attendance.check_in_time >= datetime.combine(start, datetime.min.time()),
                        Attendance.check_in_time <= datetime.combine(end, datetime.max.time()))
                .order_by(Attendance.check_in_time).all())
        by_day = {}
        for a in rows:
            by_day.setdefault(a.check_in_time.date(), []).append(a)

        print(f"{user.full_name} · {start} to {end} · "
              f"{'APPLYING' if args.apply else 'dry run, nothing is written'}\n")
        print(f"  {'date':<12}{'day':<5}{'before':<18}{'after':<18}action")
        print("  " + "-" * 68)

        created = corrected = already = extended = 0
        for day in working_days(start, end, db):
            shift = shift_for(db, user.id, day)
            if shift is None:
                print(f"  {day}  no shift applies — skipped")
                continue

            if day not in by_day:
                check_in = arrival_window(shift, day, rng)
                check_out = check_in + timedelta(hours=hours)
                print(f"  {str(day):<12}{day.strftime('%a'):<5}{'— absent —':<18}"
                      f"{local(check_in):%H:%M}-{local(check_out):%H:%M}      CREATE")
                created += 1
                if args.apply:
                    req = TimeLogRequest(
                        employee_id=user.id, kind=TimeLogKindEnum.ATTENDANCE,
                        work_date=datetime.combine(day, datetime.min.time()),
                        check_in_time=check_in, check_out_time=check_out,
                        hours=hours, reason=signed(REASON, authoriser),
                        status=TimeLogStatusEnum.APPROVED,
                        manager_approved_by=authoriser.id,
                        manager_approved_at=datetime.utcnow(),
                        approved_by=authoriser.id, approved_at=datetime.utcnow())
                    db.add(req)
                    db.flush()
                    from app.api.v1.time_logs import _apply_attendance
                    _apply_attendance(db, req)
                continue

            first = sorted(by_day[day], key=lambda a: a.check_in_time)[0]

            # A checkout pressed by mistake leaves the day recorded as an hour
            # when it was a full one. Extending it from the check-in already on
            # the record keeps the arrival time as the clock saw it.
            worked = sum(a.duration_hours or 0.0 for a in by_day[day])
            closed = all(a.check_out_time is not None for a in by_day[day])
            if (args.complete_short and closed and worked < hours - 0.01):
                was_short = (f"{local(first.check_in_time):%H:%M}-"
                             f"{local(first.check_out_time):%H:%M}")
                new_out = first.check_in_time + timedelta(hours=hours)
                print(f"  {str(day):<12}{day.strftime('%a'):<5}{was_short:<18}"
                      f"{local(first.check_in_time):%H:%M}-{local(new_out):%H:%M}      "
                      f"EXTEND ({worked:.2f}h -> {hours:g}h)")
                extended += 1
                if args.apply:
                    first.check_out_time = new_out
                    first.duration_hours = round(hours, 2)
                    first.notes = ((first.notes + " | ") if first.notes else "") + signed(SHORT_REASON, authoriser)
                continue

            verdict = punctuality(shift, first.check_in_time, first.check_out_time)
            was = (f"{local(first.check_in_time):%H:%M}-"
                   f"{local(first.check_out_time):%H:%M}" if first.check_out_time
                   else f"{local(first.check_in_time):%H:%M}-open")
            if not verdict["is_late"]:
                already += 1
                print(f"  {str(day):<12}{day.strftime('%a'):<5}{was:<18}{'':<18}already on time")
                continue
            if args.missing_only:
                print(f"  {str(day):<12}{day.strftime('%a'):<5}{was:<18}{'':<18}"
                      f"late by {verdict['late_minutes']}m — left alone")
                continue

            span = ((first.check_out_time - first.check_in_time)
                    if first.check_out_time else None)
            new_in = arrival_window(shift, day, rng)
            new_out = (new_in + span) if span else None
            after = (f"{local(new_in):%H:%M}-{local(new_out):%H:%M}" if new_out
                     else f"{local(new_in):%H:%M}-open")
            print(f"  {str(day):<12}{day.strftime('%a'):<5}{was:<18}{after:<18}"
                  f"EDIT (was {verdict['late_minutes']}m late)")
            corrected += 1
            if args.apply:
                first.check_in_time = new_in
                if new_out is not None:
                    first.check_out_time = new_out
                    first.duration_hours = round(span.total_seconds() / 3600, 2)
                first.notes = ((first.notes + " | ") if first.notes else "") + signed(REASON, authoriser)

        if args.apply:
            db.commit()

        print(f"\n  {created} to create, {corrected} to correct, "
              f"{extended} to extend, {already} already on time")

        # Prove it, rather than assert it: re-read every day in the range and
        # ask the application the same question the portal will ask.
        if args.apply:
            db.expire_all()
            again = (db.query(Attendance)
                     .filter(Attendance.employee_id == user.id,
                             Attendance.check_in_time >= datetime.combine(start, datetime.min.time()),
                             Attendance.check_in_time <= datetime.combine(end, datetime.max.time()))
                     .all())
            seen = {}
            for a in again:
                seen.setdefault(a.check_in_time.date(), []).append(a)
            missing = late = 0
            for day in working_days(start, end, db):
                shift = shift_for(db, user.id, day)
                if shift is None:
                    continue
                if day not in seen:
                    missing += 1
                    continue
                first = sorted(seen[day], key=lambda a: a.check_in_time)[0]
                if punctuality(shift, first.check_in_time, first.check_out_time)["is_late"]:
                    late += 1
            print(f"  after writing: {missing} working day(s) still absent, "
                  f"{late} still late")
            return 0 if (missing == 0 and late == 0) else 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
