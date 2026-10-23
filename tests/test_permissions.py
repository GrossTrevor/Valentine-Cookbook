"""Permission tests (T3b): R6 signed-out redirects, R7 non-owner refusals,
R11 blocked users.

Characterization tests: they describe what app.py does today and are
expected to pass. Each refusal has an allowed counterpart (submitter and
admin) so a pass proves the permission check, not a broken route.
Synthetic data only.
"""

import hashlib
import io
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from PIL import Image

import app as app_module
import db
import photos
from tests.conftest import get_csrf

EDIT_REFUSED = "Only the person who added a recipe can change it."


# --- helpers ---------------------------------------------------------------


def _new_client():
    return app_module.app.test_client()


def _jpeg(color=(200, 40, 60), size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


def _upload(color=(200, 40, 60), name="synthetic.jpg"):
    return (io.BytesIO(_jpeg(color)), name)


def _rows(conn, sql):
    return [tuple(row) for row in conn.execute(sql).fetchall()]


def snapshot():
    """Every recipe-related row plus every stored photo file (name -> sha256)."""
    conn = db.connect()
    try:
        state = {
            "recipes": _rows(conn, "SELECT * FROM recipes ORDER BY id"),
            "ingredients": _rows(conn, "SELECT * FROM ingredients ORDER BY id"),
            "tags": _rows(conn, "SELECT * FROM tags ORDER BY id"),
            "recipe_tags": _rows(
                conn, "SELECT * FROM recipe_tags ORDER BY recipe_id, tag_id"
            ),
            "photos": _rows(conn, "SELECT * FROM photos ORDER BY id"),
            "comments": _rows(conn, "SELECT * FROM comments ORDER BY id"),
        }
    finally:
        conn.close()
    directory = photos.photo_dir()
    files = {}
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), "rb") as handle:
            files[name] = hashlib.sha256(handle.read()).hexdigest()
    state["files"] = files
    return state


def _query(sql, params=()):
    conn = db.connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _execute(sql, params=()):
    conn = db.connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _recipe(slug):
    rows = _query("SELECT * FROM recipes WHERE slug = ?", (slug,))
    return rows[0] if rows else None


def _photo_rows(recipe_id):
    return _query("SELECT * FROM photos WHERE recipe_id = ? ORDER BY id", (recipe_id,))


def _photo_file_exists(filename):
    return os.path.exists(os.path.join(photos.photo_dir(), filename))


def _is_login_redirect(response):
    return response.status_code == 302 and (
        urlsplit(response.headers["Location"]).path == "/login"
    )


def _edit_form(token, **overrides):
    data = {
        "csrf": token,
        "title": "Hijacked Chowder",
        "ingredients": "9 cups gravel\n1 tsp sand",
        "steps": "Replaced steps.",
        "servings": "2",
        "tags": "changed, different",
        "credited_to": "Someone Else",
    }
    data.update(overrides)
    return data


@pytest.fixture
def world(make_user, sign_in, make_recipe, csrf):
    """An owner with one recipe (ingredients, tags, two real photos), a note
    on it by another member, and signed-in clients for owner, other and admin."""
    owner = make_user("olive.owner@example.com", "Olive Owner")
    other = make_user("nina.nobody@example.com", "Nina Nobody")
    admin = make_user("ada.admin@example.com", "Ada Admin", admin=True)
    clients = {}
    for key, user in (("owner", owner), ("other", other), ("admin", admin)):
        clients[key] = sign_in(_new_client(), user)

    slug = make_recipe(
        clients["owner"],
        title="Synthetic Barley Soup",
        ingredients="2 cups barley\n1 onion, diced\n1/2 tsp salt",
        tags="soup, winter",
        credited_to="Aunt Testa",
        photos=[_upload((10, 120, 200), "one.jpg"), _upload((220, 200, 10), "two.jpg")],
    )
    recipe = _recipe(slug)
    photo_rows = _photo_rows(recipe["id"])
    assert len(photo_rows) == 2, "fixture photos were not stored"
    assert (
        len(_query("SELECT * FROM ingredients WHERE recipe_id = ?", (recipe["id"],)))
        == 3
    )
    assert (
        len(_query("SELECT * FROM recipe_tags WHERE recipe_id = ?", (recipe["id"],)))
        == 2
    )

    response = clients["other"].post(
        "/recipe/%s/comment" % slug,
        data={"csrf": csrf(clients["other"], "/add"), "comment": "Lovely with bread."},
    )
    assert response.status_code == 302
    comment_id = _query("SELECT id FROM comments WHERE user_id = ?", (other.id,))[0][
        "id"
    ]

    return SimpleNamespace(
        owner=owner,
        other=other,
        admin=admin,
        clients=clients,
        slug=slug,
        recipe_id=recipe["id"],
        photo_ids=[p["id"] for p in photo_rows],
        photo_files=[p["filename"] for p in photo_rows],
        comment_id=comment_id,
    )


