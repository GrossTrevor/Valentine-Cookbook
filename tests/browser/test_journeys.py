"""Journeys in a real browser (R24, R25, R26). Characterization tests.

R24: a signed-out visitor browses, opens a recipe, finds a recipe by the name
it is credited to (someone with no account), and narrows the list with the
course, kind-of-food and tag chips and the "Has this ingredient" box.
R25: a family member signs in with the emailed link, adds a recipe with a
photo, sees it titled "... à la <credit>" with "Typed in by <name>", scales
it x2 and back to "As written", and uses the servings stepper.
R26: a second family member sees no edit or delete controls on that recipe
and can leave a note; the submitter then edits and deletes it.

All names, emails and recipes are synthetic.
"""

import re

import pytest
from PIL import Image

import app as app_module
from tests.browser.conftest import (
    fill_like_a_person,
    request_is_allowed,
    sign_in_via_browser,
)
from tests.conftest import get_csrf

RECIPE_URL = re.compile(r"/recipe/[^/#?]+$")
DELETE_FORM = "form[data-confirm][action^='/recipe/'][action$='/delete']"
GENERATED_PHOTO = re.compile(r"^/photo/[0-9a-f]{32}\.jpg$")


# --- helpers -----------------------------------------------------------------


def card_titles(page):
    return sorted(page.locator(".cards h3 a").all_inner_texts())


def heading(page):
    """The h1 as one line of text, the way a screen reader reads it (the
    "à la" span is styled onto its own line, so inner_text splits it)."""
    return " ".join(page.locator("h1").text_content().split())


def flashes(page):
    return page.locator(".flashes .flash").all_inner_texts()


def amounts(page):
    return page.locator("[data-ingredients] .amount").all_inner_texts()


def add_recipe_in_browser(page, fields, photo=None):
    """Fill /add like a person and submit; returns the recipe page's path."""
    page.goto("/add")
    for name, value in fields.items():
        page.fill("#%s" % name, value)
    if photo is not None:
        page.set_input_files("#photos", str(photo))
    fill_like_a_person(page, "form[data-recipe-form]")
    page.click("form[data-recipe-form] button[type=submit]")
    page.wait_for_url(RECIPE_URL)
    return "/recipe/" + page.url.rsplit("/", 1)[-1]


def seed_recipes_with_test_client(sent_mail, email, name, recipes):
    """Sign in with the server test client (same DATA_DIR as the live
    server) and save each recipe through the real /add route."""
    client = app_module.app.test_client()
    client.post(
        "/login",
        data={"csrf": get_csrf(client), "email": email, "full_name": name},
        environ_base={"REMOTE_ADDR": "10.9.1.1"},
    )
    link = re.search(r"https?://\S+/login/\S+", sent_mail[-1][2]).group(0)
    assert client.get(link).status_code == 302
    for fields in recipes:
        response = client.post("/add", data=dict(fields, csrf=get_csrf(client, "/add")))
        assert response.status_code == 302, response.get_data(as_text=True)[:300]


@pytest.fixture
def guarded_context(browser, live_server):
    """Another browser context (a second person's browser) with the same
    off-origin guard as the default one."""
    violations = []
    context = browser.new_context(base_url=live_server)

    def handler(route):
        if request_is_allowed(route.request.url, live_server):
            route.continue_()
        else:
            violations.append(route.request.url)
            route.abort()

    context.route("**/*", handler)
    try:
        yield context
    finally:
        context.close()
    assert not violations, "second context made off-origin requests: %r" % violations


# --- R24: visitor ------------------------------------------------------------

