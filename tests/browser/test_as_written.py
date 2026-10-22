"""R21: at factor 1 the recipe page shows the server's amount text.

The server (no JavaScript) renders every amount with units.py. static/app.js
re-renders amounts in the browser, and at factor 1 (page load, "As written",
or the servings stepper back to the original count) it must show exactly the
text the server rendered, for every ingredient: the `.amount` text and, for
unitless counted items, the item text that apply() rewrites.

Red by design until T9: today app.js applies its friendly-unit conversion at
x1 too (requirements.md, open question 1).
"""

import re

import pytest

import app as app_module
import units
from tests.browser.conftest import request_is_allowed
from tests.conftest import get_csrf

# The five lines open question 1 measured as different at x1.
TABLE_MISMATCHES = [
    "1/2 gallon milk",
    "1/3 tbsp sugar",
    "1/16 tsp salt",
    "0.1 tsp salt",
    "0.2 cup water",
]
# Lines that already match, plus a range, plus unitless counted items whose
# item text apply() rewrites (and stores in data-item).
ALREADY_MATCHING = [
    "1 cup flour",
    "2 1/4 cups sugar",
    "2-3 cups flour",
    "1 egg",
    "3 eggs",
]
_QUANTITIES = [
    "1/4",
    "1/3",
    "1/2",
    "2/3",
    "3/4",
    "1",
    "1 1/2",
    "2",
    "3/8",
    "0.2",
    "2 1/4",
    "5",
]


def _generated_lines():
    """Two lines per unit in units.UNIT_NAMES, quantities cycled from a fixed
    list, each checked to parse to the unit it was written for."""
    lines = []
    for index, (unit, (singular, plural)) in enumerate(
        sorted(units.UNIT_NAMES.items())
    ):
        for offset in (0, 1):
            qty = _QUANTITIES[(2 * index + offset) % len(_QUANTITIES)]
            line = "%s %s rice" % (qty, plural if offset else singular)
            parsed = units.parse_ingredient(line)
            assert parsed["unit"] == unit and parsed["qty"], line
            lines.append(line)
    return lines


LINES = TABLE_MISMATCHES + ALREADY_MATCHING + _generated_lines()

_COLLECT = """() => Array.from(document.querySelectorAll('.recipe .amount')).map(n => [
    n.textContent,
    n.parentNode.querySelector('.ing-item').textContent,
    n.classList.contains('changed'),
])"""


def _add_recipe(sent_mail):
    """Sign in with the server test client (same DATA_DIR as the live server)
    and save one recipe holding every line in LINES. Returns its path."""
    client = app_module.app.test_client()
    client.post(
        "/login",
        data={
            "csrf": get_csrf(client),
            "email": "as.written@example.com",
            "full_name": "Ada Testcook",
        },
        environ_base={"REMOTE_ADDR": "10.9.0.1"},
    )
    link = re.search(r"https?://\S+/login/\S+", sent_mail[-1][2]).group(0)
    assert client.get(link).status_code == 302
    response = client.post(
        "/add",
        data={
            "csrf": get_csrf(client, "/add"),
            "title": "As Written Check",
            "ingredients": "\n".join(LINES),
            "steps": "Mix everything together.",
            "servings": "4",
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)[:300]
    return "/recipe/" + response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


def _rows(page):
    rows = page.evaluate(_COLLECT)
    assert len(rows) == len(LINES), "expected one .amount per saved line"
    return rows


def _server_text(browser, live_server, path):
    """Each line's (amount, item) text from the page loaded with JS off."""
    context = browser.new_context(java_script_enabled=False, base_url=live_server)
    blocked = []

    def guard(route):
        if request_is_allowed(route.request.url, live_server):
            route.continue_()
        else:
            blocked.append(route.request.url)
            route.abort()

    context.route("**/*", guard)
    try:
        page = context.new_page()
        assert page.goto(path).status == 200
        amounts = page.locator(".recipe .amount").all_text_contents()
        items = page.locator(".recipe .amount + .ing-item").all_text_contents()
    finally:
        context.close()
    assert not blocked, "no-JS page made off-origin requests: %r" % blocked
    assert len(amounts) == len(items) == len(LINES), (
        "expected one .amount per saved line"
    )
    return list(zip(amounts, items, strict=True))


def _assert_matches_server(server, rows, when):
    mismatches = [
        "%-22s no-JS %-28r JS %r"
        % (line, "%s | %s" % expected, "%s | %s" % (amount, item))
        for line, expected, (amount, item, _changed) in zip(
            LINES, server, rows, strict=True
        )
        if (amount, item) != expected
    ]
    changed = [line for line, row in zip(LINES, rows, strict=True) if row[2]]
    assert not mismatches, (
        "%s: %d line(s) differ from the server text (amount | item):\n%s"
        % (when, len(mismatches), "\n".join(mismatches))
    )
    assert not changed, "%s: amounts still marked .changed: %r" % (when, changed)


@pytest.fixture
def recipe(sent_mail, browser, live_server):
    path = _add_recipe(sent_mail)
    return path, _server_text(browser, live_server, path)


def test_page_load_shows_server_text(page, recipe):
    path, server = recipe
    page.goto(path)
    _assert_matches_server(server, _rows(page), "on page load (JS on)")


def test_as_written_after_double_restores_server_text(page, recipe):
    path, server = recipe
    page.goto(path)
    page.click("[data-scaler] button[data-factor='2']")
    doubled = _rows(page)
    assert all(row[2] for row in doubled), "x2 should mark every amount as changed"
    assert doubled[LINES.index("1 cup flour")][0] == "2 cups"
    assert doubled[LINES.index("1 egg")][1] == "eggs"

    page.get_by_role("button", name="As written").click()
    _assert_matches_server(server, _rows(page), "after x2 then As written")


def test_servings_stepper_back_to_original_restores_server_text(page, recipe):
    path, server = recipe
    page.goto(path)
    page.get_by_role("button", name="More servings").click()
    assert page.locator("[data-serving-output]").text_content() == "5"
    assert page.locator(".recipe .amount.changed").count() == len(LINES)

    page.get_by_role("button", name="Fewer servings").click()
    assert page.locator("[data-serving-output]").text_content() == "4"
    _assert_matches_server(server, _rows(page), "after servings 4 -> 5 -> 4")
