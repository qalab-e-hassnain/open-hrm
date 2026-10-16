#!/usr/bin/env python
"""
Fill a local development database with enough people to log in as.

For running the portal against a real backend on your own machine. It creates
one person per role the permission matrix distinguishes between, all with the
same password, so you can sign in as each and see what they see.

    createdb qgt_hrm_dev
    DATABASE_URL=postgresql://$USER@localhost:5432/qgt_hrm_dev alembic upgrade head
    DATABASE_URL=... python scripts/seed_local.py

Idempotent: re-running updates the people rather than duplicating them.

Never point this at a deployed database. It writes known passwords.
"""
import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import get_password_hash                       # noqa: E402
from app.database import SessionLocal                                 # noqa: E402
from app.models.user import RoleEnum, User                            # noqa: E402

PASSWORD = "Passw0rd!"

# (email, name, role, flags, designation, reports to)
PEOPLE = [
    ("admin@example.com", "Ayesha Admin", RoleEnum.ADMIN,
     {}, "Director", None),
    ("hr@example.com", "Hina HR", RoleEnum.MANAGER,
     {"is_hr": True}, "HR Manager", None),
    ("custodian@example.com", "Kamran Custodian", RoleEnum.EMPLOYEE,
     {"is_lab_custodian": True}, "Lab Custodian", "admin@example.com"),
    ("lead@example.com", "Lubna Lead", RoleEnum.MANAGER,
     {}, "Team Lead", "admin@example.com"),
    ("eng@example.com", "Imran Engineer", RoleEnum.EMPLOYEE,
     {}, "Firmware Engineer", "lead@example.com"),
    ("eng2@example.com", "Sana Engineer", RoleEnum.EMPLOYEE,
     {}, "Hardware Engineer", "lead@example.com"),
    # Named in the Location column of the physical count, so the importer can
    # resolve them to real holders rather than leaving two rows unmatched.
    ("hamza@example.com", "Hamza Khan", RoleEnum.EMPLOYEE,
     {}, "Engineer", "lead@example.com"),
    ("dania@example.com", "Dania Iqbal", RoleEnum.EMPLOYEE,
     {}, "Engineer", "lead@example.com"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", default=PASSWORD)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        url = str(db.get_bind().url)
        if "localhost" not in url and "127.0.0.1" not in url:
            print(f"Refusing to seed known passwords into {url.split('@')[-1]}.")
            print("This is for a local database only.")
            return 1

        hashed = get_password_hash(args.password)
        created = updated = 0
        for email, name, role, flags, designation, manager_email in PEOPLE:
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                user = User(email=email)
                db.add(user)
                created += 1
            else:
                updated += 1
            user.full_name = name
            user.role = role
            user.hashed_password = hashed
            user.is_active = True
            user.designation = designation
            user.department = "Engineering"
            user.base_salary = 150000.0
            user.standard_hours = 8.0
            user.date_of_joining = datetime.utcnow() - timedelta(days=500)
            user.probation_end_date = date.today() - timedelta(days=380)
            for field, value in flags.items():
                setattr(user, field, value)
        db.flush()

        by_email = {u.email: u for u in db.query(User).all()}
        for email, _, _, _, _, manager_email in PEOPLE:
            if manager_email and email in by_email:
                by_email[email].manager_id = by_email[manager_email].id
        db.commit()

        print(f"{created} created, {updated} updated. Password: {args.password}\n")
        for email, name, role, flags, designation, _ in PEOPLE:
            extra = " ".join(f"+{k}" for k in flags)
            print(f"  {email:28s} {name:20s} {role.value:9s} {extra}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
