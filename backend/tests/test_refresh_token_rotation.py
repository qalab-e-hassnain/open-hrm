"""
A refresh token can be taken away, and reusing one ends the session.

Before this, refresh tokens were stateless JWTs: signed, valid for seven
days, and unknown to the server. Three things followed, and they are what
these tests pin down.

Signing out did nothing. The portal dropped the tokens locally and the server
was never told, so the refresh token it had issued went on working for the
rest of its week — for anyone holding a copy, and for the next person to use
the machine if the browser had kept it.

Rotating on refresh looked like a mitigation and was not. A new token came
back each time, but the old one stayed valid until its own expiry, so a
stolen token worked for a week regardless of how often the real user
refreshed.

And taking an account away did not take its sessions away. A deactivated
employee's phone could still mint fresh fifteen-minute access tokens from the
refresh token it already had, for another week.

What replaces it: every issued token has a row, tokens are grouped into a
family per sign-in, and each refresh supersedes the last link. A correctly
behaving client only ever presents the newest link — so presenting a
superseded one means two parties hold tokens from one chain, and the whole
family is revoked rather than guessing which holder is the real user.
"""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.core.security import create_access_token, decode_access_token, get_password_hash
from app.models.refresh_token import RefreshToken
from app.models.user import RoleEnum, User

PASSWORD = "A-password-for-the-tests-1"
NEW_PASSWORD = "A-brand-new-password-2!"

# Not the people the shared `env` fixture seeds: their addresses end in
# `.test`, which is a reserved TLD that EmailStr refuses, so they cannot be
# used against /auth/login-json at all.
ACCOUNT = "rotation.user@example.com"
OTHER = "rotation.other@example.com"


@pytest.fixture(autouse=True)
def accounts(env):
    """Two ordinary employees who can actually sign in."""
    db = env.Session()
    try:
        ids = {}
        for key, email, name in (("main", ACCOUNT, "Rotation User"),
                                 ("other", OTHER, "Other User")):
            user = User(email=email, full_name=name, role=RoleEnum.EMPLOYEE,
                        hashed_password=get_password_hash(PASSWORD),
                        is_active=True, base_salary=100000.0)
            db.add(user)
            db.flush()
            ids[key] = user.id
        db.commit()
        return ids | {"main_access": create_access_token({"sub": ACCOUNT}),
                      "other_access": create_access_token({"sub": OTHER})}
    finally:
        db.close()


def sign_in(env, email=ACCOUNT, *, remember=True):
    response = env.client.post("/api/v1/auth/login-json", json={
        "email": email, "password": PASSWORD, "remember_device": remember})
    assert response.status_code == 200, response.text
    return response.json()


def do_refresh(env, token):
    return env.client.post("/api/v1/auth/refresh", json={"refresh_token": token})


def row_for(env, token):
    jti = decode_access_token(token)["jti"]
    db = env.Session()
    try:
        return db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    finally:
        db.close()


class TestSigningIn:

    def test_remembering_a_device_issues_a_tracked_token(self, env, accounts):
        body = sign_in(env)
        assert body["refresh_token"]
        record = row_for(env, body["refresh_token"])
        assert record is not None, "the token was issued but never recorded"
        assert record.revoked_at is None
        assert record.user_id == accounts["main"]
        assert record.family_started_at is not None

    def test_not_remembering_issues_nothing_to_record(self, env):
        body = sign_in(env, remember=False)
        assert body["refresh_token"] is None
        db = env.Session()
        try:
            assert db.query(RefreshToken).count() == 0
        finally:
            db.close()

    def test_each_sign_in_starts_its_own_family(self, env):
        """
        Two devices are two sessions. Revoking one must not touch the other,
        which only works if they are separate families.
        """
        first = sign_in(env)["refresh_token"]
        second = sign_in(env)["refresh_token"]
        assert decode_access_token(first)["fid"] != decode_access_token(second)["fid"]


class TestRotation:

    def test_refreshing_returns_a_different_token(self, env):
        original = sign_in(env)["refresh_token"]
        body = do_refresh(env, original).json()
        assert body["refresh_token"] != original
        assert body["access_token"]

    def test_the_spent_token_is_marked_rotated_and_points_at_its_successor(
            self, env):
        original = sign_in(env)["refresh_token"]
        successor = do_refresh(env, original).json()["refresh_token"]

        spent = row_for(env, original)
        assert spent.revoked_at is not None
        assert spent.revoked_reason == "rotated"
        assert spent.replaced_by == decode_access_token(successor)["jti"]

    def test_the_successor_stays_in_the_same_family(self, env):
        original = sign_in(env)["refresh_token"]
        successor = do_refresh(env, original).json()["refresh_token"]
        assert (decode_access_token(original)["fid"]
                == decode_access_token(successor)["fid"])

    def test_the_successor_works_and_can_itself_be_rotated(self, env):
        token = sign_in(env)["refresh_token"]
        for _ in range(3):
            response = do_refresh(env, token)
            assert response.status_code == 200, response.text
            token = response.json()["refresh_token"]


