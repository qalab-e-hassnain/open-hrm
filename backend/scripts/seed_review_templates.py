#!/usr/bin/env python
"""
Create the review forms from app/utils/review_templates.py.

Safe to run more than once: a template HR has edited is left alone unless
--force is given. Reports any designation that would fall through to the
default, which is usually a sign a new role needs its own form.

    python scripts/seed_review_templates.py --dry-run
    python scripts/seed_review_templates.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal                              # noqa: E402
from app.models.performance import ReviewTemplate                  # noqa: E402
from app.models.user import User                                   # noqa: E402
from app.utils.review_templates import (DEFAULT_TEMPLATE, SECTIONS,  # noqa: E402
                                        TEMPLATES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="overwrite templates HR has already edited")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    created = updated = skipped = 0
    try:
        for name, content in TEMPLATES.items():
            existing = (db.query(ReviewTemplate)
                        .filter(ReviewTemplate.name == name).first())
            fields = dict(
                description=content["description"],
                designations=list(content["designations"]),
                competencies=[dict(c) for c in content["competencies"]],
                sections=[dict(s) for s in SECTIONS],
                goal_weight=content["goal_weight"],
                competency_weight=content["competency_weight"],
                is_default=(name == DEFAULT_TEMPLATE),
                is_active=True,
            )
            if existing is None:
                print(f"CREATE  {name}  ({len(fields['competencies'])} competencies,"
                      f" {len(fields['designations'])} designations)")
                created += 1
                if not args.dry_run:
                    db.add(ReviewTemplate(name=name, **fields))
            elif args.force:
                print(f"REPLACE {name}")
                updated += 1
                if not args.dry_run:
                    for k, v in fields.items():
                        setattr(existing, k, v)
            else:
                print(f"SKIP    {name} (already exists; --force to replace)")
                skipped += 1

        if not args.dry_run:
            db.commit()
        print(f"\ncreated {created}, replaced {updated}, skipped {skipped}"
              + ("  (dry run — nothing written)" if args.dry_run else ""))

        claimed = set()
        for t in db.query(ReviewTemplate).filter(ReviewTemplate.is_active.is_(True)).all():
            claimed |= set(t.designations or [])
        falling_through = sorted({
            u.designation for u in db.query(User).filter(User.is_active.is_(True)).all()
            if u.designation and u.designation not in claimed})
        if falling_through:
            print("\nDesignations with no template of their own "
                  f"(they get '{DEFAULT_TEMPLATE}'):")
            for d in falling_through:
                print(f"  {d}")
        else:
            print("\nEvery designation in use has a template of its own.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
