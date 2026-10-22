"""Permission properties (T4a): R8 ownership state machine, R9 credited_to
grants nothing.

Characterization tests: they describe what app.py does today and are
expected to pass. Synthetic data only (@example.com emails, invented names).

R8 runs a hypothesis RuleBasedStateMachine against the real app through
Flask test clients (one client and one REMOTE_ADDR per simulated user, real
magic-link sign-in). A shadow model records who owns what; after every step
the database must equal the model, and the model only applies a change when
the actor is the recipe's submitter or an admin (for notes: the note's author
or an admin).

Why the machine manages DATA_DIR itself: conftest's `fresh_db` is a
function-scoped fixture, but a state machine runs many examples inside one
test function, and hypothesis builds a new machine object per example without
going through pytest fixtures. So `@initialize` makes its own temp DATA_DIR
with `tempfile.mkdtemp`, points `os.environ["DATA_DIR"]` at it, runs
`db.init()`, and swaps `mailer.send` for a capture list; `teardown()` puts
the previous DATA_DIR, ADMIN_EMAILS and mailer.send back and deletes the
temp dir. A fresh database per example also resets the rate-limit buckets.
"""

import hashlib
import io
import os
import re
import shutil
import string
import tempfile
from urllib.parse import urlsplit

import pytest
from hypothesis import assume, event, example, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    multiple,
    precondition,
    rule,
)
from PIL import Image

import app as app_module
import db
import mailer
import moderation
import photos as photos_module
from tests.conftest import get_csrf

# Caps per example. Each simulated user signs in once (limit 6 per user per
# hour, 20 per IP; every user has its own IP). Recipes and notes stay far
# below 12 adds and 15 notes per user per hour. Photos are slow (Pillow
# decode + re-encode), so only a few per example.
MAX_USERS = 4
MAX_RECIPES = 6
MAX_NOTES = 6
MAX_PHOTOS = 5
STEP_COUNT = 25

USER_NAMES = ["Wren Testwood", "Pip Fakerly", "Juno Samplesby", "Otto Mockridge"]
TITLE_WORDS = ["Synthetic", "Imaginary", "Pretend", "Sample", "Example", "Mock"]

# About one member in four is an admin, so most attempts on other people's
# things are by plain members and get refused.
admin_flags = st.integers(0, 3).map(lambda n: n == 3)


# --- shared helpers ------------------------------------------------------------


def _jpeg(color):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(buffer, "JPEG")
    return buffer.getvalue()


def _session_csrf(client):
    with client.session_transaction() as sess:
        token = sess.get("csrf")
    assert token, "no csrf token in the session"
    return token


def _path(response):
    return urlsplit(response.headers.get("Location", "")).path


# --- R8: ownership state machine -----------------------------------------------