class TestReuseDetection:
    """The case the whole table exists for."""

    def test_the_old_token_stops_working_immediately(self, env):
        original = sign_in(env)["refresh_token"]
        do_refresh(env, original)
        assert do_refresh(env, original).status_code == 401

    def test_reusing_it_revokes_the_whole_family(self, env):
        """
        Not just the replayed token. If only the replayed one died, an
        attacker who got in first would hold the live successor and the real
        user's next refresh would be the one that failed — the theft would
        succeed and look like a glitch.
        """
        original = sign_in(env)["refresh_token"]
        successor = do_refresh(env, original).json()["refresh_token"]

        # The attacker replays the token they copied.
        assert do_refresh(env, original).status_code == 401
        # The legitimate client's live token is gone too.
        assert do_refresh(env, successor).status_code == 401

        db = env.Session()
        try:
            family = decode_access_token(original)["fid"]
            live = (db.query(RefreshToken)
                    .filter(RefreshToken.family_id == family,
                            RefreshToken.revoked_at.is_(None)).count())
            assert live == 0
            replayed = (db.query(RefreshToken)
                        .filter(RefreshToken.jti
                                == decode_access_token(successor)["jti"]).one())
            assert replayed.revoked_reason == "reuse_detected"
        finally:
            db.close()

    def test_it_leaves_an_audit_record(self, env):
        """
        A revoked session with no explanation is a support ticket. This is the
        one place the system can say "two people held this session".
        """
        from app.models.audit_log import AuditLog
        original = sign_in(env)["refresh_token"]
        do_refresh(env, original)
        do_refresh(env, original)

        db = env.Session()
        try:
            entry = (db.query(AuditLog)
                     .filter(AuditLog.action == "auth.refresh.reuse_detected")
                     .first())
            assert entry is not None, "a reuse was detected and not recorded"
        finally:
            db.close()

    def test_another_device_is_unaffected(self, env):
        """Revocation is per family, so one compromised session is one session."""
        compromised = sign_in(env)["refresh_token"]
        other_device = sign_in(env)["refresh_token"]
        do_refresh(env, compromised)
        do_refresh(env, compromised)          # the replay

        assert do_refresh(env, other_device).status_code == 200


class TestSigningOut:

    def test_logout_revokes_the_session(self, env):
        token = sign_in(env)["refresh_token"]
        assert env.client.post("/api/v1/auth/logout",
                               json={"refresh_token": token}).status_code == 204
        assert do_refresh(env, token).status_code == 401

    def test_logout_revokes_the_family_not_just_the_token_given(self, env):
        original = sign_in(env)["refresh_token"]
        successor = do_refresh(env, original).json()["refresh_token"]
        env.client.post("/api/v1/auth/logout", json={"refresh_token": original})
        assert do_refresh(env, successor).status_code == 401

    def test_it_accepts_a_caller_with_no_token_at_all(self, env):
        """A session that never asked to be remembered still signs out."""
        assert env.client.post("/api/v1/auth/logout",
                               json={}).status_code == 204

    @pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
    def test_rubbish_does_not_produce_an_error(self, env, token):
        """
        204 either way. The caller is signing out regardless, and whether a
        token was recognised is not something to tell them.
        """
        assert env.client.post("/api/v1/auth/logout",
                               json={"refresh_token": token}).status_code == 204


