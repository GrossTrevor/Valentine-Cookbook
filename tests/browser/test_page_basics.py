"""Page basics in a real browser (R27, R28, R29). Characterization tests.

R27: with JavaScript off, a recipe page still shows its ingredients and
amounts, and the add form still submits.
R28: A+ makes the text bigger and that survives a reload (app.js keeps it in
localStorage under "vc-text-size" and sets <html data-size>); A- makes it
smaller. There is no reset button.
R29: every button and link in the recipe page's scaler ([data-scaler], the
real attribute in recipe.html) and in any <header> (the site header and the
recipe's <header class="recipe-head">) is at least 44x44 CSS px.

All names and emails are synthetic.
"""

import re

import pytest

from tests.browser.conftest import fill_like_a_person, request_is_allowed

MIN_TARGET = 44  # CSS px, both ways

RECIPE = {
    "title": "Synthetic Oat Squares",
    "ingredients": "3/4 cup rolled oats\n2 tbsp honey\n1 pinch salt",
    "steps": "Stir everything together.\nPress into a pan and chill.",
    "servings": "8",
    "meal": "Dessert",
    "cuisine": "Testland",
    "tags": "make ahead, no bake",
}


# --- helpers -----------------------------------------------------------------


def add_recipe_with_js(page, fields):
    """Fill /add in a JS-enabled page and submit; returns the recipe URL."""
    page.goto("/add")
    for name, value in fields.items():
        page.fill("#%s" % name, value)
    fill_like_a_person(page, "form[data-recipe-form]")
    page.click("form[data-recipe-form] button[type=submit]")
    page.wait_for_url(re.compile(r"/recipe/[^/]+$"))
    return page.url


def root_font_px(page):
    return float(
        page.evaluate("getComputedStyle(document.documentElement).fontSize")[:-2]
    )


def body_font_px(page):
    return float(page.evaluate("getComputedStyle(document.body).fontSize")[:-2])


@pytest.fixture
def no_js_page(browser, live_server):
    """A page in its own context with JavaScript disabled. The autouse
    off_origin_requests guard only covers the default context, so this one
    has the same guard: off-origin requests are aborted, recorded and fail
    the test at teardown."""
    violations = []
    context = browser.new_context(java_script_enabled=False, base_url=live_server)

    def handler(route):
        url = route.request.url
        if request_is_allowed(url, live_server):
            route.continue_()
        else:
            violations.append(url)
            route.abort()

    context.route("**/*", handler)
    page = context.new_page()
    page.off_origin_requests = violations
    try:
        yield page
    finally:
        context.close()
    assert not violations, "no-JS page made off-origin requests: %r" % violations


@pytest.fixture
def recipe_url(page, sign_in):
    """A synthetic recipe with servings, course, cuisine and tags, so the
    scaler's servings stepper and the recipe header's pills all render."""
    sign_in("page.basics@example.com", "Pat Basics")
    url = add_recipe_with_js(page, RECIPE)
    page.goto("/login")  # leave the recipe page; tests navigate themselves
    return url


# --- R27: works without JavaScript --------------------------------------------


def test_recipe_shows_ingredients_and_amounts_without_js(recipe_url, no_js_page):
    page = no_js_page
    response = page.goto(recipe_url)
    assert response.status == 200

    # JS really is off: the A+ button does nothing.
    page.click("[data-text-size=up]")
    assert page.locator("html").get_attribute("data-size") is None

    amounts = page.locator("[data-ingredients] .amount").all_inner_texts()
    items = page.locator("[data-ingredients] .ing-item").all_inner_texts()
    assert amounts[0] == "3/4 cup"
    assert items[0] == "rolled oats"
    assert len(amounts) == 3
    assert "honey" in items
    assert all(text.strip() for text in amounts)
    assert page.off_origin_requests == []


def test_add_form_submits_without_js(no_js_page, sent_mail):
    page = no_js_page

    # Sign in with JS off: the login form, then the link from the mail.
    page.goto("/login")
    page.fill("#full_name", "Nora Nojs")
    page.fill("#email", "no.js@example.com")
    page.wait_for_timeout(2100)  # started_at can't be backdated without JS
    page.click("form.signin-form button[type=submit]")
    assert len(sent_mail) == 1
    link = re.search(r"https?://\S+/login/\S+", sent_mail[0][2]).group(0)
    page.goto(link)
    assert page.locator("header.site-header").get_by_text("Sign out").is_visible()

    page.goto("/add")
    page.fill("#title", "Plain Toast Without Scripts")
    page.fill("#ingredients", "3/4 cup milk\n2 slices bread")
    page.fill("#steps", "Toast the bread.\nPour the milk.")
    page.wait_for_timeout(2100)
    page.click("form[data-recipe-form] button[type=submit]")

    page.wait_for_url(re.compile(r"/recipe/[^/]+$"))
    assert "Plain Toast Without Scripts" in page.locator("h1").inner_text()
    assert page.locator("[data-ingredients] .amount").all_inner_texts() == [
        "3/4 cup",
        "2 slices",
    ]
    assert page.locator("[data-ingredients] .ing-item").all_inner_texts() == [
        "milk",
        "bread",
    ]
    assert page.off_origin_requests == []


