"""
A device enrolment outlives the person it was made for.

Mapping a device name re-points its whole history at the employee, which is
right for somebody whose name has been reporting since they joined, and wrong
for a name that was enrolled to someone else first. `saad` reported from
October 2025 to April 2026, went quiet for six months, then came back when a
new Mian Saad Karim was enrolled under the same name. Mapping it handed him
thirty-nine days of another person's attendance from before he worked here.

Scans older than the employee's start date stay attached to nobody. That is
the honest answer: nobody knows whose they are.
"""
from datetime import date, datetime, timedelta

from app.models.biometric import BiometricIdentity, BiometricScan
from app.models.user import User

URL = "/api/v1/biometric/identities"


def setup_identity(env, joined: date, scan_days):
    """One device name with scans on the given dates, mapped to nobody yet."""
    db = env.Session()
    try:
        person = db.query(User).filter(User.id == env.ids["eng"]).first()
        person.date_of_joining = datetime.combine(joined, datetime.min.time())
        ident = BiometricIdentity(device_name="saad", scan_count=len(scan_days))
        db.add(ident)
        for day in scan_days:
            at = datetime.combine(day, datetime.min.time())
            db.add(BiometricScan(device_name="saad", scan_date=day,
                                 first_scan=at, last_scan=at + timedelta(hours=8)))
        db.commit()
        return ident.id, person.id
    finally:
        db.close()


def attached(env, employee_id):
    db = env.Session()
    try:
        return [s.scan_date for s in db.query(BiometricScan)
                .filter(BiometricScan.employee_id == employee_id)
                .order_by(BiometricScan.scan_date).all()]
    finally:
        db.close()


class TestMappingBringsTheirOwnHistory:

    def test_scans_from_their_own_time_are_attached(self, env):
        joined = date(2026, 10, 5)
        days = [joined + timedelta(days=1), joined + timedelta(days=2)]
        ident_id, employee_id = setup_identity(env, joined, days)

        res = env.put("admin", f"{URL}/{ident_id}", {"employee_id": employee_id})
        assert res.status_code == 200
        assert attached(env, employee_id) == days

    def test_a_scan_on_the_joining_day_itself_counts(self, env):
        joined = date(2026, 10, 5)
        ident_id, employee_id = setup_identity(env, joined, [joined])

        env.put("admin", f"{URL}/{ident_id}", {"employee_id": employee_id})
        assert attached(env, employee_id) == [joined]


class TestItDoesNotReachBackPastTheirStart:

    def test_somebody_elses_history_is_left_alone(self, env):
        joined = date(2026, 10, 5)
        theirs = [joined + timedelta(days=3)]
        before = [date(2025, 10, 8), date(2026, 4, 13)]
        ident_id, employee_id = setup_identity(env, joined, before + theirs)

        env.put("admin", f"{URL}/{ident_id}", {"employee_id": employee_id})
        assert attached(env, employee_id) == theirs

    def test_the_older_scans_stay_attached_to_nobody(self, env):
        joined = date(2026, 10, 5)
        before = [date(2025, 10, 8)]
        ident_id, employee_id = setup_identity(env, joined, before + [joined])

        env.put("admin", f"{URL}/{ident_id}", {"employee_id": employee_id})
        db = env.Session()
        try:
            orphan = (db.query(BiometricScan)
                      .filter(BiometricScan.scan_date == before[0]).first())
            assert orphan.employee_id is None
        finally:
            db.close()

    def test_remapping_releases_what_is_no_longer_theirs(self, env):
        """
        Pointing the name at somebody who joined later must give back the days
        that belong to the period before them.
        """
        joined = date(2026, 10, 5)
        days = [date(2026, 1, 1), joined + timedelta(days=1)]
        ident_id, employee_id = setup_identity(env, joined, days)

        # Map it while the person's start date is early, so everything attaches.
        db = env.Session()
        try:
            person = db.query(User).filter(User.id == employee_id).first()
            person.date_of_joining = datetime(2025, 1, 1)
            db.commit()
        finally:
            db.close()
        env.put("admin", f"{URL}/{ident_id}", {"employee_id": employee_id})
        assert len(attached(env, employee_id)) == 2

        # Correct the start date and map again: the early day is released.
        db = env.Session()
        try:
            person = db.query(User).filter(User.id == employee_id).first()
            person.date_of_joining = datetime.combine(joined, datetime.min.time())
            db.commit()
        finally:
            db.close()
        env.put("admin", f"{URL}/{ident_id}", {"employee_id": employee_id})
        assert attached(env, employee_id) == [joined + timedelta(days=1)]


class TestIgnoringStillWorks:

    def test_setting_a_name_aside_detaches_nothing_it_should_not(self, env):
        joined = date(2026, 10, 5)
        ident_id, _ = setup_identity(env, joined, [joined])
        res = env.put("admin", f"{URL}/{ident_id}", {"ignored": True})
        assert res.status_code == 200
        assert res.json()["ignored"] is True
