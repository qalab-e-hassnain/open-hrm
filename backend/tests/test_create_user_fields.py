"""
What the endpoint accepts, it keeps.

POST /users/ took father_name, date_of_joining, the banking details and three
other fields, answered 201, and wrote none of them. That is the worst way to
lose data: the caller is told it worked, and nothing is wrong until somebody
reads a contract with no parentage on it or a payslip with no account to pay
into.

The two that matter most:

  * father_name, because an employment agreement prints "X d/o Y" from it.
  * date_of_joining, which carries a comment on the schema explaining that it
    exists so an admin can backfill a real historical join date — and was
    then never read, so everybody got the day their row happened to be
    written.
"""
from datetime import datetime

JOINER = {
    "email": "newjoiner@example.com",
    "full_name": "Nadia New",
    "password": "a-long-enough-password",
    "gender": "female",
    "date_of_birth": "1996-03-04T00:00:00",
    "role": "employee",
}


def create(env, **overrides):
    body = {**JOINER, **overrides}
    res = env.post("admin", "/api/v1/users/", body)
    assert res.status_code == 201, res.text
    return res.json()


def stored(env, user_id):
    from app.models.user import User
    db = env.Session()
    try:
        return db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()


class TestFieldsThatUsedToBeDropped:

    def test_fathers_name_is_kept(self, env):
        """A contract reads "d/o {father_name}". Without it the line is blank."""
        created = create(env, father_name="Abdul Latif")
        assert stored(env, created["id"]).father_name == "Abdul Latif"

    def test_a_backfilled_joining_date_is_kept(self, env):
        created = create(env, date_of_joining="2022-01-01T00:00:00")
        assert stored(env, created["id"]).date_of_joining == datetime(2022, 1, 1)

    def test_an_omitted_joining_date_still_defaults_to_now(self, env):
        """Leaving it out must keep the old behaviour, not write a null."""
        created = create(env, email="nodate@example.com")
        assert stored(env, created["id"]).date_of_joining is not None

    def test_banking_details_are_kept(self, env):
        created = create(env, bank_number="Bank Alfalah",
                         account_title="Nadia New",
                         account_number="04181008637599",
                         branch_code="0418")
        row = stored(env, created["id"])
        # bank_number holds the bank's NAME, despite what it is called.
        assert row.bank_number == "Bank Alfalah"
        assert row.account_title == "Nadia New"
        assert row.account_number == "04181008637599"
        assert row.branch_code == "0418"

    def test_personal_email_and_remote_flag_are_kept(self, env):
        created = create(env, personal_email="nadia@example.com",
                         works_remotely=True)
        row = stored(env, created["id"])
        assert row.personal_email == "nadia@example.com"
        assert row.works_remotely is True


class TestTheFieldsThatAlreadyWorked:
    """Guarding against a careless edit to the same constructor."""

    def test_the_basics_survive(self, env):
        created = create(env, phone="03312841267", address="H13/3, Islamabad",
                         cnic_number="41304-5024142-2", department="Marketing",
                         designation="Go-to-market Strategies",
                         emergency_contact_name="Muhammad Umar",
                         emergency_contact_phone="03351039927",
                         base_salary=120000.0)
        row = stored(env, created["id"])
        assert row.phone == "03312841267"
        assert row.address == "H13/3, Islamabad"
        assert row.cnic_number == "41304-5024142-2"
        assert row.department == "Marketing"
        assert row.designation == "Go-to-market Strategies"
        assert row.emergency_contact_name == "Muhammad Umar"
        assert row.base_salary == 120000.0
