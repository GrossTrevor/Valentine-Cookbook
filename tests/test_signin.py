"""Sign-in tests (T3c): R13 token lifecycle, R15 rate limits, bot traps and
blocked users on /login.

Characterization tests: they describe what app.py does today and are
expected to pass, except any test marked xfail(strict=True, "finding: ...").
Every refusal has an allowed counterpart, so a pass proves the check, not a
broken route. Client IPs are varied with environ_base REMOTE_ADDR only, never
X-Forwarded-For. Synthetic data only.
"""

import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
import db
from tests.conftest import get_csrf

LINK_REFUSED = (
    "That sign-in link has expired or was already used. "
    "Please request a new one -- it only takes a moment."
)
RATE_LIMITED = (
    "Too many sign-in emails were requested. Please wait an hour, then try again."
)
TOO_FAST = "That was submitted very fast. Please try again."
HONEYPOT = "Submission blocked."
BLOCKED = "That account cannot sign in."
SIGNED_IN = "You are signed in. Welcome!"

_LINK_RE = re.compile(r"https?://\S+/login/(\S+)")


# --- helpers ---------------------------------------------------------------


def _new_client():
    return app_module.app.test_client()


def request_link(client, email, name, ip="10.20.0.1", **extra):
    """POST /login the way the form does, from REMOTE_ADDR `ip`."""
    data = {"csrf": get_csrf(client), "email": email, "full_name": name}
    data.update(extra)
    return client.post("/login", data=data, environ_base={"REMOTE_ADDR": ip})


def token_from(mail):
    match = _LINK_RE.search(mail[2])
    assert match, "no sign-in link in the email body"
    return match.group(1)


def token_rows():
    conn = db.connect()
    try:
        return [
            dict(row) for row in conn.execute("SELECT * FROM login_tokens ORDER BY id")
        ]
    finally:
        conn.close()


def session_user_id(client):
    with client.session_transaction() as sess:
        return sess.get("user_id")