# --- R6: signed-out visitors -----------------------------------------------


@pytest.mark.parametrize("path", ["/add", "/recipe/{slug}/edit", "/me"])
def test_signed_out_get_redirects_to_login(world, path):
    visitor = _new_client()
    before = snapshot()
    response = visitor.get(path.format(slug=world.slug))
    assert _is_login_redirect(response), (
        response.status_code,
        response.headers.get("Location"),
    )
    assert snapshot() == before


@pytest.mark.parametrize("action", ["edit", "delete", "comment", "delete_comment"])
def test_signed_out_post_redirects_to_login_and_changes_nothing(world, action):
    visitor = _new_client()
    token = get_csrf(visitor, "/login")
    paths = {
        "edit": "/recipe/%s/edit" % world.slug,
        "delete": "/recipe/%s/delete" % world.slug,
        "comment": "/recipe/%s/comment" % world.slug,
        "delete_comment": "/comment/%d/delete" % world.comment_id,
    }
    data = {
        "edit": _edit_form(token, remove_photo=str(world.photo_ids[0])),
        "delete": {"csrf": token},
        "comment": {"csrf": token, "comment": "A note from nowhere."},
        "delete_comment": {"csrf": token},
    }[action]
    before = snapshot()
    response = visitor.post(paths[action], data=data)
    assert _is_login_redirect(response), (
        response.status_code,
        response.headers.get("Location"),
    )
    assert snapshot() == before


def test_signed_out_post_without_csrf_is_stopped_by_guard_not_login(world):
    """Control for the test above: without the token, guard() redirects to the
    index before require_login() runs, so those /login redirects really come
    from require_login()."""
    visitor = _new_client()
    get_csrf(visitor, "/login")
    before = snapshot()
    response = visitor.post("/recipe/%s/delete" % world.slug, data={})
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/"
    assert snapshot() == before


# --- R7: non-owner refusals and their allowed counterparts -----------------


def _post_edit(client, slug, **overrides):
    token = get_csrf(client, "/add")
    return client.post("/recipe/%s/edit" % slug, data=_edit_form(token, **overrides))


def _assert_edit_refused(client, world, **overrides):
    before = snapshot()
    response = _post_edit(client, world.slug, **overrides)
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/recipe/%s" % world.slug
    assert snapshot() == before
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert EDIT_REFUSED in page
    for filename in world.photo_files:
        assert _photo_file_exists(filename)


def test_non_owner_edit_is_refused_with_message_and_changes_nothing(world):
    _assert_edit_refused(
        world.clients["other"],
        world,
        remove_photo=[str(pid) for pid in world.photo_ids],
        photos=[_upload((1, 2, 3), "extra.jpg")],
    )


def test_non_owner_photo_removal_removes_nothing(world):
    _assert_edit_refused(
        world.clients["other"],
        world,
        title="Synthetic Barley Soup",
        ingredients="2 cups barley\n1 onion, diced\n1/2 tsp salt",
        tags="soup, winter",
        credited_to="Aunt Testa",
        remove_photo=str(world.photo_ids[0]),
    )


