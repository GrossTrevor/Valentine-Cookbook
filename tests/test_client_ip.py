"""Client IP for rate limiting (R35-R37).

R35: the client IP is the TRUSTED_PROXY_HOPS-th X-Forwarded-For entry from
the right (default 1); fewer entries than that means the socket address.
Invalid, zero or negative values behave as 1 and are logged once at startup.
R36: a client-supplied X-Forwarded-For prefix never changes the result.
R37: a sign-in request logs the computed IP and the entry count, never the
email address.

Red by design until T6 lands: the current client_ip() takes the first entry.
"""

import logging
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

import app

SOCKET = "192.0.2.77"  # TEST-NET-1, the "socket" address in every case


def ip_for(xff=None, remote=SOCKET):
    headers = {} if xff is None else {"X-Forwarded-For": xff}
    with app.app.test_request_context(
        "/", headers=headers, environ_base={"REMOTE_ADDR": remote}
    ):
        return app.client_ip()


@pytest.fixture
def default_hops(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)


# --- R35: default of one trusted hop ---------------------------------------


def test_default_single_entry_is_used(default_hops):
    assert ip_for("203.0.113.5") == "203.0.113.5"


def test_default_takes_rightmost_entry(default_hops):
    assert ip_for("198.51.100.1, 203.0.113.5") == "203.0.113.5"


def test_default_takes_rightmost_of_many(default_hops):
    assert ip_for("1.2.3.4, 198.51.100.1, 203.0.113.5") == "203.0.113.5"


def test_no_header_gives_socket_address(default_hops):
    assert ip_for(None) == SOCKET


# --- R35: TRUSTED_PROXY_HOPS=2 ---------------------------------------------


def test_two_hops_takes_second_from_right(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ip_for("198.51.100.1, 203.0.113.5, 10.0.0.2") == "203.0.113.5"


def test_two_hops_with_exactly_two_entries(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ip_for("203.0.113.5, 10.0.0.2") == "203.0.113.5"


def test_two_hops_no_header_gives_socket_address(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ip_for(None) == SOCKET


# --- R35: fewer entries than hops ------------------------------------------


def test_one_entry_with_two_hops_gives_socket_address(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ip_for("203.0.113.5") == SOCKET


def test_two_entries_with_three_hops_gives_socket_address(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
    assert ip_for("198.51.100.1, 203.0.113.5") == SOCKET


# --- R35: whitespace and empty entries -------------------------------------


def test_whitespace_and_empty_entries_are_ignored(default_hops):
    assert ip_for("  198.51.100.1 , , 203.0.113.5 ,  ") == "203.0.113.5"


def test_whitespace_and_empty_entries_with_two_hops(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ip_for(" 198.51.100.1 ,, ,203.0.113.5 , ") == "198.51.100.1"


def test_only_empty_entries_gives_socket_address(default_hops):
    assert ip_for(" , ,, ") == SOCKET


# --- R35: invalid values behave as 1 ---------------------------------------

BAD_HOPS = ["0", "-1", "-5", "abc", "two", "1.5"]


@pytest.mark.parametrize("value", BAD_HOPS)
def test_bad_hops_value_behaves_as_one(monkeypatch, value):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    assert ip_for("1.2.3.4, 198.51.100.1, 203.0.113.5") == "203.0.113.5"


@pytest.mark.parametrize("value", BAD_HOPS)
def test_bad_hops_value_without_header_gives_socket(monkeypatch, value):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    assert ip_for(None) == SOCKET


@pytest.mark.parametrize("value", ["0", "-2", "abc"])
def test_bad_hops_value_logged_once_at_startup(monkeypatch, caplog, value):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    caplog.set_level(logging.INFO)
    app.create_app()
    all_lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("mail:") for m in all_lines), "caplog missed the startup log"
    lines = [
        r.getMessage() for r in caplog.records if "TRUSTED_PROXY_HOPS" in r.getMessage()
    ]
    assert len(lines) == 1, lines


# --- R36: a client-supplied prefix cannot move the bucket -------------------

_junk = st.text(
    alphabet=st.characters(
        min_codepoint=0x20, max_codepoint=0x7E, exclude_characters=","
    ),
    max_size=20,
)
_entry = st.one_of(st.ip_addresses().map(str), _junk)


@given(
    data=st.data(),
    hops=st.integers(min_value=1, max_value=3),
    prefix=st.lists(_entry, max_size=6),
)
def test_client_prefix_does_not_change_ip(monkeypatch, data, hops, prefix):
    suffix = data.draw(
        st.lists(st.ip_addresses(v=4).map(str), min_size=hops, max_size=hops),
        label="proxy-appended",
    )
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", str(hops))
    assert ip_for(", ".join(prefix + suffix)) == ip_for(", ".join(suffix))


# --- R37: sign-in log line ------------------------------------------------

IP_LINE = re.compile(r"\bip=203\.0\.113\.9(?![\d.])")
COUNT_LINE = re.compile(r"\bforwarded_entries=2(?!\d)")


def test_sign_in_logs_ip_and_entry_count_without_email(
    client, csrf, mailbox, caplog, default_hops
):
    email = "quillon.marbury@example.com"
    local_part = email.split("@")[0]
    token = csrf(client)
    caplog.set_level(logging.INFO)
    caplog.clear()

    response = client.post(
        "/login",
        data={"csrf": token, "email": email, "full_name": "Quillon Marbury"},
        headers={"X-Forwarded-For": "198.51.100.44, 203.0.113.9"},
        environ_base={"REMOTE_ADDR": "10.20.30.40"},
    )

    assert response.status_code == 200
    assert len(mailbox) == 1, "sign-in did not send its email"
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        IP_LINE.search(m) and COUNT_LINE.search(m) for m in messages
    ), messages
    for message in messages:
        assert email not in message.lower(), message
        assert local_part not in message.lower(), message