class OwnershipMachine(RuleBasedStateMachine):
    users = Bundle("users")
    recipes = Bundle("recipes")
    notes = Bundle("notes")

    # -- lifecycle -------------------------------------------------------------

    @initialize(target=users, admins=st.tuples(admin_flags, admin_flags))
    def fresh_database(self, admins):
        """New DATA_DIR + schema, then the first two members sign in, so
        add_recipe can run from the first step. (Hypothesis switches random
        subsets of rules off per example; with add_user as the only valid
        rule at the start, many examples were aborted as invalid.)"""
        self._saved_env = {
            key: os.environ.get(key) for key in ("DATA_DIR", "ADMIN_EMAILS")
        }
        self._saved_send = mailer.send
        self.data_dir = tempfile.mkdtemp(prefix="cookbook-machine-")
        os.environ["DATA_DIR"] = self.data_dir
        os.environ.pop("ADMIN_EMAILS", None)
        db.init()
        self.mail = []
        mailer.send = self._capture_mail

        self.user_list = []  # handle -> dict(id, email, name, admin, client)
        self.recipe_model = {}  # recipe id -> dict(slug, title, owner, credit, ingredients, photos)
        self.note_model = {}  # comment id -> (recipe id, author user id)
        self.last_slug = {}  # recipe id -> last slug the model knew (for 404 attempts)
        self.recipes_added = 0
        self.notes_added = 0
        self.photos_added = 0
        self.counter = 0
        return multiple(*(self._sign_up(admin) for admin in admins))

    def teardown(self):
        if not hasattr(self, "data_dir"):
            return
        mailer.send = self._saved_send
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def _capture_mail(self, to_address, subject, body):
        self.mail.append((to_address, subject, body))
        return False

    def _next(self):
        self.counter += 1
        return self.counter

    def _can_change_recipe(self, user, recipe_id):
        return user["admin"] or self.recipe_model[recipe_id]["owner"] == user["id"]

    @staticmethod
    def _record(action, live, allowed):
        """Shows in --hypothesis-show-statistics that both sides ran."""
        event(
            "%s: %s"
            % (action, "gone" if not live else "allowed" if allowed else "refused")
        )

    # -- users -----------------------------------------------------------------

    @precondition(lambda self: len(self.user_list) < MAX_USERS)
    @rule(target=users, admin=admin_flags)
    def add_user(self, admin):
        return self._sign_up(admin)

    def _sign_up(self, admin):
        """Real magic-link sign-in; /login creates the account itself."""
        handle = len(self.user_list)
        name = USER_NAMES[handle]
        email = "machine.user%d@example.com" % handle
        if admin:
            current = os.environ.get("ADMIN_EMAILS", "")
            os.environ["ADMIN_EMAILS"] = ",".join(filter(None, [current, email]))

        client = app_module.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "10.42.0.%d" % (handle + 1)
        token = get_csrf(client, "/login")
        before = len(self.mail)
        client.post("/login", data={"csrf": token, "email": email, "full_name": name})
        assert len(self.mail) == before + 1, "sign-in sent no email"
        link = re.search(r"https?://\S+/login/\S+", self.mail[-1][2]).group(0)
        assert client.get(link).status_code == 302

        conn = db.connect()
        try:
            user_id = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()["id"]
        finally:
            conn.close()
        self.user_list.append(
            {
                "id": user_id,
                "email": email,
                "name": name,
                "admin": admin,
                "client": client,
            }
        )
        return handle

    # -- recipes ---------------------------------------------------------------

    @precondition(lambda self: self.recipes_added < MAX_RECIPES)
    @rule(
        target=recipes,
        owner=users,
        n_photos=st.integers(0, 2),
        credit=st.one_of(st.none(), users),
    )
    def add_recipe(self, owner, n_photos, credit):
        user = self.user_list[owner]
        n = self._next()
        n_photos = min(n_photos, MAX_PHOTOS - self.photos_added)
        title = "%s Bake %d" % (TITLE_WORDS[n % len(TITLE_WORDS)], n)
        ingredients = ("1 cup water", "%d tbsp salt" % (n % 9 + 1))
        credit_name = self.user_list[credit]["name"] if credit is not None else ""
        data = {
            "csrf": _session_csrf(user["client"]),
            "title": title,
            "ingredients": "\n".join(ingredients),
            "steps": "Stir the pot. Serve warm.",
            "servings": "4",
            "credited_to": credit_name,
        }
        if n_photos:
            data["photos"] = [
                (io.BytesIO(_jpeg((40 * i, 90, 200 - 40 * i))), "pic%d.jpg" % i)
                for i in range(n_photos)
            ]
        response = user["client"].post("/add", data=data)
        assert response.status_code == 302, response.get_data(as_text=True)[:300]
        slug = _path(response).rsplit("/", 1)[-1]

        conn = db.connect()
        try:
            recipe_id = conn.execute(
                "SELECT id FROM recipes WHERE slug = ?", (slug,)
            ).fetchone()["id"]
            photo_rows = conn.execute(
                "SELECT id, filename FROM photos WHERE recipe_id = ?", (recipe_id,)
            ).fetchall()
        finally:
            conn.close()
        assert recipe_id not in self.recipe_model
        assert len(photo_rows) == n_photos
        self.recipes_added += 1
        self.photos_added += n_photos
        self.recipe_model[recipe_id] = {
            "slug": slug,
            "title": title,
            "owner": user["id"],
            "credit": credit_name,
            "ingredients": ingredients,
            "photos": {row["id"]: row["filename"] for row in photo_rows},
        }
        self.last_slug[recipe_id] = slug
        return recipe_id

    def _edit_post(self, user, recipe_id, title, ingredients, credit, remove=None):
        data = {
            "csrf": _session_csrf(user["client"]),
            "title": title,
            "ingredients": "\n".join(ingredients),
            "steps": "Stir the pot. Serve warm.",
            "servings": "4",
            "credited_to": credit,
        }
        if remove is not None:
            data["remove_photo"] = str(remove)
        return user["client"].post(
            "/recipe/%s/edit" % self.last_slug[recipe_id], data=data
        )

    @rule(actor=users, recipe=recipes)
    def attempt_edit(self, actor, recipe):
        """Change title, ingredients and credit (credit = the actor's own name)."""
        user = self.user_list[actor]
        n = self._next()
        title = "Edited Stew %d" % n
        ingredients = ("%d cups broth" % (n % 9 + 1),)
        live = recipe in self.recipe_model
        allowed = live and self._can_change_recipe(user, recipe)
        old_slug = self.last_slug[recipe]
        self._record("edit", live, allowed)

        response = self._edit_post(user, recipe, title, ingredients, user["name"])

        if not live:
            assert response.status_code == 404
        elif allowed:
            assert response.status_code == 302
            new_slug = _path(response).rsplit("/", 1)[-1]
            entry = self.recipe_model[recipe]
            entry.update(slug=new_slug, title=title, ingredients=ingredients)
            entry["credit"] = user["name"]
            self.last_slug[recipe] = new_slug
        else:
            assert response.status_code == 302
            assert _path(response) == "/recipe/%s" % old_slug

    @rule(actor=users, recipe=recipes, own=st.booleans(), pick=st.integers(0, 50))
    def attempt_remove_photo(self, actor, recipe, own, pick):
        """Edit POST with remove_photo; every other field is resent unchanged.
        The photo id is one of this recipe's (own) or any photo in the model,
        including other people's recipes, or a missing id if there are none."""
        user = self.user_list[actor]
        live = recipe in self.recipe_model
        allowed = live and self._can_change_recipe(user, recipe)
        own_ids = sorted(self.recipe_model[recipe]["photos"]) if live else []
        all_ids = sorted(
            pid for entry in self.recipe_model.values() for pid in entry["photos"]
        )
        candidates = own_ids if (own and own_ids) else all_ids
        photo_id = candidates[pick % len(candidates)] if candidates else 999999

        if live:
            entry = self.recipe_model[recipe]
            title, ingredients, credit = (
                entry["title"],
                entry["ingredients"],
                entry["credit"],
            )
        else:
            title, ingredients, credit = "Gone Dish", ("1 cup air",), ""
        old_slug = self.last_slug[recipe]
        own_photo = live and photo_id in self.recipe_model[recipe]["photos"]
        self._record(
            "remove_photo (%s)" % ("own" if own_photo else "other/missing"),
            live,
            allowed,
        )

        response = self._edit_post(
            user, recipe, title, ingredients, credit, remove=photo_id
        )

        if not live:
            assert response.status_code == 404
            return
        assert response.status_code == 302
        assert _path(response) == "/recipe/%s" % old_slug
        if allowed:
            # detach() only removes photos that belong to the edited recipe.
            self.recipe_model[recipe]["photos"].pop(photo_id, None)

    @rule(actor=users, recipe=recipes)
    def attempt_delete(self, actor, recipe):
        user = self.user_list[actor]
        live = recipe in self.recipe_model
        allowed = live and self._can_change_recipe(user, recipe)
        self._record("delete", live, allowed)
        response = user["client"].post(
            "/recipe/%s/delete" % self.last_slug[recipe],
            data={"csrf": _session_csrf(user["client"])},
        )
        if not live:
            assert response.status_code == 404
        elif allowed:
            assert response.status_code == 302
            del self.recipe_model[recipe]
            for note_id in [
                nid for nid, (rid, _) in self.note_model.items() if rid == recipe
            ]:
                del self.note_model[note_id]
        else:
            assert response.status_code == 403

    # -- notes -----------------------------------------------------------------

    @precondition(lambda self: self.notes_added < MAX_NOTES)
    @rule(target=notes, author=users, recipe=recipes)
    def add_note(self, author, recipe):
        user = self.user_list[author]
        body = "Synthetic note %d, very tasty." % self._next()
        response = user["client"].post(
            "/recipe/%s/comment" % self.last_slug[recipe],
            data={"csrf": _session_csrf(user["client"]), "comment": body},
        )
        if recipe not in self.recipe_model:
            assert response.status_code == 404
            return multiple()
        assert response.status_code == 302
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id FROM comments WHERE user_id = ? AND body = ?",
                (user["id"], body),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "note was not stored"
        self.notes_added += 1
        self.note_model[row["id"]] = (recipe, user["id"])
        return row["id"]

    @rule(actor=users, note=notes)
    def attempt_delete_note(self, actor, note):
        user = self.user_list[actor]
        live = note in self.note_model
        self._record(
            "delete_note",
            live,
            live and (user["admin"] or self.note_model[note][1] == user["id"]),
        )
        response = user["client"].post(
            "/comment/%d/delete" % note,
            data={"csrf": _session_csrf(user["client"])},
        )
        if note not in self.note_model:
            assert response.status_code == 404
        elif user["admin"] or self.note_model[note][1] == user["id"]:
            assert response.status_code == 302
            del self.note_model[note]
        else:
            assert response.status_code == 403

    # -- the property ----------------------------------------------------------

    @invariant()
    def database_matches_model(self):
        if not hasattr(self, "data_dir"):
            return
        conn = db.connect()
        try:
            recipes = {
                row["id"]: (
                    row["slug"],
                    row["title"],
                    row["submitted_by_id"],
                    row["credited_to"],
                )
                for row in conn.execute(
                    "SELECT id, slug, title, submitted_by_id, credited_to FROM recipes"
                )
            }
            ingredients = {}
            for row in conn.execute(
                "SELECT recipe_id, raw FROM ingredients ORDER BY recipe_id, position"
            ):
                ingredients.setdefault(row["recipe_id"], []).append(row["raw"])
            photo_rows = {
                row["id"]: (row["recipe_id"], row["filename"])
                for row in conn.execute("SELECT id, recipe_id, filename FROM photos")
            }
            notes = {
                row["id"]: (row["recipe_id"], row["user_id"])
                for row in conn.execute("SELECT id, recipe_id, user_id FROM comments")
            }
        finally:
            conn.close()

        expected_recipes = {
            rid: (e["slug"], e["title"], e["owner"], e["credit"])
            for rid, e in self.recipe_model.items()
        }
        expected_ingredients = {
            rid: list(e["ingredients"]) for rid, e in self.recipe_model.items()
        }
        expected_photos = {
            pid: (rid, filename)
            for rid, e in self.recipe_model.items()
            for pid, filename in e["photos"].items()
        }
        assert recipes == expected_recipes
        assert ingredients == expected_ingredients
        assert photo_rows == expected_photos
        assert notes == self.note_model
        on_disk = set(os.listdir(photos_module.photo_dir()))
        assert on_disk == {filename for _, filename in expected_photos.values()}