VISITOR_RECIPES = [
    {
        "title": "Plum Kuchen",
        "credited_to": "Great-Grandma Ottilie",
        "meal": "Dessert",
        "cuisine": "German",
        "tags": "holiday, baking",
        "ingredients": "2 cups flour\n6 plums\n1/2 cup sugar",
        "steps": "Slice the plums.\nBake on the dough.",
        "servings": "8",
    },
    {
        "title": "Weeknight Chili",
        "meal": "Dinner",
        "cuisine": "Tex-Mex",
        "tags": "spicy, make ahead",
        "ingredients": "1 lb ground beef\n2 cans beans\n1 tbsp chili powder",
        "steps": "Brown the beef.\nSimmer everything.",
        "servings": "6",
    },
    {
        "title": "Lemon Bars",
        "meal": "Dessert",
        "cuisine": "American",
        "tags": "baking, potluck",
        "ingredients": "1 cup flour\n3 lemons\n1 cup sugar",
        "steps": "Bake the crust.\nPour on the lemon filling and bake again.",
        "servings": "16",
    },
    {
        "title": "Green Salad",
        "meal": "Lunch",
        "cuisine": "American",
        "tags": "quick",
        "ingredients": "1 head lettuce\n2 tbsp olive oil",
        "steps": "Tear the lettuce.\nToss with the oil.",
        "servings": "2",
    },
]
ALL_TITLES = sorted(r["title"] for r in VISITOR_RECIPES)


def chip(page, row_label, name):
    """A filter chip link in the row labelled row_label (chips may end with a
    count, e.g. "Dessert 2")."""
    row = page.locator(
        ".filter-row", has=page.locator(".filter-label", has_text=row_label)
    )
    return row.locator("a.chip").filter(
        has_text=re.compile(r"^\s*%s\b" % re.escape(name))
    )


def test_visitor_browses_searches_by_credited_name_and_filters(page, sent_mail):
    seed_recipes_with_test_client(
        sent_mail, "vera.seed@example.com", "Vera Testcook", VISITOR_RECIPES
    )

    # Browse, signed out.
    page.goto("/")
    assert page.get_by_role("link", name="Sign in", exact=True).is_visible()
    assert card_titles(page) == ALL_TITLES
    assert page.locator(".results-heading").inner_text() == "4 recipes in the cookbook"

    # Open a recipe from the list.
    page.get_by_role("link", name="Plum Kuchen", exact=True).click()
    page.wait_for_url(RECIPE_URL)
    assert heading(page) == "Plum Kuchen à la Great-Grandma Ottilie"
    assert (
        page.locator(".recipe-by").inner_text().strip() == "Typed in by Vera Testcook"
    )
    assert amounts(page) == ["2 cups", "6", "1/2 cup"]
    assert page.get_by_role("link", name="Edit this recipe").count() == 0

    # Search by the credited name (no account has it) from the header box.
    page.fill("#site-search", "Ottilie")
    page.click("form.search button[type=submit]")
    page.wait_for_url(re.compile(r"/\?q=Ottilie$"))
    assert card_titles(page) == ["Plum Kuchen"]
    assert page.locator(".card-by").all_inner_texts() == ["à la Great-Grandma Ottilie"]
    page.fill("#site-search", "great-grandma ottilie")
    page.click("form.search button[type=submit]")
    page.wait_for_url(re.compile(r"/\?q=great-grandma\+ottilie$"))
    assert card_titles(page) == ["Plum Kuchen"]

    # Each filter narrows the full list. Start from a clean index each time.
    page.goto("/")
    chip(page, "Course", "Dessert").click()
    page.wait_for_url(re.compile(r"meal=Dessert"))
    assert card_titles(page) == ["Lemon Bars", "Plum Kuchen"]
    assert page.locator(".results-heading").inner_text() == "2 recipes found"

    page.goto("/")
    chip(page, "Kind of food", "American").click()
    page.wait_for_url(re.compile(r"cuisine=American"))
    assert card_titles(page) == ["Green Salad", "Lemon Bars"]

    page.goto("/")
    chip(page, "Tags", "spicy").click()
    page.wait_for_url(re.compile(r"tag=spicy"))
    assert card_titles(page) == ["Weeknight Chili"]

    page.goto("/")
    page.fill("#ingredient-filter", "lemon")
    page.get_by_role("button", name="Apply").click()
    page.wait_for_url(re.compile(r"ingredient=lemon"))
    assert card_titles(page) == ["Lemon Bars"]

    # Filters combine: Dessert then American leaves only the one that is both.
    page.goto("/")
    chip(page, "Course", "Dessert").click()
    page.wait_for_url(re.compile(r"meal=Dessert"))
    chip(page, "Kind of food", "American").click()
    page.wait_for_url(re.compile(r"cuisine=American"))
    assert card_titles(page) == ["Lemon Bars"]

    # "Clear all" brings every recipe back.
    page.get_by_role("link", name="Clear all").click()
    page.wait_for_url(re.compile(r"/$"))
    assert card_titles(page) == ALL_TITLES


