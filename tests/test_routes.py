"""Route characterization tests (T3a): hidden recipes (R10), CSRF on every
POST route (R12), security headers (R16), HTML escaping (R17) and the
photo-name guard (R18).

These pin down current behaviour through the Flask test client. Synthetic
data only: @example.com emails and invented names.
"""

import html
import io
import os

import flask
import flask.helpers
import pytest
import werkzeug.utils
from PIL import Image
from werkzeug.exceptions import NotFound

import app as app_module
import db
import moderation
import photos
import units
from tests.conftest import get_csrf

# --- helpers ---------------------------------------------------------------


def _db_snapshot():
    """Every row of every table, as sorted tuples, plus the photo files on
    disk. Used to prove a request changed no data."""
    conn = db.connect()
    try:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        snapshot = {
            table: sorted(
                tuple(row) for row in conn.execute("SELECT * FROM %s" % table)
            )
            for table in tables
        }
    finally:
        conn.close()
    photo_root = os.path.join(db.data_dir(), "photos")
    snapshot["<photo files>"] = (
        sorted(os.listdir(photo_root)) if os.path.isdir(photo_root) else []
    )
    return snapshot


def _set_hidden(slug, hidden=True):
    conn = db.connect()
    conn.execute(
        "UPDATE recipes SET is_hidden = ? WHERE slug = ?", (1 if hidden else 0, slug)
    )
    conn.commit()
    conn.close()


def _jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 120, 80)).save(buffer, "JPEG")
    return buffer.getvalue()


def _new_client():
    return app_module.app.test_client()


def _text(response):
    return response.get_data(as_text=True)


SECURITY_HEADERS = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)


def _assert_security_headers(response):
    for name in SECURITY_HEADERS:
        assert name in response.headers, "%s missing on a %d response" % (
            name,
            response.status_code,
        )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] in {
        "same-origin",
        "no-referrer",
        "strict-origin",
        "strict-origin-when-cross-origin",
    }
    directives = {}
    for part in response.headers["Content-Security-Policy"].split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0]] = tokens[1:]
    assert directives.get("default-src") == ["'self'"]
    assert directives.get("script-src") == ["'self'"]
    assert directives.get("style-src") == ["'self'"]
    assert directives.get("frame-ancestors") == ["'none'"]


# --- R10: hidden recipes ---------------------------------------------------

HIDDEN = {
    "title": "Quuxberry Pie",
    "cuisine": "Zanzibarian",
    "meal": "Elevenses",
    "tags": "zorptag",
    "ingredients": "2 cups quuxberries\n1 cup sugar",
}
FILTERS = {
    "q": "quuxberry",
    "cuisine": "Zanzibarian",
    "meal": "Elevenses",
    "tag": "zorptag",
    "ingredient": "quuxberries",
}


@pytest.fixture
def hidden_setup(client, make_user, sign_in, make_recipe):
    """An owner with one visible and one (about to be) hidden recipe.
    Returns (owner_client, slug_of_hidden, other_client, admin_client)."""
    owner = make_user("hollis.quade@example.com", "Hollis Quade")
    sign_in(client, owner)
    make_recipe(
        client,
        title="Plain Toast",
        cuisine="American",
        meal="Breakfast",
        tags="simple",
        ingredients="1 slice bread",
    )
    slug = make_recipe(client, **HIDDEN)

    other = make_user("mirela.voss@example.com", "Mirela Voss")
    other_client = sign_in(_new_client(), other)
    admin = make_user("odessa.brandt@example.com", "Odessa Brandt", admin=True)
    admin_client = sign_in(_new_client(), admin)
    return client, slug, other_client, admin_client


def _viewers(hidden_setup):
    owner_client, _slug, other_client, _admin = hidden_setup
    return {
        "signed-out": _new_client(),
        "other member": other_client,
        "owner": owner_client,
    }


