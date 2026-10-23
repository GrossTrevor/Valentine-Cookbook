"""Shared fixtures for the server tests (everything under tests/).

The environment is set up at module top level, before any test module can
`import app`, because importing app.py runs create_app() and db.init().
"""

import os
import re
import tempfile
from types import SimpleNamespace

# --- session-level environment: must run before `import app` ---------------
_SESSION_DIR = tempfile.mkdtemp(prefix="cookbook-session-")
os.environ["DATA_DIR"] = _SESSION_DIR
os.environ["MAIL_PROVIDER"] = "console"
for _key in ("RESEND_API_KEY", "SMTP_HOST", "ANTHROPIC_API_KEY", "MODERATION_AI"):
    os.environ.pop(_key, None)

import pytest  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402

import app as app_module  # noqa: E402
import db  # noqa: E402
import mailer  # noqa: E402

settings.register_profile(
    "cookbook",
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile("cookbook")


# --- isolation -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Fresh DATA_DIR and schema for every test.

    Autouse because test_cookbook.py's PhotoTests.tearDownClass pops DATA_DIR
    globally, which would otherwise fall back to the repo's data/.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAIL_PROVIDER", "console")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    for key in ("RESEND_API_KEY", "SMTP_HOST", "ANTHROPIC_API_KEY", "MODERATION_AI"):
        monkeypatch.delenv(key, raising=False)
    db.init()
    yield tmp_path


@pytest.fixture
def fresh_db(tmp_path_factory, monkeypatch):
    """Returns a function; call it once per hypothesis example for a new
    DATA_DIR + schema, so rate limits don't accumulate across examples."""

    def _fresh():
        path = tmp_path_factory.mktemp("example")
        monkeypatch.setenv("DATA_DIR", str(path))
        db.init()
        return path

    return _fresh


# --- client and mail -------------------------------------------------------


@pytest.fixture
def client():
    # Deliberately not app.testing = True: the real 500 handler must run.
    return app_module.app.test_client()


@pytest.fixture
def mailbox(monkeypatch):
    """List of (to, subject, body) for every mail the app tries to send.

    app.py calls mailer.send(...) and ignores the result; the console
    provider returns False, so this does too.
    """
    sent = []

    def _send(to_address, subject, body):
        sent.append((to_address, subject, body))
        return False

    monkeypatch.setattr(mailer, "send", _send)
    return sent


# --- helpers ---------------------------------------------------------------

_TOKEN_RE = re.compile(r'name="csrf" value="([^"]+)"')


def get_csrf(client, path="/login"):
    """GET a page and extract the session CSRF token. For a signed-in client
    use another path (/login redirects when signed in), e.g. "/add"."""
    response = client.get(path, follow_redirects=True)
    match = _TOKEN_RE.search(response.get_data(as_text=True))
    assert match, "no csrf field on %s" % path
    return match.group(1)


@pytest.fixture
def csrf():
    return get_csrf


@pytest.fixture
def make_user(monkeypatch):
    """make_user(email, name, admin=False, blocked=False) -> SimpleNamespace(id, email, name).

    Inserts the users row exactly as /login does (no rate-limit budget spent);
    sign_in() then uses the real flow. Admin is via ADMIN_EMAILS.
    """

    def _make(email, name, admin=False, blocked=False):
        email = email.strip().lower()
        conn = db.connect()
        conn.execute(
            "INSERT INTO users(email, full_name, created_at) VALUES(?,?,?)",
            (email, name, db.now()),
        )
        if blocked:
            conn.execute("UPDATE users SET is_blocked = 1 WHERE email = ?", (email,))
        conn.commit()
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if admin:
            current = os.environ.get("ADMIN_EMAILS", "")
            monkeypatch.setenv("ADMIN_EMAILS", ",".join(filter(None, [current, email])))
        return SimpleNamespace(id=row["id"], email=email, name=name)

    return _make


@pytest.fixture
def sign_in(mailbox, csrf):
    """sign_in(client, user): POST /login, take the link from the mailbox, GET it.

    Each user gets its own REMOTE_ADDR so the 20/hour per-IP limit is not hit.
    Returns the client. Blocked users are refused by the app (no mail sent).
    """

    def _sign_in(client, user):
        token = csrf(client)
        ip = "10.1.%d.%d" % divmod(user.id % 60000 + 1, 250)
        before = len(mailbox)
        client.post(
            "/login",
            data={"csrf": token, "email": user.email, "full_name": user.name},
            environ_base={"REMOTE_ADDR": ip},
        )
        assert len(mailbox) == before + 1, (
            "sign-in sent no email (blocked or rate limited?)"
        )
        link = re.search(r"https?://\S+/login/\S+", mailbox[-1][2]).group(0)
        response = client.get(link)
        assert response.status_code == 302
        return client

    return _sign_in


@pytest.fixture
def make_recipe(csrf):
    """make_recipe(client, **fields) -> slug. `client` must be signed in.
    Goes through POST /add. Limit: 12 adds per user per hour."""

    def _make(client, **fields):
        data = {
            "title": "Test Soup",
            "ingredients": "1 cup water\n2 tbsp salt",
            "steps": "Boil the water. Add the salt.",
            "servings": "4",
        }
        data.update(fields)
        data["csrf"] = csrf(client, "/add")
        response = client.post("/add", data=data)
        assert response.status_code == 302, (
            "add was rejected: %s" % (response.get_data(as_text=True)[:300])
        )
        return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    return _make
