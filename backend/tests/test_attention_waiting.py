"""
Opening a document is not signing it.

The attention panel counted only documents still at ISSUED, so the five
people who had opened their agreement and not signed it vanished from the
chase list. On screen that read as a contradiction with no explanation: "20
people have no signed contract" directly above "13 documents issued but not
signed", with nothing to say where the other seven went.

Someone who read it and did not sign is precisely who needs chasing.
"""
from app.api.v1.analytics import needs_attention
from app.models.document import (DocumentKindEnum, DocumentStatusEnum,
                                 DocumentTemplate, EmployeeDocument)


def waiting_count(env, key):
    db = env.Session()
    try:
        from app.models.user import User
        admin = db.query(User).filter(User.id == env.ids["admin"]).first()
        for item in needs_attention(db=db, current_user=admin):
            if item.key == key:
                return item.count
        return 0
    finally:
        db.close()


def issue(db, employee_id, status, requires_signature=True):
    from datetime import datetime
    tpl = db.query(DocumentTemplate).first()
    if tpl is None:
        tpl = DocumentTemplate(kind=DocumentKindEnum.CONTRACT, title="Key Employee Agreement",
                               version=1, body_html="<p>x</p>", requires_signature=True,
                               is_active=True)
        db.add(tpl)
        db.flush()
    doc = EmployeeDocument(
        employee_id=employee_id, template_id=tpl.id, template_version=tpl.version,
        kind=DocumentKindEnum.CONTRACT, title="Key Employee Agreement",
        body_html="<p>x</p>", content_hash="x",
        requires_signature=requires_signature, status=status,
        issued_by=1, issued_at=datetime.utcnow(), verification_code=f"c{employee_id}{status}")
    db.add(doc)
    db.commit()
    return doc


class TestDocumentsWaitingOnSomebody:

    def test_an_unopened_document_is_waiting(self, env):
        db = env.Session()
        try:
            issue(db, env.ids["eng"], DocumentStatusEnum.ISSUED)
        finally:
            db.close()
        assert waiting_count(env, "documents_unsigned") == 1

    def test_an_opened_but_unsigned_document_is_also_waiting(self, env):
        """The one that used to disappear."""
        db = env.Session()
        try:
            issue(db, env.ids["eng"], DocumentStatusEnum.VIEWED)
        finally:
            db.close()
        assert waiting_count(env, "documents_unsigned") == 1

    def test_a_signed_document_is_not_waiting(self, env):
        db = env.Session()
        try:
            issue(db, env.ids["eng"], DocumentStatusEnum.SIGNED)
        finally:
            db.close()
        assert waiting_count(env, "documents_unsigned") == 0

    def test_one_that_needs_no_signature_is_not_waiting(self, env):
        db = env.Session()
        try:
            issue(db, env.ids["eng"], DocumentStatusEnum.ISSUED, requires_signature=False)
        finally:
            db.close()
        assert waiting_count(env, "documents_unsigned") == 0

    def test_both_states_are_counted_together(self, env):
        db = env.Session()
        try:
            issue(db, env.ids["eng"], DocumentStatusEnum.ISSUED)
            issue(db, env.ids["eng2"], DocumentStatusEnum.VIEWED)
            issue(db, env.ids["lead"], DocumentStatusEnum.SIGNED)
        finally:
            db.close()
        assert waiting_count(env, "documents_unsigned") == 2


class TestTheTwoCountsAgree:
    """
    The panel shows both figures together, so the gap between them has to be
    explainable: people with no signed contract, less those waiting on one,
    should leave only the people who were never issued one at all.
    """

    def test_the_gap_is_only_people_never_issued_one(self, env):
        db = env.Session()
        try:
            issue(db, env.ids["eng"], DocumentStatusEnum.VIEWED)
            issue(db, env.ids["eng2"], DocumentStatusEnum.ISSUED)
            issue(db, env.ids["lead"], DocumentStatusEnum.SIGNED)
        finally:
            db.close()

        unsigned = waiting_count(env, "contract_missing")
        pending = waiting_count(env, "documents_unsigned")
        # Everyone active, less the one who signed.
        db = env.Session()
        try:
            from app.models.user import User
            active = db.query(User).filter(User.is_active.is_(True)).count()
        finally:
            db.close()

        assert unsigned == active - 1
        assert pending == 2
        # The remainder were never issued anything, which is a different
        # problem with a different fix.
        assert unsigned - pending == active - 3