def test_filters_find_the_recipe_while_visible(hidden_setup):
    """Control for the hidden tests: every filter and facet finds it first."""
    _owner, slug, _other, _admin = hidden_setup
    viewer = _new_client()
    index = _text(viewer.get("/"))
    assert "/recipe/%s" % slug in index
    for facet in ("Zanzibarian", "Elevenses", "zorptag"):
        assert facet in index
    for param, value in FILTERS.items():
        page = _text(viewer.get("/", query_string={param: value}))
        assert "/recipe/%s" % slug in page, "%s=%s did not find it" % (param, value)
    assert viewer.get("/recipe/%s" % slug).status_code == 200


def test_hidden_recipe_is_absent_from_index_search_filters_and_facets(hidden_setup):
    _owner, slug, _other, _admin = hidden_setup
    _set_hidden(slug)
    for who, viewer in _viewers(hidden_setup).items():
        index = viewer.get("/")
        assert index.status_code == 200
        body = _text(index)
        assert "/recipe/%s" % slug not in body, who
        assert HIDDEN["title"] not in body, who
        # facet lists: cuisine, meal and tag chips come only from visible recipes
        for facet in ("Zanzibarian", "Elevenses", "zorptag"):
            assert facet not in body, "%s facet shown to %s" % (facet, who)
        assert "1 recipe in the cookbook" in body, who
        for param, value in FILTERS.items():
            page = viewer.get("/", query_string={param: value})
            assert page.status_code == 200
            assert "/recipe/%s" % slug not in _text(page), "%s=%s for %s" % (
                param,
                value,
                who,
            )
            assert HIDDEN["title"] not in _text(page), "%s=%s for %s" % (
                param,
                value,
                who,
            )


def test_hidden_recipe_page_is_404_for_non_admins(hidden_setup):
    _owner, slug, _other, _admin = hidden_setup
    _set_hidden(slug)
    for who, viewer in _viewers(hidden_setup).items():
        response = viewer.get("/recipe/%s" % slug)
        assert response.status_code == 404, who
        assert HIDDEN["title"] not in _text(response), who


@pytest.mark.xfail(
    strict=True,
    reason="finding: GET /recipe/<slug>/edit ignores is_hidden; owner gets 200 with the form",
)
def test_hidden_recipe_edit_page_is_404_for_its_owner(hidden_setup):
    owner_client, slug, _other, _admin = hidden_setup
    _set_hidden(slug)
    response = owner_client.get("/recipe/%s/edit" % slug)
    assert response.status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="finding: GET /recipe/<slug>/edit ignores is_hidden; non-owner gets 302, not 404",
)
def test_hidden_recipe_edit_page_is_404_for_other_members(hidden_setup):
    _owner, slug, other_client, _admin = hidden_setup
    _set_hidden(slug)
    response = other_client.get("/recipe/%s/edit" % slug)
    assert response.status_code == 404


def test_admin_can_open_a_hidden_recipe(hidden_setup):
    _owner, slug, _other, admin_client = hidden_setup
    _set_hidden(slug)
    response = admin_client.get("/recipe/%s" % slug)
    assert response.status_code == 200
    assert HIDDEN["title"] in _text(response)
    assert admin_client.get("/recipe/%s/edit" % slug).status_code == 200


# --- R12: CSRF on every POST route -----------------------------------------


