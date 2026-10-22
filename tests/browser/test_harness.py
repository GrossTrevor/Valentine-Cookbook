"""Tests of the browser harness itself (R30, R31)."""

import os
import socket

import db
from tests.browser.conftest import request_is_allowed


def test_server_binds_loopback_only(live_server):
    assert live_server.startswith("http://127.0.0.1:")
    port = int(live_server.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        pass


def test_home_page_loads_same_origin(page, off_origin_requests):
    response = page.goto("/")
    assert response.status == 200
    assert off_origin_requests == []


def test_off_origin_navigation_is_caught(page, off_origin_requests):
    """The CSP (connect-src 'self') already stops a page's own fetch() before
    it reaches the network, so the guard is exercised with a navigation, which
    CSP does not govern."""
    page.goto("/")
    try:
        page.goto("https://example.org/x")
    except Exception:  # the aborted request surfaces as a navigation error
        pass
    assert off_origin_requests == ["https://example.org/x"]
    off_origin_requests.clear()  # deliberate; otherwise teardown fails the test


def test_request_origin_rules(live_server):
    assert request_is_allowed(live_server + "/x", live_server)
    assert request_is_allowed("data:text/plain,hi", live_server)
    assert not request_is_allowed("https://example.org/x", live_server)
    assert not request_is_allowed("http://127.0.0.1:1/x", live_server)
    assert not request_is_allowed("http://localhost:%s/" % live_server.rsplit(":", 1)[1], live_server)


def test_sign_in_sends_no_real_mail(sign_in, sent_mail, urlopen_calls):
    sign_in("harness@example.com", "Harness Tester")
    assert len(sent_mail) == 1
    assert sent_mail[0][0] == "harness@example.com"
    assert urlopen_calls == []


def test_db_is_isolated_per_test_a(sign_in):
    sign_in("isolation@example.com", "Isolation One")
    assert _user_count() == 1


def test_db_is_isolated_per_test_b():
    assert _user_count() == 0
    assert os.environ["DATA_DIR"] in str(db.connect().execute("PRAGMA database_list").fetchone()[2])


def _user_count():
    return db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0]