def assert_signed_out(client):
    assert session_user_id(client) is None
    response = client.get("/me")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def naive_utc(delta):
    """The naive UTC ISO format db.now() uses, shifted by `delta`."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now + delta).isoformat(timespec="seconds")


def set_expiry(token, value):
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE login_tokens SET expires_at = ? WHERE token_hash = ?",
            (value, hashlib.sha256(token.encode("utf-8")).hexdigest()),
        )
        conn.commit()
    finally:
        conn.close()


# --- R13: token lifecycle --------------------------------------------------


def test_unused_unexpired_link_signs_the_user_in(client, mailbox, make_user):
    user = make_user("rosalind.testwell@example.com", "Rosalind Testwell")
    response = request_link(client, user.email, user.name)
    assert response.status_code == 200
    assert len(mailbox) == 1
    assert mailbox[0][0] == user.email

    response = client.get("/login/" + token_from(mailbox[0]))
    assert response.status_code == 302

    assert session_user_id(client) == user.id
    me = client.get("/me")
    assert me.status_code == 200
    page = me.get_data(as_text=True)
    assert "Signed in as <strong>Rosalind Testwell</strong>" in page
    assert SIGNED_IN in page


def test_opening_the_same_link_again_is_refused(client, mailbox, make_user):
    user = make_user("otto.replay@example.com", "Otto Replay")
    request_link(client, user.email, user.name)
    link = "/login/" + token_from(mailbox[0])

    first = _new_client()
    assert first.get(link).status_code == 302
    assert session_user_id(first) == user.id

    second = _new_client()
    response = second.get(link, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/login"
    assert LINK_REFUSED in response.get_data(as_text=True)
    assert_signed_out(second)


def test_expired_link_is_refused(client, mailbox, make_user):
    user = make_user("edna.expired@example.com", "Edna Expired")
    request_link(client, user.email, user.name)
    token = token_from(mailbox[0])
    set_expiry(token, naive_utc(timedelta(minutes=-1)))

    fresh = _new_client()
    response = fresh.get("/login/" + token, follow_redirects=True)
    assert response.request.path == "/login"
    assert LINK_REFUSED in response.get_data(as_text=True)
    assert_signed_out(fresh)
    assert token_rows()[0]["used_at"] is None


def test_link_one_minute_before_expiry_still_works(client, mailbox, make_user):
    user = make_user("nell.nearly@example.com", "Nell Nearly")
    request_link(client, user.email, user.name)
    token = token_from(mailbox[0])
    set_expiry(token, naive_utc(timedelta(minutes=1)))

    fresh = _new_client()
    assert fresh.get("/login/" + token).status_code == 302
    assert session_user_id(fresh) == user.id


@pytest.mark.xfail(
    strict=True,
    reason="finding: link still works in the second when now == expires_at "
    "(app uses expires_at < now; design says now >= expires_at is expired)",
)
def test_link_is_refused_at_the_exact_expiry_time(
    client, mailbox, make_user, monkeypatch
):
    user = make_user("ezra.edge@example.com", "Ezra Edge")
    request_link(client, user.email, user.name)
    token = token_from(mailbox[0])
    frozen = "2030-01-01T12:00:00"
    set_expiry(token, frozen)
    monkeypatch.setattr(db, "now", lambda: frozen)

    fresh = _new_client()
    response = fresh.get("/login/" + token, follow_redirects=True)
    assert LINK_REFUSED in response.get_data(as_text=True)
    assert session_user_id(fresh) is None


@pytest.mark.parametrize(
    "kind", ["garbage", "random-unknown", "stored-hash-of-a-real-token"]
)
def test_unknown_or_garbage_token_is_refused(client, mailbox, make_user, kind):
    user = make_user("gus.garbage@example.com", "Gus Garbage")
    request_link(client, user.email, user.name)
    if kind == "garbage":
        token = "not-a-real-token"
    elif kind == "random-unknown":
        token = secrets.token_urlsafe(32)
    else:
        token = token_rows()[0]["token_hash"]

    fresh = _new_client()
    response = fresh.get("/login/" + token, follow_redirects=True)
    assert response.request.path == "/login"
    assert LINK_REFUSED in response.get_data(as_text=True)
    assert_signed_out(fresh)
    assert token_rows()[0]["used_at"] is None


def test_database_stores_only_the_sha256_of_the_token(client, mailbox, make_user):
    user = make_user("hattie.hash@example.com", "Hattie Hash")
    request_link(client, user.email, user.name)
    token = token_from(mailbox[0])

    def check_rows():
        rows = token_rows()
        assert len(rows) == 1
        for column, value in rows[0].items():
            assert token not in str(value), "raw token found in column %s" % column
        assert (
            rows[0]["token_hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
        )
        assert rows[0]["token_hash"] == app_module._hash(token)
        assert rows[0]["user_id"] == user.id
        return rows[0]

    assert check_rows()["used_at"] is None
    assert _new_client().get("/login/" + token).status_code == 302
    assert check_rows()["used_at"] is not None


def test_a_link_signs_in_only_its_own_user(client, mailbox, make_user):
    alice = make_user("alma.owner@example.com", "Alma Owner")
    bob = make_user("boris.other@example.com", "Boris Other")
    request_link(_new_client(), alice.email, alice.name, ip="10.20.1.1")
    request_link(_new_client(), bob.email, bob.name, ip="10.20.1.2")
    assert [mail[0] for mail in mailbox] == [alice.email, bob.email]
    alice_token = token_from(mailbox[0])

    visitor = _new_client()
    assert visitor.get("/login/" + alice_token).status_code == 302
    assert session_user_id(visitor) == alice.id
    page = visitor.get("/me").get_data(as_text=True)
    assert "Signed in as <strong>Alma Owner</strong>" in page
    assert "Boris Other" not in page
    assert bob.email not in page

    by_user = {row["user_id"]: row for row in token_rows()}
    assert by_user[alice.id]["used_at"] is not None
    assert by_user[bob.id]["used_at"] is None


# --- R15: rate limits ------------------------------------------------------


def test_seventh_request_for_one_user_in_an_hour_is_refused(client, mailbox, make_user):
    user = make_user("ruth.ratelimit@example.com", "Ruth Ratelimit")
    # A different IP each time, so only the per-user bucket can trip.
    for n in range(6):
        response = request_link(
            client, user.email, user.name, ip="10.30.0.%d" % (n + 1)
        )
        assert response.status_code == 200
        assert RATE_LIMITED not in response.get_data(as_text=True)
    assert len(mailbox) == 6

    response = request_link(client, user.email, user.name, ip="10.30.0.7")
    assert response.status_code == 200
    assert RATE_LIMITED in response.get_data(as_text=True)
    assert len(mailbox) == 6
    assert len(token_rows()) == 6


def test_twenty_first_request_from_one_ip_is_refused(client, mailbox, make_user):
    shared_ip = "10.40.0.1"
    users = [
        make_user("ida.%d@example.com" % n, "Ida Sharedip%s" % "ABCD"[n])
        for n in range(4)
    ]
    # 6 + 6 + 6 + 2 = 20 requests: no user reaches its own limit of 6 on the 21st.
    plan = [users[0]] * 6 + [users[1]] * 6 + [users[2]] * 6 + [users[3]] * 2
    for user in plan:
        response = request_link(client, user.email, user.name, ip=shared_ip)
        assert RATE_LIMITED not in response.get_data(as_text=True)
    assert len(mailbox) == 20

    response = request_link(client, users[3].email, users[3].name, ip=shared_ip)
    assert response.status_code == 200
    assert RATE_LIMITED in response.get_data(as_text=True)
    assert len(mailbox) == 20
    assert len(token_rows()) == 20

    # Counterpart: the same user from another IP is still allowed.
    response = request_link(client, users[3].email, users[3].name, ip="10.40.0.2")
    assert RATE_LIMITED not in response.get_data(as_text=True)
    assert len(mailbox) == 21


# --- bot traps -------------------------------------------------------------


def _users_with_email(email):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE email = ?", (email,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_submitting_immediately_is_rejected_as_very_fast(client, mailbox):
    email = "fay.fast@example.com"
    response = request_link(client, email, "Fay Fast", started_at=str(int(time.time())))
    assert response.status_code == 200
    assert TOO_FAST in response.get_data(as_text=True)
    assert mailbox == []
    assert token_rows() == []
    assert _users_with_email(email) == 0


def test_submitting_after_a_human_pause_is_allowed(client, mailbox):
    response = request_link(
        client,
        "paula.patient@example.com",
        "Paula Patient",
        started_at=str(int(time.time()) - 5),
    )
    assert TOO_FAST not in response.get_data(as_text=True)
    assert len(mailbox) == 1


def test_filled_honeypot_is_blocked(client, mailbox):
    email = "hank.honeypot@example.com"
    response = request_link(
        client, email, "Hank Honeypot", website="https://spam.example.com"
    )
    assert response.status_code == 200
    assert HONEYPOT in response.get_data(as_text=True)
    assert mailbox == []
    assert token_rows() == []
    assert _users_with_email(email) == 0


# --- blocked user ----------------------------------------------------------


def test_blocked_user_gets_no_link(client, mailbox, make_user):
    user = make_user("bea.blocked@example.com", "Bea Blocked", blocked=True)
    response = request_link(client, user.email, user.name)
    assert response.status_code == 200
    assert BLOCKED in response.get_data(as_text=True)
    assert mailbox == []
    assert token_rows() == []
    assert_signed_out(client)