class TestLosingAccess:

    def test_deactivating_a_user_ends_their_sessions(self, env, accounts):
        token = sign_in(env)["refresh_token"]
        env.client.delete(f"/api/v1/users/{accounts['main']}",
                          headers={"Authorization": f"Bearer {env.tokens['admin']}"})
        assert do_refresh(env, token).status_code == 401

    def test_an_admin_resetting_a_password_ends_their_sessions(self, env, accounts):
        """
        A reset exists to lock somebody out. If the sessions that password
        opened survive it, it has locked nobody out of anything.
        """
        token = sign_in(env)["refresh_token"]
        response = env.client.post(
            f"/api/v1/users/{accounts['main']}/reset-password",
            json={"new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD},
            headers={"Authorization": f"Bearer {env.tokens['admin']}"})
        assert response.status_code in (200, 204), response.text
        assert do_refresh(env, token).status_code == 401

    def test_changing_your_own_password_ends_other_devices_but_not_this_one(
            self, env, accounts):
        phone = sign_in(env)["refresh_token"]
        laptop = sign_in(env)["refresh_token"]

        response = env.client.post(
            "/api/v1/users/me/change-password",
            json={"current_password": PASSWORD,
                  "new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD},
            headers={"Authorization": f"Bearer {accounts['main_access']}"})
        assert response.status_code == 200, response.text
        body = response.json()

        # Both old sessions are gone...
        assert do_refresh(env, phone).status_code == 401
        assert do_refresh(env, laptop).status_code == 401
        # ...and the caller was handed one back so it is not signed out itself.
        assert body["refresh_token"]
        assert do_refresh(env, body["refresh_token"]).status_code == 200

    def test_no_replacement_is_issued_to_a_caller_who_had_no_session(self, env, accounts):
        """
        Handing a refresh token to somebody who never asked to be remembered
        would create a persistent session out of a password change.
        """
        response = env.client.post(
            "/api/v1/users/me/change-password",
            json={"current_password": PASSWORD,
                  "new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD},
            headers={"Authorization": f"Bearer {accounts['other_access']}"})
        assert response.status_code == 200, response.text
        assert response.json()["refresh_token"] is None


class TestWhatItRefuses:

    def test_an_unknown_token_id(self, env):
        """Correctly signed, never issued here — or pruned long ago."""
        from app.core.security import create_refresh_token
        forged = create_refresh_token({"sub": ACCOUNT})
        assert do_refresh(env, forged).status_code == 401

    def test_a_token_from_before_tokens_were_tracked(self, env):
        """
        No jti, so there is no family to rotate within and no way to tell a
        stolen one from a live one. It buys a sign-in, not a silent upgrade.
        """
        import jwt
        legacy = jwt.encode(
            {"sub": ACCOUNT, "type": "refresh",
             "exp": datetime.utcnow() + timedelta(days=1)},
            settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        assert do_refresh(env, legacy).status_code == 401

    def test_an_access_token_is_not_a_refresh_token(self, env):
        assert do_refresh(env, env.tokens["eng"]).status_code == 401

    def test_an_expired_token(self, env):
        token = sign_in(env)["refresh_token"]
        db = env.Session()
        try:
            record = db.query(RefreshToken).filter(
                RefreshToken.jti == decode_access_token(token)["jti"]).one()
            record.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()
        assert do_refresh(env, token).status_code == 401

    def test_every_refusal_says_the_same_thing(self, env):
        """
        Which reason applies is recorded server-side. Telling the caller
        whether a token was unknown, superseded or merely expired would let
        somebody probe the difference.
        """
        from app.core.security import create_refresh_token
        original = sign_in(env)["refresh_token"]
        do_refresh(env, original)

        messages = set()
        for token in (original, create_refresh_token({"sub": ACCOUNT}),
                      env.tokens["eng"], "rubbish"):
            response = do_refresh(env, token)
            assert response.status_code == 401
            messages.add(response.json()["detail"])
        assert len(messages) == 1, messages


class TestRefreshingCannotLastForEver:

    def test_a_family_older_than_the_ceiling_stops_refreshing(self, env):
        """
        A seven-day token refreshed every sixth day would otherwise never
        expire, and "remember this device" would quietly mean for ever.
        """
        token = sign_in(env)["refresh_token"]
        db = env.Session()
        try:
            record = db.query(RefreshToken).filter(
                RefreshToken.jti == decode_access_token(token)["jti"]).one()
            record.family_started_at = datetime.utcnow() - timedelta(
                days=settings.REFRESH_TOKEN_FAMILY_MAX_DAYS + 1)
            db.commit()
        finally:
            db.close()
        assert do_refresh(env, token).status_code == 401

    def test_the_ceiling_is_measured_from_the_first_sign_in(self, env):
        """Not from the last refresh, or rotating would reset it."""
        token = sign_in(env)["refresh_token"]
        started = row_for(env, token).family_started_at
        for _ in range(3):
            token = do_refresh(env, token).json()["refresh_token"]
        assert row_for(env, token).family_started_at == started


class TestPruning:

    def test_it_deletes_long_expired_rows_and_keeps_recent_ones(self, env):
        from app.api.v1.auth import prune_expired_refresh_tokens
        token = sign_in(env)["refresh_token"]
        recent = sign_in(env)["refresh_token"]

        db = env.Session()
        try:
            old = db.query(RefreshToken).filter(
                RefreshToken.jti == decode_access_token(token)["jti"]).one()
            old.expires_at = datetime.utcnow() - timedelta(days=90)
            db.commit()

            deleted = prune_expired_refresh_tokens(db)
            db.commit()
            assert deleted == 1
            remaining = {r.jti for r in db.query(RefreshToken).all()}
            assert decode_access_token(recent)["jti"] in remaining
            assert decode_access_token(token)["jti"] not in remaining
        finally:
            db.close()

    def test_a_recently_expired_token_is_kept_so_a_replay_is_visible(self, env):
        from app.api.v1.auth import prune_expired_refresh_tokens
        token = sign_in(env)["refresh_token"]
        db = env.Session()
        try:
            record = db.query(RefreshToken).filter(
                RefreshToken.jti == decode_access_token(token)["jti"]).one()
            record.expires_at = datetime.utcnow() - timedelta(days=2)
            db.commit()
            assert prune_expired_refresh_tokens(db) == 0
        finally:
            db.close()