# --- R28: text size -------------------------------------------------------------


def test_a_plus_grows_text_and_persists_after_reload(page):
    page.goto("/")
    root_before, body_before = root_font_px(page), body_font_px(page)

    page.click("[data-text-size=up]")
    root_after, body_after = root_font_px(page), body_font_px(page)
    assert root_after > root_before
    assert body_after > body_before

    page.reload()
    assert root_font_px(page) == root_after
    assert body_font_px(page) == body_after


def test_a_minus_shrinks_text_and_persists_after_reload(page):
    page.goto("/")
    root_before, body_before = root_font_px(page), body_font_px(page)

    page.click("[data-text-size=down]")
    root_after, body_after = root_font_px(page), body_font_px(page)
    assert root_after < root_before
    assert body_after < body_before

    page.reload()
    assert root_font_px(page) == root_after
    assert body_font_px(page) == body_after


def test_a_plus_then_a_minus_returns_to_the_default_size(page):
    page.goto("/")
    default = root_font_px(page)
    page.click("[data-text-size=up]")
    page.click("[data-text-size=down]")
    assert root_font_px(page) == default


# --- R29: 44x44 targets -----------------------------------------------------------
#
# R29's selector `[data-scaler] button, header a, header button` is split into
# groups so a too-small target in one group doesn't hide the others. Together
# they are exactly that selector: header.site-header and header.recipe-head
# are the only <header> elements on the recipe page, and
# test_target_groups_cover_the_spec_selector checks that the counts add up.
# The page is measured signed in (recipe_url signs in to add the recipe), so
# the site header shows "My Recipes" and the "Sign out" button; a visitor's
# "Sign in" link has the same .btn class as "Help".

GROUPS = {
    "scaler": "[data-scaler] button",
    "site header nav": "header.site-header a:not(.brand), header.site-header button",
    "site header brand": "header.site-header a.brand",
    "recipe header": "header.recipe-head a, header.recipe-head button",
}
SPEC_SELECTOR = "[data-scaler] button, header a, header button"
VIEWPORTS = {
    "default": None,  # pytest-playwright's default context viewport (1280x720)
    "phone": {"width": 390, "height": 844},
}
# Findings, measured on 61b44b1. Strict, so a fix turns these red until the
# mark is removed.
FINDINGS = {
    ("site header brand", "default"): "finding: header.site-header a.brand is 246.8x41",
    ("site header brand", "phone"): "finding: header.site-header a.brand is 220.5x36.5",
    ("recipe header", "default"): "finding: header.recipe-head a.pill is ~80x31.5",
    ("recipe header", "phone"): "finding: header.recipe-head a.pill is ~80x31.5",
}


def target_cases():
    for group in GROUPS:
        for viewport in VIEWPORTS:
            reason = FINDINGS.get((group, viewport))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            yield pytest.param(
                group, viewport, marks=marks, id="%s-%s" % (group, viewport)
            )


def too_small_targets(page, selector):
    """(description, WxH) for each visible match smaller than 44x44, and how
    many visible matches were measured. Elements that aren't visible are
    skipped: nobody can tap them, and bounding_box() has nothing to measure."""
    measured, small = 0, []
    for element in page.locator(selector).all():
        if not element.is_visible():
            continue
        box = element.bounding_box()
        measured += 1
        if box["width"] < MIN_TARGET or box["height"] < MIN_TARGET:
            tag = element.evaluate("e => e.tagName.toLowerCase()")
            label = (
                element.get_attribute("aria-label") or element.inner_text()
            ).strip()
            small.append(
                (
                    "%s %r" % (tag, label),
                    "%gx%g" % (round(box["width"], 1), round(box["height"], 1)),
                )
            )
    return measured, small


@pytest.mark.parametrize("group, viewport", list(target_cases()))
def test_targets_are_at_least_44px(page, recipe_url, group, viewport):
    if VIEWPORTS[viewport]:
        page.set_viewport_size(VIEWPORTS[viewport])
    page.goto(recipe_url)
    measured, small = too_small_targets(page, GROUPS[group])
    assert measured > 0, "nothing visible matched %r" % GROUPS[group]
    assert small == [], "targets under 44x44 in %s (%s): %r" % (group, viewport, small)


def test_target_groups_cover_the_spec_selector(page, recipe_url):
    page.goto(recipe_url)
    spec_count = page.locator(SPEC_SELECTOR).count()
    group_count = sum(page.locator(sel).count() for sel in GROUPS.values())
    assert spec_count > 0
    assert group_count == spec_count