# --- R25: family member adds, reads and scales -------------------------------

PANCAKES = {
    "title": "Sunday Pancakes",
    "credited_to": "Aunt Marguerite",
    "servings": "4",
    "ingredients": "2 cups flour\n1 1/2 cups milk\n1 tsp baking powder\n2 eggs",
    "steps": "Whisk everything together.\nCook on a hot griddle.",
}
PANCAKE_AMOUNTS = ["2 cups", "1 1/2 cups", "1 teaspoon", "2"]


@pytest.fixture
def photo_file(tmp_path):
    """A synthetic 1600x1200 JPEG with a filename the server must not keep."""
    path = tmp_path / "my-pancakes-original-upload.jpg"
    Image.new("RGB", (1600, 1200), (210, 160, 90)).save(path, "JPEG", quality=85)
    return path


def test_member_adds_recipe_with_photo_and_scales_it(page, sign_in, photo_file):
    sign_in("rosa.member@example.com", "Rosa Testcook")
    assert page.get_by_role("button", name="Sign out").is_visible()

    path = add_recipe_in_browser(page, PANCAKES, photo=photo_file)
    assert "Thank you! Your recipe is now in the cookbook." in flashes(page)

    # Title, credit and submitter, in the template's exact words.
    assert heading(page) == "Sunday Pancakes à la Aunt Marguerite"
    assert page.locator("h1 .ala").inner_text() == "à la Aunt Marguerite"
    assert (
        page.locator(".recipe-by").inner_text().strip() == "Typed in by Rosa Testcook"
    )

    # The photo renders from a generated name, resized to 1280 on the long edge.
    image = page.locator(".photo-banner img")
    assert image.count() == 1
    src = image.get_attribute("src")
    assert GENERATED_PHOTO.match(src), src
    assert photo_file.stem not in page.content()
    page.wait_for_function(
        "() => { const i = document.querySelector('.photo-banner img');"
        " return i.complete && i.naturalWidth > 0; }"
    )
    assert image.evaluate("i => [i.naturalWidth, i.naturalHeight]") == [1280, 960]
    response = page.request.get(src)
    assert response.status == 200
    assert response.headers["content-type"] == "image/jpeg"

    # x2 doubles every amount; "As written" restores the exact original text.
    original = amounts(page)
    assert original == PANCAKE_AMOUNTS
    page.click("[data-scaler] button[data-factor='2']")
    assert amounts(page) == ["4 cups", "3 cups", "2 teaspoons", "4"]
    assert page.locator("[data-serving-output]").inner_text() == "8"
    assert page.locator("[data-scale-note]").is_visible()
    page.get_by_role("button", name="As written").click()
    assert amounts(page) == original
    assert page.locator("[data-ingredients] .amount.changed").count() == 0
    assert page.locator("[data-serving-output]").inner_text() == "4"
    assert page.locator("[data-scale-note]").is_hidden()

    # The servings stepper: 4 -> 6 is x1.5, then back down to 4.
    more = page.get_by_role("button", name="More servings")
    fewer = page.get_by_role("button", name="Fewer servings")
    more.click()
    more.click()
    assert page.locator("[data-serving-output]").inner_text() == "6"
    assert amounts(page) == ["3 cups", "2 1/4 cups", "1 1/2 teaspoons", "3"]
    fewer.click()
    fewer.click()
    fewer.click()
    assert page.locator("[data-serving-output]").inner_text() == "3"
    assert amounts(page) == ["1 1/2 cups", "1 1/8 cups", "3/4 teaspoon", "1 1/2"]
    more.click()
    assert page.locator("[data-serving-output]").inner_text() == "4"
    assert amounts(page) == original

    # The page we were on is the recipe we added.
    assert page.url.endswith(path)


