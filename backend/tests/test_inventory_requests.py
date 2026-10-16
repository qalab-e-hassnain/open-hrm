"""
Stock requests: an engineer asks, a lead approves, the custodian issues.

The interesting cases are the boundaries of "own team" — the one cell in the
permission matrix that is not a flat yes or no — and the link from a closed
request back to the movement that satisfied it.
"""
from app.models.inventory import InvRequest, RequestStatusEnum
from app.models.team import Team, UserTeam


def _raise(env, who="eng", **extra):
    payload = {"category_id": env.ids["cat_stock"], "qty": 100,
               "purpose": "Building the CBOXX rev B boards"}
    payload.update(extra)
    r = env.post(who, "/api/v1/inventory/requests", payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_request_starts_pending(env):
    row = _raise(env)
    assert row["status"] == RequestStatusEnum.PENDING.value
    assert row["requester_id"] == env.ids["eng"]
    assert row["category_name"] == "Components and consumables"


def test_a_request_must_name_an_item_or_a_category(env):
    r = env.post("eng", "/api/v1/inventory/requests", {"qty": 1, "purpose": "Stuff"})
    assert r.status_code == 400
    assert "not neither" in r.json()["detail"]


def test_the_lead_approves_for_their_own_reports(env):
    row = _raise(env)
    r = env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
                 {"approved": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == RequestStatusEnum.APPROVED.value
    assert r.json()["approver_id"] == env.ids["lead"]


def test_a_lead_from_another_team_cannot_approve(env):
    """
    The 'Own team' cell. `eng` reports to `lead`; a second manager who leads
    nobody relevant is refused.
    """
    db = env.session()
    try:
        from app.models.user import RoleEnum, User
        from app.core.security import get_password_hash
        stranger = User(email="other-lead@qgt.test", full_name="Omar Otherteam",
                        role=RoleEnum.MANAGER, is_active=True,
                        hashed_password=get_password_hash("pw"))
        db.add(stranger)
        db.commit()
        stranger_id = stranger.id
    finally:
        db.close()
    from app.core.security import create_access_token
    env.tokens["stranger"] = create_access_token({"sub": "other-lead@qgt.test"})
    env.ids["stranger"] = stranger_id

    row = _raise(env)
    r = env.post("stranger", f"/api/v1/inventory/requests/{row['id']}/decision",
                 {"approved": True})
    assert r.status_code == 403
    assert "do not approve requests" in r.json()["detail"]


def test_a_shared_team_is_enough_to_approve(env):
    """A lead who shares a team with the requester counts, not only a direct report."""
    from app.core.security import create_access_token, get_password_hash
    from app.models.user import RoleEnum, User

    db = env.session()
    try:
        boss = User(email="team-lead@qgt.test", full_name="Tariq Teamlead",
                    role=RoleEnum.MANAGER, is_active=True,
                    hashed_password=get_password_hash("pw"))
        db.add(boss)
        db.flush()
        team = Team(name="Firmware", manager_id=boss.id)
        db.add(team)
        db.flush()
        db.add(UserTeam(user_id=env.ids["eng"], team_id=team.id))
        db.commit()
    finally:
        db.close()
    env.tokens["boss"] = create_access_token({"sub": "team-lead@qgt.test"})

    row = _raise(env)
    r = env.post("boss", f"/api/v1/inventory/requests/{row['id']}/decision",
                 {"approved": True})
    assert r.status_code == 200, r.text


def test_nobody_approves_their_own_request(env):
    row = _raise(env, who="lead")
    r = env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
                 {"approved": True})
    assert r.status_code == 403
    assert "your own request" in r.json()["detail"]


def test_a_custodian_may_approve_anything_including_their_own(env):
    """One person runs the store; making them find an approver would stall it."""
    row = _raise(env, who="custodian")
    r = env.post("custodian", f"/api/v1/inventory/requests/{row['id']}/decision",
                 {"approved": True})
    assert r.status_code == 200, r.text


def test_a_rejection_carries_its_reason(env):
    row = _raise(env)
    r = env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
                 {"approved": False, "reason": "Use what is already on the shelf"})
    assert r.json()["status"] == RequestStatusEnum.REJECTED.value
    assert r.json()["rejection_reason"] == "Use what is already on the shelf"


