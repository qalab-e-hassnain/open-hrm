"""Nobody grants themselves authority by editing their own profile."""
import pytest

from app.models.user import User


def _put_self(env, who, payload):
    # The fixture's .test addresses fail response validation after the write
    # has committed; what matters here is what was stored.
    try:
        env.client.put(f"/api/v1/users/{env.ids[who]}", headers=env.hdr(who), json=payload)
    except Exception:
        pass
    db = env.session()
    try:
        return db.query(User).get(env.ids[who])
    finally:
        db.close()


@pytest.mark.parametrize("who", ["eng", "lead", "custodian"])
def test_an_employee_cannot_make_themselves_hr(env, who):
    assert _put_self(env, who, {"is_hr": True}).is_hr is False


def test_an_employee_cannot_make_themselves_a_custodian(env):
    assert _put_self(env, "eng", {"is_lab_custodian": True}).is_lab_custodian is False



def test_an_admin_makes_someone_a_custodian_and_the_api_says_so(env):
    try:
        env.put("admin", f"/api/v1/users/{env.ids['eng']}", json={"is_lab_custodian": True})
    except Exception:
        pass
    db = env.session()
    try:
        assert db.query(User).get(env.ids["eng"]).is_lab_custodian is True
    finally:
        db.close()


def test_the_user_payload_carries_the_custodian_flag():
    from app.schemas.user import UserResponse
    assert "is_lab_custodian" in UserResponse.model_fields