# --- R26: a second member, then the submitter edits and deletes --------------

RHUBARB = {
    "title": "Rhubarb Crumble",
    "credited_to": "Grandpa Teodor",
    "servings": "6",
    "ingredients": "4 cups rhubarb\n1 cup oats\n1/2 cup butter",
    "steps": "Chop the rhubarb.\nTop with oats and butter and bake.",
}


def test_second_member_notes_and_submitter_edits_then_deletes(
    page, sign_in, sent_mail, guarded_context
):
    sign_in("una.submitter@example.com", "Una Testcook")
    path = add_recipe_in_browser(page, RHUBARB)
    assert page.get_by_role("link", name="Edit this recipe").is_visible()
    # The delete form sits in <p class="recipe-actions"> in the template, but
    # the HTML parser closes the <p> at <form>, so select it by its action.
    assert page.locator(DELETE_FORM).count() == 1

    # A second family member, in their own browser.
    other = guarded_context.new_page()
    sign_in_via_browser(other, sent_mail, "sam.second@example.com", "Sam Second")
    assert other.get_by_role("button", name="Sign out").is_visible()
    other.goto(path)
    assert heading(other) == "Rhubarb Crumble à la Grandpa Teodor"
    assert other.get_by_role("link", name="Edit this recipe").count() == 0
    assert other.locator(DELETE_FORM).count() == 0
    assert other.get_by_role("button", name="Delete").count() == 0

    # They can leave a note, and it shows with their name.
    note = "We used half oats, half almonds. Lovely."
    other.fill("#comment", note)
    fill_like_a_person(other, "form.comment-form")
    other.get_by_role("button", name="Post my note").click()
    other.wait_for_url(re.compile(r"#notes$"))
    assert "Thank you for the note!" in flashes(other)
    assert other.locator(".comment-body").all_inner_texts() == [note]
    assert other.locator(".comment-head strong").all_inner_texts() == ["Sam Second"]

    # The submitter sees the note and edits the recipe.
    page.goto(path)
    assert page.locator(".comment-body").all_inner_texts() == [note]
    page.get_by_role("link", name="Edit this recipe").click()
    page.wait_for_url(re.compile(r"/edit$"))
    assert page.input_value("#title") == "Rhubarb Crumble"
    page.fill("#title", "Rhubarb Ginger Crumble")
    page.fill("#ingredients", RHUBARB["ingredients"] + "\n1 tsp ground ginger")
    fill_like_a_person(page, "form[data-recipe-form]")
    page.get_by_role("button", name="Save my changes").click()
    page.wait_for_url(RECIPE_URL)
    assert "Your changes were saved." in flashes(page)
    assert heading(page) == "Rhubarb Ginger Crumble à la Grandpa Teodor"
    assert (
        page.locator("[data-ingredients] .ing-item").all_inner_texts()[-1]
        == "ground ginger"
    )
    assert page.locator(".comment-body").all_inner_texts() == [note]
    edited_path = "/recipe/" + page.url.rsplit("/", 1)[-1]

    # The second member sees the edit, still without controls.
    other.goto(edited_path)
    assert heading(other) == "Rhubarb Ginger Crumble à la Grandpa Teodor"
    assert other.get_by_role("link", name="Edit this recipe").count() == 0
    assert other.locator(DELETE_FORM).count() == 0

    # The submitter deletes it, accepting the confirm dialog.
    dialogs = []

    def accept(dialog):
        dialogs.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    page.locator(DELETE_FORM).get_by_role("button", name="Delete").click()
    page.wait_for_url(re.compile(r"/$"))
    assert dialogs == ["Remove this recipe from the cookbook for good?"]
    assert "That recipe was removed." in flashes(page)
    assert card_titles(page) == []
    assert page.get_by_role("link", name=re.compile("Rhubarb")).count() == 0

    # Gone: both its old and new addresses are 404, for both members.
    assert page.goto(edited_path).status == 404
    assert page.goto(path).status == 404
    assert other.goto(edited_path).status == 404
