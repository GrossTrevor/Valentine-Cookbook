"""R14: the redirect after sign-in and safe_referrer() never leave the site.

"On site" is judged the way a browser would, on the Location header as it
goes out on the wire (after Werkzeug's own IRI-to-URI step): WHATWG-like
resolution against http://localhost/ (strip tab/newline, treat a backslash as
a slash, a leading // is scheme-relative), then require scheme http, host
localhost and no userinfo.

How `next` is stored: the real mechanism is require_login(), which saves
request.full_path of a signed-out GET to /add, /me or /recipe/<slug>/edit in
session["next_url"]. That can't carry arbitrary strings, so the properties
set session["next_url"] with client.session_transaction(), and one test
checks the real mechanism end to end.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import app as app_module
import db

TEST_HOST = "localhost"
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
_C0_AND_SPACE = "".join(chr(i) for i in range(0x21))


def resolves_on_site(location, host=TEST_HOST):
    """True when a browser on http://<host>/ following `location` stays on
    http://<host>/ (port 80, no userinfo). Unparseable targets count as off-site."""
    url = location.strip(_C0_AND_SPACE)
    url = url.replace("\t", "").replace("\n", "").replace("\r", "")
    match = _SCHEME.match(url)
    if match:
        if match.group(1).lower() != "http":
            return False
        # Same special scheme as the base: the rest resolves like a relative URL.
        url = url[match.end() :]
    url = url.replace("\\", "/")  # http is a special scheme
    if not url.startswith("//"):
        return True  # path, query or fragment on the base host
    authority = re.split(r"[/?#]", url.lstrip("/"), maxsplit=1)[0]
    if "@" in authority or authority.startswith("["):
        return False
    name, _, port = authority.partition(":")
    return unquote(name).lower() == host and port in ("", "80")


# --- the judge itself --------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "/",
        "/ok/path?x=1",
        "/%2F%2Fevil.example",
        "/%5Cevil.example",
        "ok",
        "?q=1",
        "#top",
        "http://localhost/x",
        "http://LOCALHOST:80/x",
        "http:/x",
        "",
    ],
)
def test_judge_accepts_on_site(location):
    assert resolves_on_site(location)


@pytest.mark.parametrize(
    "location",
    [
        "//evil.example",
        "///evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "/\t/evil.example",
        "/\n/evil.example",
        " //evil.example",
        "http:/\\evil.example",
        "https://evil.example",
        "https://localhost/",
        "http://evil.example/",
        "http://localhost.evil.example/",
        "http://localhost@evil.example/",
        "http://evil.example@localhost/",
        "http://localhost:8080/",
        "//evil%2Eexample",
        "javascript:alert(1)",
        "data:text/html,x",
        "//",
    ],
)
def test_judge_rejects_off_site(location):
    assert not resolves_on_site(location)


# --- redirect after sign-in --------------------------------------------------

EXPLICIT_NEXT = [
    "//evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "https://evil.example",
    "/%2F%2Fevil.example",
    "/\t/evil.example",
    "javascript:alert(1)",
    "/ok/path?x=1",
]
_NEXT_PARTS = st.sampled_from(
    [
        "/",
        "\\",
        "\t",
        "\n",
        "\r",
        " ",
        "%2F",
        "%5C",
        "%09",
        "@",
        ":",
        "?",
        "#",
        ".",
        "evil.example",
        "localhost",
        "http:",
        "https:",
        "javascript:",
        "x",
    ]
)
next_paths = st.one_of(
    # "/", a few chars a browser or Werkzeug may drop or read as "/", then "/host"
    st.text(alphabet="\t\\\n x", max_size=3).map(lambda s: "/" + s + "/evil.example"),
    st.lists(_NEXT_PARTS, max_size=8).map("".join),
    st.text(max_size=30),
    st.text(max_size=30).map(lambda s: "/" + s),
)
# Each example is a few ms, so look harder than the default 50.
MANY = settings(max_examples=300)


def _add_token(user_id):
    token = secrets.token_urlsafe(16)
    conn = db.connect()
    conn.execute(
        "INSERT INTO login_tokens(user_id, token_hash, created_at, expires_at) "
        "VALUES(?,?,?,?)",
        (
            user_id,
            app_module._hash(token),
            db.now(),
            (datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10)).isoformat(
                timespec="seconds"
            ),
        ),
    )
    conn.commit()
    return token


