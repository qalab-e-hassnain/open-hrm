#!/usr/bin/env python
"""
Issue the employment agreement to the active workforce.

Reports by default and writes nothing. Pass --apply to issue.

    python scripts/issue_contracts.py --issued-by "Qalab" \
        --signatory "Sardar Usairem Alamgeer" \
        --signatory-designation "Chief Executive Officer"
    ... --apply

Each issue renders the template once against that employee's own record,
hashes the result and notifies them that it is waiting for their signature —
so this is twenty legal documents and twenty notifications, not a report.
Two things are checked before anything is written:

  * Every placeholder the template uses must be fillable for that employee.
    A contract that goes out with a blank salary or a missing CNIC is worse
    than no contract, so an employee with gaps is skipped and named, and
    --force is needed to issue to them anyway.

  * Anyone who already holds a live copy of this template version is left
    alone, so a second run does not issue duplicates.

The signatory is named separately from the issuer. The administrator who runs
this is not the person who binds the Company.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.v1.documents import issue_document                       # noqa: E402
from app.database import SessionLocal                                 # noqa: E402
from app.models.document import (DocumentStatusEnum, DocumentTemplate,  # noqa: E402
                                 EmployeeDocument)
from app.models.notification import NotificationTypeEnum              # noqa: E402
from app.models.user import User                                      # noqa: E402
from app.utils import audit                                           # noqa: E402
from app.utils.documents import build_context, missing_placeholders    # noqa: E402
from app.utils.notifications import create_notification               # noqa: E402

LIVE = (DocumentStatusEnum.ISSUED, DocumentStatusEnum.VIEWED,
        DocumentStatusEnum.SIGNED)


def one_user(db, needle):
    found = (db.query(User)
             .filter(User.full_name.ilike(f"%{needle}%"), User.is_active.is_(True))
             .all())
    if len(found) != 1:
        raise SystemExit(f"'{needle}' matched {len(found)}: "
                         f"{[u.full_name for u in found]}")
    return found[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issued-by", required=True,
                    help="name, or part of one, of the administrator issuing")
    ap.add_argument("--signatory", required=True,
                    help="who signs for the Company")
    ap.add_argument("--signatory-designation", required=True)
    ap.add_argument("--template", type=int, default=None,
                    help="template id; default the active CONTRACT template")
    ap.add_argument("--employee", default=None,
                    help="issue to one person only, by name")
    ap.add_argument("--apply", action="store_true", help="write and notify")
    ap.add_argument("--force", action="store_true",
                    help="issue even where placeholders cannot be filled")
    ap.add_argument("--reissue", action="store_true",
                    help="issue again to people who already hold this version")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        issuer = one_user(db, args.issued_by)
        if args.template:
            tpl = db.get(DocumentTemplate, args.template)
        else:
            tpl = (db.query(DocumentTemplate)
                   .filter(DocumentTemplate.kind == "CONTRACT",
                           DocumentTemplate.is_active.is_(True))
                   .order_by(DocumentTemplate.version.desc()).first())
        if tpl is None:
            raise SystemExit("no contract template found")

        staff = db.query(User).filter(User.is_active.is_(True))
        if args.employee:
            staff = [one_user(db, args.employee)]
        else:
            staff = staff.order_by(User.date_of_joining).all()

        held = {}
        for row in (db.query(EmployeeDocument)
                    .filter(EmployeeDocument.template_id == tpl.id,
                            EmployeeDocument.status.in_(LIVE)).all()):
            held.setdefault(row.employee_id, []).append(row)

        print(f"{tpl.title} v{tpl.version} (#{tpl.id})")
        print(f"issued by {issuer.full_name}, signed for the Company by "
              f"{args.signatory}, {args.signatory_designation}")
        print(f"{'APPLYING' if args.apply else 'dry run, nothing is written'}\n")
        print(f"  {'employee':<28}{'joined':<12}{'probation':<34}action")
        print("  " + "-" * 86)

        issued = skipped = gaps = 0
        for employee in staff:
            ctx = build_context(db, employee, extra={
                "signatory_name": args.signatory,
                "signatory_designation": args.signatory_designation,
            })
            short = (("on probation to "
                      + ctx["probation_clause"].rsplit("ending on ", 1)[-1].rstrip("."))
                     if ctx["is_on_probation"] == "yes" else
                     ("no probation, " + ctx["service_months"] + " months served"))
            missing = missing_placeholders(tpl.body_html, ctx)
            joined = (employee.date_of_joining.date().isoformat()
                      if employee.date_of_joining else "—")
            name = (employee.full_name or "")[:27]

            if employee.id in held and not args.reissue:
                skipped += 1
                print(f"  {name:<28}{joined:<12}{short:<34}holds v{tpl.version} already")
                continue
            if missing and not args.force:
                gaps += 1
                print(f"  {name:<28}{joined:<12}{short:<34}SKIP — cannot fill "
                      f"{', '.join(missing)}")
                continue

            note = " (forced)" if missing else ""
            print(f"  {name:<28}{joined:<12}{short:<34}ISSUE{note}")
            issued += 1
            if args.apply:
                doc = issue_document(db, employee, tpl, issuer,
                                     signatory_name=args.signatory,
                                     signatory_designation=args.signatory_designation)
                db.flush()
                create_notification(
                    db=db, user_id=employee.id, title=f"{tpl.title} to sign",
                    message=(f"{tpl.title} has been issued to you and is waiting "
                             f"for your signature."),
                    notification_type=NotificationTypeEnum.INFO)
                db.commit()
                audit.record(db, "document.issued", actor=issuer,
                             target_type="employee_document", target_id=doc.id,
                             detail={"employee_id": employee.id,
                                     "kind": tpl.kind.value, "title": tpl.title,
                                     "version": tpl.version,
                                     "signatory": args.signatory,
                                     "issued_by_script": True})

        print(f"\n  {issued} to issue, {skipped} already hold it, "
              f"{gaps} with unfillable placeholders")

        # Prove it rather than assert it: every document just written is read
        # back, and its stored hash recomputed from its stored text.
        if args.apply:
            db.expire_all()
            fresh = (db.query(EmployeeDocument)
                     .filter(EmployeeDocument.template_id == tpl.id,
                             EmployeeDocument.status.in_(LIVE)).all())
            from app.utils.documents import content_hash
            bad = [d.id for d in fresh
                   if content_hash(d.body_html) != d.content_hash]
            print(f"  after writing: {len(fresh)} live copies of v{tpl.version}, "
                  f"{len(bad)} failing their integrity check")
            return 0 if not bad else 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
