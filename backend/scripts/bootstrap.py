#!/usr/bin/env python
"""
Take an empty database to a portal somebody can sign in to.

    alembic upgrade head
    python scripts/bootstrap.py --admin-email you@example.com --admin-name "Your Name"

Run it once, on a new deployment. It is idempotent: running it again reports
what already exists and changes nothing, so it is safe to re-run if you are
unsure whether it finished.

It creates the three things the portal cannot work without:

  * an administrator to sign in as, because every other account is created
    from inside the portal and there is no other way in;
  * a default working pattern, because attendance measures lateness and a
    short day against a shift, and with none defined it can answer neither;
  * the equipment register's categories and locations, so the inventory
    module has somewhere to put things.

It deliberately creates NO employment contract, job description, handbook or
leave policy document. Those are legal documents belonging to whoever is
running this, not samples to inherit from somebody else. Write your own in
the portal under HR Documents.

The password is not an argument. Typing one on a command line leaves it in
your shell history and in the process list where any other user on the
machine can read it; this prompts for it, or generates one and prints it once.
"""
import argparse
import getpass
import secrets
import string
import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect                                        # noqa: E402

from app.config import settings                                       # noqa: E402
from app.core.security import get_password_hash                       # noqa: E402
from app.database import SessionLocal, engine                         # noqa: E402
from app.models.inventory import (InvCategory, InvLocation,           # noqa: E402
                                  InvTagCounter)
from app.models.shift import Shift                                    # noqa: E402
from app.models.user import RoleEnum, User                            # noqa: E402

# A starting set for an equipment register. Rename or delete them in the
# portal; they exist so the first person to add an item has somewhere to put
# it rather than meeting an empty dropdown.
CATEGORIES = [
    ("Laptops and desktops", "serialised", "EQ", True),
    ("Monitors and displays", "serialised", "EQ", True),
    ("Phones and tablets", "serialised", "EQ", True),
    ("Peripherals", "serialised", "EQ", False),
    ("Furniture", "serialised", "OF", False),
    ("Consumables", "bulk", None, False),
]
LOCATIONS = [
    ("HQ", "Head office", None, "internal"),
    ("HQ-STORE", "Store room", "HQ", "internal"),
    ("EXT", "External", None, "external"),
]


def generated_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ask_password() -> str:
    first = getpass.getpass("Password for the administrator (blank to generate one): ")
    if not first:
        return ""
    if len(first) < 8:
        print("  At least 8 characters.")
        return ask_password()
    if first != getpass.getpass("Again: "):
        print("  Those did not match.")
        return ask_password()
    return first


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-name", required=True)
    ap.add_argument("--shift-start", default="09:00", help="local clock, default 09:00")
    ap.add_argument("--shift-end", default="17:00", help="local clock, default 17:00")
    ap.add_argument("--skip-inventory", action="store_true",
                    help="do not create equipment categories or locations")
    args = ap.parse_args()

    # A missing migration is the most common reason this fails, and the error
    # it would otherwise produce names a table rather than the cause.
    tables = set(inspect(engine).get_table_names())
    if "users" not in tables:
        print("The database has no tables. Run `alembic upgrade head` first.")
        return 1

    db = SessionLocal()
    try:
        print(f"  database : {settings.DATABASE_URL.split('@')[-1]}")
        print(f"  company  : {settings.COMPANY_NAME}")
        print(f"  portal   : {settings.PORTAL_URL}\n")

        # ---- the administrator -------------------------------------------
        existing = db.query(User).filter(User.email == args.admin_email).first()
        if existing:
            print(f"  administrator {args.admin_email} already exists — left alone")
        else:
            if db.query(User).filter(User.role == RoleEnum.ADMIN).count():
                print("  an administrator already exists; adding another")
            password = ask_password() or generated_password()
            db.add(User(
                email=args.admin_email, full_name=args.admin_name,
                hashed_password=get_password_hash(password),
                role=RoleEnum.ADMIN, is_active=True))
            db.commit()
            print(f"  administrator created: {args.admin_email}")
            print(f"    password: {password}")
            print("    Shown once. Sign in and change it.")

        # ---- a working pattern -------------------------------------------
        if db.query(Shift).filter(Shift.is_default.is_(True)).first():
            print("  a default shift already exists — left alone")
        else:
            start = time.fromisoformat(args.shift_start)
            end = time.fromisoformat(args.shift_end)
            db.add(Shift(name="General", start_time=start, end_time=end,
                         days_of_week="1111100", grace_minutes=15,
                         is_active=True, is_default=True))
            db.commit()
            print(f"  default shift created: {start:%H:%M}–{end:%H:%M}, Mon–Fri, "
                  f"15 minutes' grace")

        # ---- the equipment register --------------------------------------
        if args.skip_inventory:
            print("  equipment reference data skipped")
        else:
            made = 0
            # Several categories share a tag prefix, and a row added in this
            # session is invisible to a query until it is flushed — so asking
            # the database each time inserted the same prefix once per
            # category that used it.
            counters = {c.prefix for c in db.query(InvTagCounter).all()}
            for name, tracking, prefix, serial_required in CATEGORIES:
                if db.query(InvCategory).filter(InvCategory.name == name).first():
                    continue
                db.add(InvCategory(name=name, tracking_type=tracking,
                                   tag_prefix=prefix, serial_required=serial_required,
                                   domain="office"))
                if prefix and prefix not in counters:
                    db.add(InvTagCounter(prefix=prefix, next_number=1))
                    counters.add(prefix)
                made += 1
            ids = {}
            for code, name, parent, kind in LOCATIONS:
                row = db.query(InvLocation).filter(InvLocation.code == code).first()
                if row is None:
                    row = InvLocation(code=code, name=name, location_type=kind,
                                      parent_id=ids.get(parent))
                    db.add(row)
                    db.flush()
                    made += 1
                ids[code] = row.id
            db.commit()
            print(f"  equipment reference data: {made} row(s) created")

        print(f"\n  Done. Sign in at {settings.PORTAL_URL}")
        print("  Next: add your people under User Management, then write your")
        print("  own contract and policy templates under HR Documents.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