@pytest.fixture
def world(client, make_user, sign_in, make_recipe, mailbox):
    """A signed-in admin who owns a recipe (with a photo) and a note on it,
    so permission checks would pass and only CSRF can stop a request."""
    admin = make_user(
        "perpetua.lindqvist@example.com", "Perpetua Lindqvist", admin=True
    )
    sign_in(client, admin)
    token = get_csrf(client, "/add")
    response = client.post(
        "/add",
        data={
            "csrf": token,
            "title": "Marrowfat Stew",
            "ingredients": "1 cup marrowfat peas\n2 cups broth",
            "steps": "Simmer everything slowly.",
            "servings": "4",
            "photos": (io.BytesIO(_jpeg_bytes()), "stew.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    slug = response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    client.post(
        "/recipe/%s/comment" % slug,
        data={"csrf": get_csrf(client, "/add"), "comment": "Lovely with crusty bread."},
    )
    conn = db.connect()
    recipe_id = conn.execute(
        "SELECT id FROM recipes WHERE slug = ?", (slug,)
    ).fetchone()[0]
    comment_id = conn.execute("SELECT id FROM comments").fetchone()[0]
    photo_name = conn.execute("SELECT filename FROM photos").fetchone()[0]
    conn.close()
    assert os.path.exists(os.path.join(db.data_dir(), "photos", photo_name))
    return {
        "client": client,
        "slug": slug,
        "recipe_id": recipe_id,
        "comment_id": comment_id,
        "photo": photo_name,
        "mailbox": mailbox,
    }


def _signed_in(client):
    return client.get("/me").status_code == 200


# Each POST endpoint: how to build its URL and a form that WOULD change data
# with a valid token (proved by the control in the same test).
POST_CASES = {
    "add": lambda w: (
        "/add",
        {
            "title": "Fennel Gratin",
            "ingredients": "1 bulb fennel",
            "steps": "Bake until golden.",
        },
    ),
    "edit": lambda w: (
        "/recipe/%s/edit" % w["slug"],
        {
            "title": "Marrowfat Stew Revised",
            "ingredients": "3 cups marrowfat peas",
            "steps": "Simmer for an hour.",
            "remove_photo": w["photo"],
        },
    ),
    "delete": lambda w: ("/recipe/%s/delete" % w["slug"], {}),
    "comment": lambda w: (
        "/recipe/%s/comment" % w["slug"],
        {"comment": "Added a pinch of thyme, very nice."},
    ),
    "delete_comment": lambda w: ("/comment/%d/delete" % w["comment_id"], {}),
    "login": lambda w: (
        "/login",
        {"email": "ignatius.fenwick@example.com", "full_name": "Ignatius Fenwick"},
    ),
    "logout": lambda w: ("/logout", {}),
    "api_parse": lambda w: ("/api/parse", {"ingredients": "2 cups flour"}),
}


def _post_endpoints():
    return sorted(
        rule.endpoint
        for rule in app_module.app.url_map.iter_rules()
        if "POST" in (rule.methods or ())
    )


def test_every_post_route_has_a_csrf_case():
    """A new POST route fails here until it is added to POST_CASES."""
    assert set(_post_endpoints()) == set(POST_CASES)


@pytest.mark.parametrize("bad_token", ["missing", "wrong", "empty"])
@pytest.mark.parametrize("endpoint", _post_endpoints())
def test_post_without_valid_csrf_changes_nothing(endpoint, bad_token, world):
    assert endpoint in POST_CASES, "new POST route %s has no CSRF case" % endpoint
    client = world["client"]
    url, form = POST_CASES[endpoint](world)
    if endpoint == "login":
        client = _new_client()  # /login redirects signed-in users before the form
        get_csrf(client)  # the session has a real token; the form does not
    else:
        get_csrf(client, "/add")

    data = dict(form)
    if bad_token == "wrong":
        data["csrf"] = "not-the-session-token-0123456789abcdef"
    elif bad_token == "empty":
        data["csrf"] = ""

    before = _db_snapshot()
    mail_before = len(world["mailbox"])
    response = client.post(url, data=data)
    assert response.status_code == 302, _text(response)[:300]
    assert _db_snapshot() == before
    assert len(world["mailbox"]) == mail_before
    if endpoint == "logout":
        assert _signed_in(client), "logout without a valid token signed the user out"
    if endpoint == "api_parse":
        assert not response.is_json

    # control: the same request with the session's token does take effect
    data["csrf"] = get_csrf(client, "/login" if endpoint == "login" else "/add")
    response = client.post(url, data=data)
    if endpoint == "logout":
        assert not _signed_in(client)
    elif endpoint == "api_parse":
        assert response.status_code == 200
        assert response.get_json()["ingredients"][0]["item"] == "flour"
    else:
        assert _db_snapshot() != before, "control request changed nothing"


# --- R16: security headers -------------------------------------------------


def test_headers_on_200(client):
    response = client.get("/")
    assert response.status_code == 200
    _assert_security_headers(response)


def test_headers_on_302(client):
    response = client.get("/add")
    assert response.status_code == 302
    _assert_security_headers(response)


def test_headers_on_302_from_csrf_guard(client):
    response = client.post("/logout", data={})
    assert response.status_code == 302
    _assert_security_headers(response)


def test_headers_on_403(client, make_user, sign_in, make_recipe):
    owner = make_user("bertram.oyelaran@example.com", "Bertram Oyelaran")
    sign_in(client, owner)
    slug = make_recipe(client)
    stranger = sign_in(
        _new_client(), make_user("cosima.raft@example.com", "Cosima Raft")
    )
    response = stranger.post(
        "/recipe/%s/delete" % slug, data={"csrf": get_csrf(stranger, "/add")}
    )
    assert response.status_code == 403
    _assert_security_headers(response)


def test_headers_on_404(client):
    response = client.get("/recipe/no-such-recipe-anywhere")
    assert response.status_code == 404
    _assert_security_headers(response)


def test_headers_on_405(client):
    response = client.get("/logout")
    assert response.status_code == 405
    _assert_security_headers(response)


def test_headers_on_500_from_the_real_error_handler(client, monkeypatch, csrf):
    assert not app_module.app.testing
    assert not app_module.app.debug
    assert not app_module.app.config.get("PROPAGATE_EXCEPTIONS")

    def boom(_line):
        raise RuntimeError("synthetic failure for the 500 test")

    monkeypatch.setattr(units, "parse_ingredient", boom)
    response = client.post(
        "/api/parse", data={"csrf": csrf(client), "ingredients": "1 cup flour"}
    )
    assert response.status_code == 500
    assert "Something went wrong on our end" in _text(response)
    _assert_security_headers(response)


def test_headers_on_static_file(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    _assert_security_headers(response)
    response.close()


# --- R17: HTML is shown as text --------------------------------------------

SCRIPT = "<script>alert(1)</script>"
BOLD = "<b>x</b>"


def test_payloads_pass_moderation():
    """Guard for the escaping tests: the payloads must reach the database."""
    for text in (SCRIPT, BOLD, "Quiche " + SCRIPT, "Tasty " + BOLD + " pie"):
        assert moderation.check(text) == text


def _assert_escaped(body, raw, where):
    assert raw not in body, "raw %r rendered on %s" % (raw, where)
    assert html.escape(raw, quote=False) in body, "%r not shown as text on %s" % (
        raw,
        where,
    )


def test_html_in_recipe_fields_and_notes_is_escaped(
    client, make_user, sign_in, make_recipe
):
    user = make_user("thaddeus.morrow@example.com", "Thaddeus Morrow")
    sign_in(client, user)
    slug = make_recipe(
        client,
        title="Quiche " + SCRIPT,
        description="Tasty " + BOLD + " pie " + SCRIPT,
        ingredients="1 cup " + BOLD + "\n2 eggs " + SCRIPT,
        notes="Chill first " + BOLD,
        steps="Whisk it. " + SCRIPT,
    )
    response = client.post(
        "/recipe/%s/comment" % slug,
        data={"csrf": get_csrf(client, "/add"), "comment": "Loved it " + SCRIPT + BOLD},
    )
    assert response.status_code == 302

    conn = db.connect()
    stored = conn.execute(
        "SELECT title FROM recipes WHERE slug = ?", (slug,)
    ).fetchone()[0]
    note = conn.execute("SELECT body FROM comments").fetchone()[0]
    conn.close()
    assert SCRIPT in stored and SCRIPT in note  # stored as typed, escaped on output

    for who, viewer in {"author": client, "visitor": _new_client()}.items():
        page = _text(viewer.get("/recipe/%s" % slug))
        _assert_escaped(page, SCRIPT, "recipe page for %s" % who)
        _assert_escaped(page, BOLD, "recipe page for %s" % who)
        index = _text(viewer.get("/"))
        _assert_escaped(index, SCRIPT, "index for %s" % who)
        _assert_escaped(index, BOLD, "index for %s" % who)
        search = _text(viewer.get("/", query_string={"q": "quiche"}))
        _assert_escaped(search, SCRIPT, "search for %s" % who)

    edit = _text(client.get("/recipe/%s/edit" % slug))
    _assert_escaped(edit, SCRIPT, "edit form")
    _assert_escaped(edit, BOLD, "edit form")
    mine = _text(client.get("/me"))
    _assert_escaped(mine, SCRIPT, "/me")


def test_html_in_credit_is_escaped(client, make_user, sign_in, make_recipe):
    with pytest.raises(moderation.Rejected):
        moderation.check_credit("Aunt " + BOLD)  # why the DB update is needed

    user = make_user("rosalind.achebe@example.com", "Rosalind Achebe")
    sign_in(client, user)
    slug = make_recipe(client, title="Barley Loaf")
    conn = db.connect()
    conn.execute(
        "UPDATE recipes SET credited_to = ? WHERE slug = ?",
        ("Aunt " + SCRIPT + BOLD, slug),
    )
    conn.commit()
    conn.close()

    viewer = _new_client()
    page = _text(viewer.get("/recipe/%s" % slug))
    _assert_escaped(page, SCRIPT, "recipe page")
    _assert_escaped(page, BOLD, "recipe page")
    index = _text(viewer.get("/"))
    _assert_escaped(index, SCRIPT, "index")
    _assert_escaped(index, BOLD, "index")


# --- R18: photo names ------------------------------------------------------

HEX = "0123456789abcdef" * 2
BAD_PHOTO_NAMES = {
    "dotdot": "..",
    "encoded dotdot": "%2E%2E",
    "encoded slash traversal": "..%2Fapp.py",
    "encoded backslash traversal": "..%5Capp.py",
    "traversal to schema": "..%2F..%2Fschema.sql",
    "dotdot with valid suffix": "..%2F" + HEX + ".jpg",
    "wrong extension jpeg": HEX + ".jpeg",
    "wrong extension png": HEX + ".png",
    "double extension": HEX + ".jpg.png",
    "uppercase hex": HEX.upper() + ".jpg",
    "uppercase extension": HEX + ".JPG",
    "non-hex letters": "g" * 32 + ".jpg",
    "too short": HEX[:31] + ".jpg",
    "too long by one": HEX + "a.jpg",
    "over-long": "a" * 5000 + ".jpg",
    "empty": "",
    "only extension": ".jpg",
    "null byte": HEX + ".jpg%00",
    "leading space": "%20" + HEX + ".jpg",
    "trailing dot": HEX + ".jpg.",
    "database file": "cookbook.db",
}


@pytest.fixture
def filesystem_tripwire(monkeypatch):
    """Records any attempt by the photo route to reach the filesystem and
    turns it into a 404, so a test can tell 'refused' from 'looked and missed'."""
    calls = []

    def trip(*args, **kwargs):
        calls.append(args)
        raise NotFound()

    def trip_dir():
        calls.append(("photo_dir",))
        raise AssertionError("photo_dir() called")

    for module in (app_module, flask, flask.helpers, werkzeug.utils):
        monkeypatch.setattr(module, "send_from_directory", trip)
    monkeypatch.setattr(photos, "photo_dir", trip_dir)
    return calls


def test_tripwire_catches_a_valid_photo_name(client, filesystem_tripwire):
    """Control: a well-formed name does go to the filesystem."""
    response = client.get("/photo/%s.jpg" % HEX)
    assert filesystem_tripwire, "valid name never reached the filesystem"
    assert response.status_code in (404, 500)


@pytest.mark.parametrize(
    "name", list(BAD_PHOTO_NAMES.values()), ids=list(BAD_PHOTO_NAMES)
)
def test_bad_photo_names_are_404_without_touching_the_filesystem(
    client, filesystem_tripwire, name
):
    response = client.get("/photo/" + name)
    assert filesystem_tripwire == [], "the photo route reached the filesystem"
    assert response.status_code == 404
    _assert_security_headers(response)


@pytest.mark.xfail(
    strict=True,
    reason="finding: FILENAME_RE uses $ with re.match, so '<32 hex>.jpg\\n' passes and reaches send_from_directory",
)
def test_photo_name_with_trailing_newline_is_refused_before_the_filesystem(
    client, filesystem_tripwire
):
    response = client.get("/photo/%s.jpg%%0A" % HEX)
    assert filesystem_tripwire == [], "the photo route reached the filesystem"
    assert response.status_code == 404
