"""
The signed-in user is cached for a few seconds. A change to them is not.

Looking the caller up by email on every request is one round trip, and the
database is far enough away that it cost more than most endpoints spend doing
their work. Caching it is only safe if a change to someone's role or to their
active state takes effect immediately rather than waiting out the cache — so
that is what these tests hold onto.
"""
from app.core import user_cache
from app.models.user import User

# Any endpoint that simply requires a signed-in caller. /users/me is
# avoided deliberately: it serialises the email, and the fixtures use the
# reserved .test domain, which EmailStr refuses.
SIGNED_IN = "/api/v1/notifications/unread-count"
ADMIN_ONLY = "/api/v1/attendance/team/today"


class TestItActuallyCaches:

    def test_a_second_request_does_not_read_the_user_again(self, env):
        env.get("eng", SIGNED_IN)
        assert "eng@qgt.test" in user_cache._cache

        before = user_cache._cache["eng@qgt.test"]
        env.get("eng", SIGNED_IN)
        # Same entry object: the row was not read a second time.
        assert user_cache._cache["eng@qgt.test"] is before

    def test_the_cached_user_is_the_right_person(self, env):
        env.get("eng", SIGNED_IN)
        env.get("admin", SIGNED_IN)
        assert user_cache._cache["eng@qgt.test"][1]["id"] == env.ids["eng"]
        assert user_cache._cache["admin@qgt.test"][1]["id"] == env.ids["admin"]


class TestAChangeIsNotMasked:

    def test_losing_a_role_takes_effect_at_once(self, env):
        # Warm the cache as a manager, who may call the team endpoint.
        assert env.get("lead", ADMIN_ONLY).status_code == 200

        db = env.Session()
        try:
            lead = db.query(User).filter(User.id == env.ids["lead"]).first()
            lead.role = "employee"
            db.commit()
        finally:
            db.close()

        # Without eviction this would still be 200 until the TTL ran out.
        assert env.get("lead", ADMIN_ONLY).status_code == 403

    def test_being_deactivated_takes_effect_at_once(self, env):
        assert env.get("eng", SIGNED_IN).status_code == 200

        db = env.Session()
        try:
            person = db.query(User).filter(User.id == env.ids["eng"]).first()
            person.is_active = False
            db.commit()
        finally:
            db.close()

        assert env.get("eng", SIGNED_IN).status_code == 403

    def test_a_changed_email_drops_the_old_entry(self, env):
        env.get("eng2", SIGNED_IN)
        assert "eng2@qgt.test" in user_cache._cache

        db = env.Session()
        try:
            person = db.query(User).filter(User.id == env.ids["eng2"]).first()
            person.email = "renamed@qgt.test"
            db.commit()
        finally:
            db.close()

        # The old address must not keep working off the back of the cache.
        assert "eng2@qgt.test" not in user_cache._cache


class TestItDoesNotLeakBetweenPeople:

    def test_each_caller_gets_their_own_record(self, env):
        for who in ("admin", "hr", "lead", "eng", "eng2"):
            assert env.get(who, SIGNED_IN).status_code == 200
        # Five callers, five separate entries, each holding its own id.
        for who, email in (("admin", "admin@qgt.test"), ("hr", "hr@qgt.test"),
                           ("lead", "lead@qgt.test"), ("eng", "eng@qgt.test"),
                           ("eng2", "eng2@qgt.test")):
            assert user_cache._cache[email][1]["id"] == env.ids[who]
