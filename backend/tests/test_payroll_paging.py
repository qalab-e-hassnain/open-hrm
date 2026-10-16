"""
The payroll list is paged, and says so.

It used to have no ORDER BY and a hard page size of 100 with nothing to say
it had been capped. With 168 records on file that meant 68 of them did not
exist as far as the portal was concerned, and which 100 came back was
whatever order the database felt like.

A page size is fine. A page size you cannot see past is not, so the total is
reported in a header — and because the portal is on another origin, the test
checks the browser is actually allowed to read it.
"""
from app.models.payroll import Payroll

URL = "/api/v1/payroll/"
ORIGIN = {"Origin": "https://hr.example.com"}


def seed(db, employee_id, count, year=2026):
    """`count` payslips spread backwards from December."""
    made = []
    for n in range(count):
        month = 12 - (n % 12)
        row = Payroll(employee_id=employee_id, month=month, year=year - (n // 12),
                      base_salary=100000.0, net_salary=90000.0, tax=5000.0)
        db.add(row)
        made.append(row)
    db.commit()
    return made


class TestItIsPagedRatherThanTruncated:

    def test_the_total_is_reported(self, env):
        db = env.Session()
        seed(db, env.ids["eng"], 7)
        db.close()

        res = env.get("admin", URL, params={"limit": 3})
        assert res.status_code == 200
        assert len(res.json()) == 3
        assert res.headers["X-Total-Count"] == "7"

    def test_the_rest_can_be_fetched(self, env):
        db = env.Session()
        seed(db, env.ids["eng"], 7)
        db.close()

        first = env.get("admin", URL, params={"limit": 4, "skip": 0}).json()
        second = env.get("admin", URL, params={"limit": 4, "skip": 4}).json()
        assert len(first) == 4 and len(second) == 3
        # No record appears on both pages, and none is missed.
        ids = {row["id"] for row in first} | {row["id"] for row in second}
        assert len(ids) == 7

    def test_a_browser_on_another_origin_may_read_the_total(self, env):
        """
        The portal is served from a different origin to the API, so a response
        header is invisible to it unless the server says otherwise. Without
        this the client cannot tell a full page from the end of the list.
        """
        db = env.Session()
        seed(db, env.ids["eng"], 3)
        db.close()

        res = env.client.get(URL, params={"limit": 2},
                             headers={**env.hdr("admin"), **ORIGIN})
        exposed = res.headers.get("access-control-expose-headers", "")
        assert "X-Total-Count" in res.headers
        assert exposed == "*" or "x-total-count" in exposed.lower(), (
            f"the total is sent but not exposed to the browser: {exposed!r}")


class TestTheOrderIsDefined:

    def test_newest_period_first(self, env):
        db = env.Session()
        for month, year in ((1, 2025), (6, 2026), (3, 2026)):
            db.add(Payroll(employee_id=env.ids["eng"], month=month, year=year,
                           base_salary=100000.0, net_salary=90000.0, tax=0.0))
        db.commit()
        db.close()

        rows = env.get("admin", URL).json()
        periods = [(r["year"], r["month"]) for r in rows]
        assert periods == sorted(periods, reverse=True), (
            "without a defined order, which records a page returns is arbitrary")