OwnershipMachine.TestCase.settings = settings(stateful_step_count=STEP_COUNT)
TestOwnershipMachine = OwnershipMachine.TestCase


# --- R9: credited_to grants no rights ------------------------------------------

_CREDIT_ALPHABET = string.ascii_letters + " .'-" + "ÀÉéñöüŁ"
_NAME_VARIANTS = ["exact", "upper", "lower", "padded", "first", "last", "aunt"]


def _variant(name, how):
    return {
        "exact": name,
        "upper": name.upper(),
        "lower": name.lower(),
        "padded": "  %s  " % name,
        "first": name.split()[0],
        "last": name.split()[-1],
        "aunt": "Aunt %s" % name.split()[0],
    }[how]


credit_values = st.one_of(
    st.sampled_from(_NAME_VARIANTS).map(lambda how: ("actor", how)),
    st.text(alphabet=_CREDIT_ALPHABET, max_size=60).map(lambda t: ("text", t)),
    st.text(max_size=60).map(lambda t: ("text", t)),
)


def _snapshot():
    conn = db.connect()
    try:
        state = {
            table: [
                tuple(row)
                for row in conn.execute("SELECT * FROM %s ORDER BY rowid" % table)
            ]
            for table in (
                "recipes",
                "ingredients",
                "tags",
                "recipe_tags",
                "photos",
                "comments",
            )
        }
    finally:
        conn.close()
    directory = photos_module.photo_dir()
    files = {}
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), "rb") as handle:
            files[name] = hashlib.sha256(handle.read()).hexdigest()
    state["files"] = files
    return state