def test_non_owner_delete_is_forbidden_and_changes_nothing(world):
    before = snapshot()
    token = get_csrf(world.clients["other"], "/add")
    response = world.clients["other"].post(
        "/recipe/%s/delete" % world.slug, data={"csrf": token}
    )
    assert response.status_code == 403
    assert snapshot() == before


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_owner_and_admin_can_edit_and_remove_a_photo(world, actor):
    response = _post_edit(
        world.clients[actor], world.slug, remove_photo=str(world.photo_ids[0])
    )
    assert response.status_code == 302
    row = _recipe("hijacked-chowder")
    assert row is not None and row["id"] == world.recipe_id
    assert row["title"] == "Hijacked Chowder"
    assert row["submitted_by_id"] == world.owner.id
    assert [p["id"] for p in _photo_rows(world.recipe_id)] == [world.photo_ids[1]]
    assert not _photo_file_exists(world.photo_files[0])
    assert _photo_file_exists(world.photo_files[1])
    raws = [
        r["raw"]
        for r in _query(
            "SELECT raw FROM ingredients WHERE recipe_id = ? ORDER BY position",
            (world.recipe_id,),
        )
    ]
    assert raws == ["9 cups gravel", "1 tsp sand"]


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_owner_and_admin_can_delete(world, actor):
    token = get_csrf(world.clients[actor], "/add")
    response = world.clients[actor].post(
        "/recipe/%s/delete" % world.slug, data={"csrf": token}
    )
    assert response.status_code == 302
    assert _recipe(world.slug) is None
    assert _photo_rows(world.recipe_id) == []
    for filename in world.photo_files:
        assert not _photo_file_exists(filename)


def test_non_author_note_delete_is_forbidden(world):
    """The recipe's submitter is not the note's author, so is refused too."""
    before = snapshot()
    client = world.clients["owner"]
    token = get_csrf(client, "/add")
    response = client.post(
        "/comment/%d/delete" % world.comment_id, data={"csrf": token}
    )
    assert response.status_code == 403
    assert snapshot() == before


@pytest.mark.parametrize("actor", ["other", "admin"])
def test_author_and_admin_can_delete_a_note(world, actor):
    client = world.clients[actor]
    token = get_csrf(client, "/add")
    response = client.post(
        "/comment/%d/delete" % world.comment_id, data={"csrf": token}
    )
    assert response.status_code == 302
    assert _query("SELECT id FROM comments WHERE id = ?", (world.comment_id,)) == []


def test_credited_to_actor_name_grants_no_edit(world):
    _execute(
        "UPDATE recipes SET credited_to = ? WHERE id = ?",
        (world.other.name, world.recipe_id),
    )
    assert _recipe(world.slug)["credited_to"] == "Nina Nobody"
    _assert_edit_refused(
        world.clients["other"], world, remove_photo=str(world.photo_ids[0])
    )


def test_credited_to_actor_name_grants_no_delete(world):
    _execute(
        "UPDATE recipes SET credited_to = ? WHERE id = ?",
        (world.other.name, world.recipe_id),
    )
    before = snapshot()
    token = get_csrf(world.clients["other"], "/add")
    response = world.clients["other"].post(
        "/recipe/%s/delete" % world.slug, data={"csrf": token}
    )
    assert response.status_code == 403
    assert snapshot() == before


# --- R11: a user blocked after signing in ----------------------------------


def _block(user):
    _execute("UPDATE users SET is_blocked = 1 WHERE id = ?", (user.id,))


@pytest.mark.parametrize("path", ["/me", "/add"])
def test_blocked_user_next_get_is_treated_as_signed_out(world, path):
    client = world.clients["owner"]
    assert client.get(path).status_code == 200
    _block(world.owner)
    response = client.get(path)
    assert _is_login_redirect(response), (
        response.status_code,
        response.headers.get("Location"),
    )
    # Still signed out on the request after that.
    assert _is_login_redirect(client.get("/me"))


def test_blocked_owner_edit_post_changes_nothing(world):
    client = world.clients["owner"]
    token = get_csrf(client, "/add")
    _block(world.owner)
    before = snapshot()
    response = client.post(
        "/recipe/%s/edit" % world.slug,
        data=_edit_form(token, remove_photo=str(world.photo_ids[0])),
    )
    assert _is_login_redirect(response), (
        response.status_code,
        response.headers.get("Location"),
    )
    assert snapshot() == before
