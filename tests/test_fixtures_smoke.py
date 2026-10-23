"""Smoke tests for tests/conftest.py itself."""

import os

import db

REPO_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


def _snapshot():
    if not os.path.isdir(REPO_DATA):
        return None
    return sorted(
        (os.path.join(d, f), os.path.getmtime(os.path.join(d, f)))
        for d, _, files in os.walk(REPO_DATA)
        for f in files
    )


_BEFORE = _snapshot()
_marker = {}


def test_sign_in_round_trip(client, mailbox, make_user, sign_in, make_recipe):
    user = make_user("barb@example.com", "Barb Tester")
    sign_in(client, user)
    assert len(mailbox) == 1 and mailbox[0][0] == "barb@example.com"
    assert "sign-in link" in mailbox[0][1]
    assert client.get("/me").status_code == 200
    slug = make_recipe(client, title="Smoke Stew")
    assert client.get("/recipe/" + slug).status_code == 200


def test_isolation_first(make_user):
    make_user("one@example.com", "Person One")
    _marker["dir"] = db.data_dir()
    assert db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_isolation_second():
    assert db.data_dir() != _marker.get("dir")
    assert db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_mailbox_captures_without_sending(client, mailbox, csrf):
    token = csrf(client)
    client.post(
        "/login",
        data={"csrf": token, "email": "new@example.com", "full_name": "New Person"},
    )
    assert [m[0] for m in mailbox] == ["new@example.com"]


def test_admin_and_blocked(make_user):
    make_user("a@example.com", "Admin One", admin=True)
    make_user("b@example.com", "Admin Two", admin=True)
    blocked = make_user("c@example.com", "Blocked One", blocked=True)
    assert os.environ["ADMIN_EMAILS"] == "a@example.com,b@example.com"
    row = (
        db.connect()
        .execute("SELECT is_blocked FROM users WHERE id = ?", (blocked.id,))
        .fetchone()
    )
    assert row["is_blocked"] == 1


def test_no_leaked_env():
    for key in ("RESEND_API_KEY", "SMTP_HOST", "ANTHROPIC_API_KEY", "MODERATION_AI"):
        assert key not in os.environ
    assert os.environ["MAIL_PROVIDER"] == "console"


def test_data_dir_is_not_repo_data():
    assert os.path.realpath(db.data_dir()) != os.path.realpath(REPO_DATA)


def test_fresh_db_gives_new_dir(fresh_db):
    first = fresh_db()
    second = fresh_db()
    assert first != second
    assert os.path.realpath(db.data_dir()) == os.path.realpath(str(second))


def test_repo_data_untouched():
    # Keep last in the file: compares data/ with its state at collection time.
    assert _snapshot() == _BEFORE