@pytest.fixture
def credit_world(make_user, sign_in, make_recipe):
    """Once per test (not per example): an owner's recipe with one photo and
    tags, and a signed-in non-owner, non-admin member."""
    owner = make_user("cora.owner@example.com", "Cora Ownerby")
    other = make_user("milo.member@example.com", "Milo Memberson")
    owner_client = sign_in(app_module.app.test_client(), owner)
    other_client = sign_in(app_module.app.test_client(), other)
    slug = make_recipe(
        owner_client,
        title="Synthetic Credit Pie",
        tags="pie, test",
        photos=[(io.BytesIO(_jpeg((120, 30, 30))), "pie.jpg")],
    )
    conn = db.connect()
    try:
        recipe = conn.execute(
            "SELECT * FROM recipes WHERE slug = ?", (slug,)
        ).fetchone()
        photo_id = conn.execute(
            "SELECT id FROM photos WHERE recipe_id = ?", (recipe["id"],)
        ).fetchone()["id"]
    finally:
        conn.close()
    return {
        "other": other,
        "client": other_client,
        "slug": slug,
        "recipe_id": recipe["id"],
        "photo_id": photo_id,
    }


@given(credit=credit_values)
@example(credit=("actor", "exact"))
def test_credited_to_never_grants_edit_or_delete(credit_world, credit):
    """R9. credited_to is stored by a direct DB update with the value
    check_credit() returns, i.e. exactly what the add/edit form would store.
    The non-owner's edit form also sends the same raw credit value."""
    other = credit_world["other"]
    kind, value = credit
    if kind == "actor":
        value = _variant(other.name, value)
    try:
        stored = moderation.check_credit(value)
    except moderation.Rejected:
        assume(False)

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE recipes SET credited_to = ? WHERE id = ?",
            (stored, credit_world["recipe_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    client = credit_world["client"]
    slug = credit_world["slug"]
    before = _snapshot()

    token = get_csrf(client, "/add")
    response = client.post(
        "/recipe/%s/edit" % slug,
        data={
            "csrf": token,
            "title": "Taken Over Pie",
            "ingredients": "5 cups sand",
            "steps": "Replaced.",
            "servings": "2",
            "tags": "changed",
            "credited_to": value,
            "remove_photo": str(credit_world["photo_id"]),
        },
    )
    assert response.status_code == 302
    assert _path(response) == "/recipe/%s" % slug
    assert _snapshot() == before

    response = client.post("/recipe/%s/delete" % slug, data={"csrf": token})
    assert response.status_code == 403
    assert _snapshot() == before