def _location(response):
    return response.headers.get("Location")


@pytest.mark.parametrize("next_url", EXPLICIT_NEXT)
def test_sign_in_redirect_explicit(next_url, make_user, mailbox, csrf):
    """Real flow: POST /login, link from the mailbox, GET it."""
    user = make_user("redirect@example.com", "Rosa Redirect")
    client = app_module.app.test_client()
    client.post(
        "/login",
        data={"csrf": csrf(client), "email": user.email},
        environ_base={"REMOTE_ADDR": "10.9.0.1"},
    )
    link = re.search(r"https?://\S+/login/\S+", mailbox[-1][2]).group(0)
    with client.session_transaction() as session:
        session["next_url"] = next_url
    response = client.get(link)
    assert response.status_code == 302
    assert resolves_on_site(_location(response)), (next_url, _location(response))


@MANY
@given(next_url=next_paths)
@example(next_url="//evil.example")
@example(next_url="/\\evil.example")
@example(next_url="\\\\evil.example")
@example(next_url="https://evil.example")
@example(next_url="/%2F%2Fevil.example")
@example(next_url="/\t/evil.example")
@example(next_url="javascript:alert(1)")
@example(next_url="/ok/path?x=1")
def test_sign_in_redirect_stays_on_site(make_user, next_url):
    user_id = _user_id(make_user)  # one user per test; a fresh token per example
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["next_url"] = next_url
    response = client.get("/login/" + _add_token(user_id))
    location = _location(response)
    # A CR/LF in next_url makes Werkzeug refuse the header (500, no redirect):
    # that stays on site, so R14 only judges responses that do redirect.
    if location is not None:
        assert resolves_on_site(location), (next_url, location)


def _user_id(make_user):
    row = (
        db.connect()
        .execute("SELECT id FROM users WHERE email = ?", ("prop@example.com",))
        .fetchone()
    )
    return row["id"] if row else make_user("prop@example.com", "Pat Property").id


def test_real_next_mechanism_returns_to_page(make_user, mailbox, csrf):
    """require_login() stores full_path; sign-in sends the user back there."""
    user = make_user("returner@example.com", "Ruth Returner")
    client = app_module.app.test_client()
    assert client.get("/add").status_code == 302
    with client.session_transaction() as session:
        assert session["next_url"].startswith("/add")
    client.post(
        "/login",
        data={"csrf": csrf(client), "email": user.email},
        environ_base={"REMOTE_ADDR": "10.9.0.2"},
    )
    link = re.search(r"https?://\S+/login/\S+", mailbox[-1][2]).group(0)
    location = _location(client.get(link))
    assert location.startswith("/add") and resolves_on_site(location)


# --- safe_referrer() ---------------------------------------------------------

_REFERER_PARTS = st.sampled_from(
    [
        "http://",
        "https://",
        "//",
        "localhost",
        "evil.example",
        ".",
        "@",
        ":",
        "80",
        "/",
        "\\",
        "\t",
        " ",
        "%2F",
        "%40",
        "?",
        "#",
        "x",
    ]
)
referers = st.one_of(
    st.lists(_REFERER_PARTS, max_size=8).map("".join),
    st.text(max_size=30).map(lambda s: "http://localhost" + s),
    st.text(max_size=30),
).filter(lambda s: "\n" not in s and "\r" not in s)  # not sendable in a header


@MANY
@given(referer=referers)
@example(referer="http://localhost.evil.example/")
@example(referer="http://localhost@evil.example/")
@example(referer="http://evil.example/http://localhost/")
@example(referer="http://localhost/\t/evil.example")
def test_safe_referrer_never_off_site(referer):
    with app_module.app.test_request_context(headers={"Referer": referer}):
        target = app_module.safe_referrer()
    assert resolves_on_site(target), (referer, target)


@pytest.mark.parametrize(
    "referer",
    [
        "http://evil.example/",
        "http://localhost.evil.example/",
        "http://localhost@evil.example/",
        "//evil.example/",
        "http://localhost/recipe/x",
    ],
)
def test_bad_csrf_post_redirects_on_site(client, referer):
    response = client.post(
        "/login", data={"email": "nobody@example.com"}, headers={"Referer": referer}
    )
    assert response.status_code == 302
    assert resolves_on_site(_location(response)), (referer, _location(response))
