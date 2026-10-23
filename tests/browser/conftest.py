"""Fixtures for the browser (e2e) tests.

Everything here runs in the pytest process: the app is served by a werkzeug
server on a daemon thread, so the per-test DATA_DIR that tests/conftest.py
sets (the app reads the env var on every connect) reaches the server too.

Safety (R30): mailer.send is captured, urllib.request.urlopen raises and is
counted, and every page request to another origin is aborted and fails the
test.
"""

import re
import threading
import urllib.request
from urllib.parse import urlsplit

import pytest
from werkzeug.serving import make_server

import app as app_module
import mailer


def pytest_collection_modifyitems(items):
    """Everything under tests/browser/ is e2e, so `-m "not e2e"` skips it."""
    for item in items:
        if "tests/browser/" in str(item.path).replace("\\", "/"):
            item.add_marker(pytest.mark.e2e)


# --- live server -------------------------------------------------------------


@pytest.fixture(scope="session")
def live_server():
    """Base URL (http://127.0.0.1:PORT) of the app on an ephemeral port."""
    server = make_server("127.0.0.1", 0, app_module.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def base_url(live_server):
    """Overrides pytest-base-url so page.goto("/x") hits the live server."""
    return live_server


# --- mail and outbound calls (session-wide, so the server thread sees them) --

_MAIL = []
_URLOPEN_CALLS = []


def _capture_send(to_address, subject, body):
    _MAIL.append((to_address, subject, body))
    return False


def _forbidden_urlopen(*args, **kwargs):
    _URLOPEN_CALLS.append(args[0] if args else kwargs.get("url"))
    raise AssertionError("e2e tests must never make outbound HTTP calls: %r" % (args[:1],))


@pytest.fixture(scope="session", autouse=True)
def _stub_outbound():
    patch = pytest.MonkeyPatch()
    patch.setattr(mailer, "send", _capture_send)
    patch.setattr(urllib.request, "urlopen", _forbidden_urlopen)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def sent_mail():
    """List of (to, subject, body) for every mail the server tried to send.
    Cleared per test. Fails the test if the server called urlopen."""
    _MAIL.clear()
    _URLOPEN_CALLS.clear()
    yield _MAIL
    assert not _URLOPEN_CALLS, "server made outbound calls: %r" % _URLOPEN_CALLS


@pytest.fixture
def urlopen_calls():
    """URLs passed to the (raising) urlopen stub during this test."""
    return _URLOPEN_CALLS


# --- off-origin guard --------------------------------------------------------


def origin_of(url):
    parts = urlsplit(url)
    return "%s://%s" % (parts.scheme, parts.netloc)


def request_is_allowed(url, allowed_origin):
    """True for same-origin http(s) requests and for non-network schemes
    (data:, blob:, about:), which never leave the browser."""
    if urlsplit(url).scheme not in ("http", "https", "ws", "wss"):
        return True
    return origin_of(url) == allowed_origin


@pytest.fixture(autouse=True)
def off_origin_requests(context, live_server):
    """Aborts and records any request outside the live server's origin, and
    fails the test at teardown if there was one. Tests that provoke one on
    purpose must clear() the list after asserting on it."""
    violations = []

    def handler(route):
        url = route.request.url
        if request_is_allowed(url, live_server):
            route.continue_()
        else:
            violations.append(url)
            route.abort()

    context.route("**/*", handler)
    yield violations
    assert not violations, "page made off-origin requests: %r" % violations


# --- helpers -----------------------------------------------------------------


def fill_like_a_person(page, form_selector):
    """Backdate the form's hidden started_at by 5 s (it is epoch seconds;
    check_bot_traps rejects forms submitted in under 2 s) so journeys need not
    sleep. Call after filling fields, before clicking submit.

    Needs JavaScript: with JS disabled page.evaluate does not run, so no-JS
    form tests must instead page.wait_for_timeout(2100) before submitting.
    """
    page.evaluate(
        """(selector) => {
            const input = document.querySelector(selector + ' input[name="started_at"]');
            if (!input) throw new Error('no started_at input in ' + selector);
            input.value = String(parseInt(input.value, 10) - 5);
        }""",
        form_selector,
    )


def sign_in_via_browser(page, sent_mail, email, name):
    """Sign in through the real login form and the emailed link."""
    before = len(sent_mail)
    page.goto("/login")
    page.fill("#full_name", name)
    page.fill("#email", email)
    fill_like_a_person(page, "form.signin-form")
    page.click("form.signin-form button[type=submit]")
    assert len(sent_mail) == before + 1, "sign-in sent no mail"
    link = re.search(r"https?://\S+/login/\S+", sent_mail[-1][2]).group(0)
    page.goto(link)


@pytest.fixture
def sign_in(page, sent_mail):
    """sign_in(email, name): browser sign-in via the form and captured link."""

    def _sign_in(email, name):
        sign_in_via_browser(page, sent_mail, email, name)

    return _sign_in