def test_only_an_approved_request_can_be_issued(env, stock):
    row = _raise(env)
    r = env.post("custodian", f"/api/v1/inventory/requests/{row['id']}/issue",
                 {"item_id": stock["id"]})
    assert r.status_code == 400
    assert "only an approved one" in r.json()["detail"]


def test_issuing_a_stock_line_consumes_it_and_links_the_movement(env, stock):
    row = _raise(env, qty=250)
    env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
             {"approved": True})
    r = env.post("custodian", f"/api/v1/inventory/requests/{row['id']}/issue",
                 {"item_id": stock["id"]})
    assert r.status_code == 200, r.text
    issued = r.json()
    assert issued["status"] == RequestStatusEnum.ISSUED.value
    assert issued["issued_movement_id"] is not None

    # The shelf went down by the requested quantity, once.
    assert env.get("custodian", f"/api/v1/inventory/items/{stock['id']}"
                   ).json()["qty"] == 4750

    db = env.session()
    try:
        from app.models.inventory import InvMovement
        movement = db.query(InvMovement).filter(
            InvMovement.id == issued["issued_movement_id"]).one()
        assert movement.movement_type == "consume"
        assert movement.qty_delta == -250
    finally:
        db.close()


def test_issuing_an_asset_checks_it_out_to_the_requester(env, item):
    row = _raise(env, category_id=None, item_id=item["id"], qty=1)
    env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
             {"approved": True})
    r = env.post("custodian", f"/api/v1/inventory/requests/{row['id']}/issue")
    assert r.status_code == 200, r.text
    assert env.get("eng", "/api/v1/inventory/me/holdings").json()[0]["id"] == item["id"]


def test_a_category_request_needs_an_item_naming_at_issue(env):
    row = _raise(env)
    env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
             {"approved": True})
    r = env.post("custodian", f"/api/v1/inventory/requests/{row['id']}/issue")
    assert r.status_code == 400
    assert "which item" in r.json()["detail"]


def test_an_engineer_cannot_issue_to_themselves(env, stock):
    row = _raise(env)
    env.post("lead", f"/api/v1/inventory/requests/{row['id']}/decision",
             {"approved": True})
    r = env.post("eng", f"/api/v1/inventory/requests/{row['id']}/issue",
                 {"item_id": stock["id"]})
    assert r.status_code == 403


def test_a_requester_sees_their_own_requests_and_not_everyone_elses(env):
    _raise(env, who="eng")
    _raise(env, who="eng2")
    mine = env.get("eng", "/api/v1/inventory/requests").json()
    assert {r["requester_id"] for r in mine} == {env.ids["eng"]}
    all_of_them = env.get("custodian", "/api/v1/inventory/requests").json()
    assert len(all_of_them) == 2



def test_a_lead_lists_only_the_requests_they_can_decide(env):
    from app.core.security import create_access_token, get_password_hash
    from app.models.user import RoleEnum, User
    db = env.session()
    try:
        db.add(User(email="other-lead@qgt.test", full_name="Omar Otherteam",
                    role=RoleEnum.MANAGER, is_active=True,
                    hashed_password=get_password_hash("pw")))
        db.commit()
    finally:
        db.close()
    env.tokens["stranger"] = create_access_token({"sub": "other-lead@qgt.test"})

    row = _raise(env, who="eng")
    assert [r["id"] for r in env.get("lead", "/api/v1/inventory/requests").json()] == [row["id"]]
    assert env.get("stranger", "/api/v1/inventory/requests").json() == []

def test_a_requester_can_cancel_before_it_is_issued(env):
    row = _raise(env)
    r = env.post("eng", f"/api/v1/inventory/requests/{row['id']}/cancel")
    assert r.json()["status"] == RequestStatusEnum.CANCELLED.value
    again = env.post("eng", f"/api/v1/inventory/requests/{row['id']}/cancel")
    assert again.status_code == 400
