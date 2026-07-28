"""Valentine Cookbook -- a family recipe box that runs itself.

Design rules, in priority order:
  1. A five-year-old and a hundred-and-five-year-old can both use it.
  2. Nobody has to do arithmetic.
  3. Nobody has to moderate it.
"""

import hashlib
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta

from flask import (
    Flask, abort, flash, g, redirect, render_template, request,
    send_from_directory, session, url_for
)

import db
import mailer
import moderation
import photos
import units

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cookbook")

SITE_NAME = os.environ.get("SITE_NAME", "Valentine Cookbook")
TOKEN_MINUTES = 45
SESSION_DAYS = 365

# Suggestions only -- every one of these fields also accepts free text, because
# somebody's grandmother is going to type a cuisine we did not think of.
CUISINES = [
    "American", "Italian", "Mexican", "Chinese", "Indian", "French", "Greek",
    "Japanese", "Thai", "Southern", "Cajun", "German", "Polish", "Irish",
    "Mediterranean", "Middle Eastern", "Korean", "Vietnamese", "Spanish",
    "Caribbean", "Soul Food", "Jewish", "Scandinavian", "Filipino",
]
MEALS = [
    "Breakfast", "Lunch", "Dinner", "Appetizer", "Side Dish", "Soup", "Salad",
    "Bread", "Dessert", "Snack", "Drink", "Sauce", "Holiday",
]

# The one photo that represents a recipe in a list, as a scalar subquery so
# the card grid stays a single trip to the database.
FIRST_PHOTO = (
    "(SELECT p.filename FROM photos p WHERE p.recipe_id = r.id "
    "ORDER BY p.position, p.id LIMIT 1)"
)

MAX_LENGTHS = {
    "title": 120, "description": 600, "cuisine": 40, "meal": 40,
    "servings_label": 30, "steps": 12000, "notes": 3000, "source": 120,
    "ingredients": 8000, "tags": 200, "comment": 2000,
}


def create_app():
    app = Flask(__name__)
    db.init()
    app.secret_key = _secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FORCE_HTTPS", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
        # Room for five full-size phone photos in one submission. Everything
        # else is capped far lower by the per-field length limits.
        MAX_CONTENT_LENGTH=photos.MAX_PER_RECIPE * photos.MAX_UPLOAD_BYTES + (1024 * 1024),
    )
    app.teardown_appcontext(db.close)
    _register(app)
    return app


def _secret_key():
    """Use SECRET_KEY if given, otherwise make one and keep it in the database.

    Storing it means sessions survive a restart even with zero configuration,
    which is one less thing that can go wrong on deploy day.
    """
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    conn = db.connect()
    try:
        stored = db.setting(conn, "secret_key")
        if not stored:
            stored = secrets.token_hex(32)
            db.set_setting(conn, "secret_key", stored)
            conn.commit()
        return stored
    finally:
        conn.close()


# --- people ----------------------------------------------------------------

def admin_emails():
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def current_user():
    if "user" in g:
        return g.user
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        row = db.get().execute(
            "SELECT * FROM users WHERE id = ? AND is_blocked = 0", (user_id,)
        ).fetchone()
        g.user = row
        if row is None:
            session.pop("user_id", None)
    return g.user


def is_admin(user=None):
    user = user or current_user()
    return bool(user) and user["email"].lower() in admin_emails()


def require_login():
    if current_user() is None:
        session["next_url"] = request.full_path if request.method == "GET" else None
        flash("Please sign in first. It only takes a moment.", "info")
        return redirect(url_for("login"))
    return None


# --- form safety -----------------------------------------------------------

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def safe_referrer():
    """Where to send someone after a rejected POST.

    Never trust Referer for a redirect target -- an off-site value would turn
    this into an open redirect, which is the classic way phishing links get to
    borrow a trusted domain.
    """
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return referrer
    return url_for("index")


def client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_bot_traps(form):
    """A honeypot field plus a minimum fill time. No CAPTCHAs, ever --
    they are exactly the wall that stops a 90-year-old and not a script."""
    if form.get("website"):
        raise moderation.Rejected("Submission blocked.")
    try:
        started = int(form.get("started_at", "0"))
    except ValueError:
        started = 0
    if started and (time.time() - started) < 2.0:
        raise moderation.Rejected("That was submitted very fast. Please try again.")


def field(form, name, required=False, label=None):
    value = (form.get(name) or "").strip()
    limit = MAX_LENGTHS.get(name)
    label = label or name.replace("_", " ")
    if required and not value:
        raise moderation.Rejected("Please fill in the %s." % label)
    if limit and len(value) > limit:
        raise moderation.Rejected(
            "The %s is too long (limit %d characters)." % (label, limit)
        )
    return value


def optional_int(form, name, label, low=0, high=10000):
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        raise moderation.Rejected("Please enter %s as a number." % label)
    if not low <= value <= high:
        raise moderation.Rejected("Please enter a realistic %s." % label)
    return value


# --- rendering helpers -----------------------------------------------------

def step_lines(text):
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lines.append(re.sub(r"^\s*(?:\d+[.)]|[-*\u2022])\s*", "", line))
    return lines


def pretty_time(minutes):
    if not minutes:
        return ""
    hours, rest = divmod(int(minutes), 60)
    if hours and rest:
        return "%d hr %d min" % (hours, rest)
    if hours:
        return "%d hr" % hours
    return "%d min" % rest


def amount_text(row):
    """The '1 1/2 cups' part of an ingredient, rendered without JavaScript.

    The browser rewrites this the moment somebody scales the recipe, but it has
    to be correct on its own so the page reads properly before (or without) any
    scripting.
    """
    if row["qty"] is None:
        return ""
    text = units.format_amount(row["qty"])
    if row["qty_max"]:
        text += "-" + units.format_amount(row["qty_max"])
    label = units.unit_label(row["unit"], row["qty_max"] or row["qty"])
    return (text + " " + label).strip() if label else text


def credited(row):
    """Whose recipe this is: the credited name, or the submitter's own name.

    Most people are adding their own cooking, so leaving the credit blank has
    to do the sensible thing rather than leave a recipe with no name on it.
    """
    try:
        credit = row["credited_to"]
    except (IndexError, KeyError):
        credit = ""
    return credit or row["submitted_by_name"]


def relative_date(stamp):
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ""
    days = (datetime.utcnow() - when).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return "%d days ago" % days
    return when.strftime("%B %-d, %Y") if os.name != "nt" else when.strftime("%B %d, %Y")


# --- routes ----------------------------------------------------------------

def _register(app):

    @app.context_processor
    def inject():
        return {
            "site_name": SITE_NAME,
            "user": current_user(),
            "is_admin": is_admin(),
            "csrf_token": csrf_token,
            "cuisines": CUISINES,
            "meals": MEALS,
            "now_ts": int(time.time()),
            "pretty_time": pretty_time,
            "relative_date": relative_date,
            "amount_text": amount_text,
            "credited": credited,
        }

    @app.before_request
    def guard():
        session.permanent = True
        if request.method == "POST":
            sent = request.form.get("csrf")
            if not sent or sent != session.get("csrf"):
                flash(
                    "That form could not be submitted, usually because the page "
                    "sat open for a long time. Please try once more. If it keeps "
                    "happening, turn cookies on for this website.",
                    "error",
                )
                return redirect(safe_referrer())
        return None

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        # Everything is served from this origin, so the policy can be strict.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'"
        )
        return response

    # -- browsing ----------------------------------------------------------

    @app.route("/")
    def index():
        conn = db.get()
        query = (request.args.get("q") or "").strip()
        cuisine = (request.args.get("cuisine") or "").strip()
        meal = (request.args.get("meal") or "").strip()
        ingredient = (request.args.get("ingredient") or "").strip()
        tag = (request.args.get("tag") or "").strip()
        sort = request.args.get("sort") or "new"

        where = ["r.is_hidden = 0"]
        params = []
        if query:
            where.append("r.search_blob LIKE ?")
            params.append("%" + query.lower() + "%")
        if cuisine:
            where.append("LOWER(r.cuisine) = ?")
            params.append(cuisine.lower())
        if meal:
            where.append("LOWER(r.meal) = ?")
            params.append(meal.lower())
        if ingredient:
            where.append(
                "EXISTS (SELECT 1 FROM ingredients i WHERE i.recipe_id = r.id "
                "AND LOWER(i.item) LIKE ?)"
            )
            params.append("%" + ingredient.lower() + "%")
        if tag:
            where.append(
                "EXISTS (SELECT 1 FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id "
                "WHERE rt.recipe_id = r.id AND LOWER(t.name) = ?)"
            )
            params.append(tag.lower())

        order = "r.title COLLATE NOCASE ASC" if sort == "az" else "r.created_at DESC"
        rows = conn.execute(
            # No paging: a family cookbook that reaches a thousand recipes is a
            # happy problem, and one long page is far easier to use than pages.
            "SELECT r.*, " + FIRST_PHOTO + " AS photo "
            "FROM recipes r WHERE %s ORDER BY %s LIMIT 1000"
            % (" AND ".join(where), order),
            params,
        ).fetchall()

        active = {
            k: v for k, v in
            {"q": query, "cuisine": cuisine, "meal": meal,
             "ingredient": ingredient, "tag": tag}.items() if v
        }
        return render_template(
            "index.html",
            recipes=rows,
            query=query, cuisine=cuisine, meal=meal,
            ingredient=ingredient, tag=tag, sort=sort,
            active=active,
            used_cuisines=_distinct(conn, "cuisine"),
            used_meals=_distinct(conn, "meal"),
            popular_tags=_popular_tags(conn),
            total=_count(conn),
        )

    def _distinct(conn, column):
        rows = conn.execute(
            "SELECT %s AS v, COUNT(*) AS n FROM recipes WHERE is_hidden = 0 "
            "AND %s <> '' GROUP BY LOWER(%s) ORDER BY n DESC, v ASC"
            % (column, column, column)
        ).fetchall()
        return [(r["v"], r["n"]) for r in rows]

    def _popular_tags(conn):
        rows = conn.execute(
            "SELECT t.name AS v, COUNT(*) AS n FROM tags t "
            "JOIN recipe_tags rt ON rt.tag_id = t.id "
            "JOIN recipes r ON r.id = rt.recipe_id AND r.is_hidden = 0 "
            "GROUP BY t.id ORDER BY n DESC, t.name ASC LIMIT 24"
        ).fetchall()
        return [(r["v"], r["n"]) for r in rows]

    def _count(conn):
        return conn.execute(
            "SELECT COUNT(*) AS n FROM recipes WHERE is_hidden = 0"
        ).fetchone()["n"]

    @app.route("/recipe/<slug>")
    def recipe(slug):
        conn = db.get()
        row = conn.execute("SELECT * FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if row is None or (row["is_hidden"] and not is_admin()):
            abort(404)
        ingredients = conn.execute(
            "SELECT * FROM ingredients WHERE recipe_id = ? ORDER BY position",
            (row["id"],),
        ).fetchall()
        comments = conn.execute(
            "SELECT * FROM comments WHERE recipe_id = ? AND is_hidden = 0 "
            "ORDER BY created_at ASC",
            (row["id"],),
        ).fetchall()
        tags = conn.execute(
            "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id "
            "WHERE rt.recipe_id = ? ORDER BY t.name",
            (row["id"],),
        ).fetchall()
        user = current_user()
        can_edit = is_admin() or (
            user is not None and row["submitted_by_id"] == user["id"]
        )
        return render_template(
            "recipe.html",
            r=row,
            ingredients=ingredients,
            comments=comments,
            tags=[t["name"] for t in tags],
            steps=step_lines(row["steps"]),
            pictures=photos.for_recipe(conn, row["id"]),
            can_edit=can_edit,
        )

    # -- adding and editing ------------------------------------------------

    @app.route("/add", methods=["GET", "POST"])
    def add():
        redirect_response = require_login()
        if redirect_response:
            return redirect_response
        if request.method == "GET":
            return render_template("edit_recipe.html", r=None, values={}, mode="add")
        try:
            values = _read_recipe_form(request.form)
            conn = db.get()
            user = current_user()
            bucket = "add:%s" % user["id"]
            if not db.rate_ok(conn, bucket, limit=12, minutes=60):
                raise moderation.Rejected(
                    "That is a lot of recipes at once. Please wait an hour and "
                    "add the rest -- they are worth the wait."
                )
            slug = db.unique_slug(conn, values["title"])
            cursor = conn.execute(
                "INSERT INTO recipes(slug, title, description, cuisine, meal, "
                "servings, servings_label, prep_minutes, cook_minutes, steps, "
                "notes, source, submitted_by_id, submitted_by_name, credited_to, "
                "created_at, updated_at, "
                "search_blob) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    slug, values["title"], values["description"], values["cuisine"],
                    values["meal"], values["servings"], values["servings_label"],
                    values["prep_minutes"], values["cook_minutes"], values["steps"],
                    values["notes"], values["source"], user["id"],
                    user["full_name"], values["credited_to"],
                    db.now(), db.now(), "",
                ),
            )
            recipe_id = cursor.lastrowid
            _save_ingredients(conn, recipe_id, values["parsed"])
            _save_tags(conn, recipe_id, values["tags"])
            _refresh_search(conn, recipe_id)
            db.rate_hit(conn, bucket)
            problems = photos.attach(
                conn, recipe_id, request.files.getlist("photos")
            )
            conn.commit()
            flash("Thank you! Your recipe is now in the cookbook.", "success")
            for problem in problems:
                flash(problem, "info")
            return redirect(url_for("recipe", slug=slug))
        except moderation.Rejected as exc:
            flash(str(exc), "error")
            return render_template(
                "edit_recipe.html", r=None, values=request.form, mode="add"
            )

    @app.route("/recipe/<slug>/edit", methods=["GET", "POST"])
    def edit(slug):
        redirect_response = require_login()
        if redirect_response:
            return redirect_response
        conn = db.get()
        row = conn.execute("SELECT * FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            abort(404)
        user = current_user()
        if not (is_admin() or row["submitted_by_id"] == user["id"]):
            flash(
                "Only the person who added a recipe can change it. You can "
                "leave a note in the comments instead.",
                "info",
            )
            return redirect(url_for("recipe", slug=slug))

        if request.method == "GET":
            ingredients = conn.execute(
                "SELECT raw FROM ingredients WHERE recipe_id = ? ORDER BY position",
                (row["id"],),
            ).fetchall()
            tags = conn.execute(
                "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id "
                "WHERE rt.recipe_id = ? ORDER BY t.name",
                (row["id"],),
            ).fetchall()
            values = dict(row)
            values["ingredients"] = "\n".join(i["raw"] for i in ingredients)
            values["tags"] = ", ".join(t["name"] for t in tags)
            return render_template(
                "edit_recipe.html", r=row, values=values, mode="edit",
                pictures=photos.for_recipe(conn, row["id"]),
            )

        try:
            values = _read_recipe_form(request.form)
            new_slug = db.unique_slug(conn, values["title"], ignore_id=row["id"])
            conn.execute(
                "UPDATE recipes SET slug=?, title=?, description=?, cuisine=?, "
                "meal=?, servings=?, servings_label=?, prep_minutes=?, "
                "cook_minutes=?, steps=?, notes=?, source=?, credited_to=?, "
                "updated_at=? WHERE id=?",
                (
                    new_slug, values["title"], values["description"],
                    values["cuisine"], values["meal"], values["servings"],
                    values["servings_label"], values["prep_minutes"],
                    values["cook_minutes"], values["steps"], values["notes"],
                    values["source"], values["credited_to"], db.now(), row["id"],
                ),
            )
            conn.execute("DELETE FROM ingredients WHERE recipe_id = ?", (row["id"],))
            conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (row["id"],))
            _save_ingredients(conn, row["id"], values["parsed"])
            _save_tags(conn, row["id"], values["tags"])
            _refresh_search(conn, row["id"])
            photos.detach(conn, row["id"], request.form.getlist("remove_photo"))
            problems = photos.attach(
                conn, row["id"], request.files.getlist("photos")
            )
            conn.commit()
            flash("Your changes were saved.", "success")
            for problem in problems:
                flash(problem, "info")
            return redirect(url_for("recipe", slug=new_slug))
        except moderation.Rejected as exc:
            flash(str(exc), "error")
            return render_template(
                "edit_recipe.html", r=row, values=request.form, mode="edit",
                pictures=photos.for_recipe(conn, row["id"]),
            )

    @app.route("/recipe/<slug>/delete", methods=["POST"])
    def delete(slug):
        redirect_response = require_login()
        if redirect_response:
            return redirect_response
        conn = db.get()
        row = conn.execute("SELECT * FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            abort(404)
        user = current_user()
        if not (is_admin() or row["submitted_by_id"] == user["id"]):
            abort(403)
        # The photo rows cascade with the recipe, but the files on disk would
        # be left behind, so they have to go first.
        photos.detach_all(conn, row["id"])
        conn.execute("DELETE FROM recipes WHERE id = ?", (row["id"],))
        conn.commit()
        flash("That recipe was removed.", "success")
        return redirect(url_for("index"))

    def _read_recipe_form(form):
        check_bot_traps(form)
        title = field(form, "title", required=True, label="recipe name")
        moderation.check(title, "recipe title")
        description = moderation.check(
            field(form, "description"), "recipe description"
        )
        steps = moderation.check(
            field(form, "steps", required=True, label="directions"), "directions"
        )
        notes = moderation.check(field(form, "notes"), "notes")
        source = moderation.check(field(form, "source"), "source")
        raw_ingredients = field(
            form, "ingredients", required=True, label="ingredients"
        )
        moderation.check(raw_ingredients, "ingredients")

        parsed = []
        for line in raw_ingredients.splitlines():
            line = line.strip()
            if line:
                parsed.append(units.parse_ingredient(line))
        if not parsed:
            raise moderation.Rejected("Please list at least one ingredient.")
        if len(parsed) > 120:
            raise moderation.Rejected("That is more than 120 ingredient lines.")

        servings_raw = (form.get("servings") or "").strip()
        servings = None
        if servings_raw:
            try:
                servings = float(servings_raw)
            except ValueError:
                raise moderation.Rejected("Please enter servings as a number.")
            if not 0 < servings <= 1000:
                raise moderation.Rejected("Please enter a realistic number of servings.")

        tags = []
        for piece in re.split(r"[,\n]", field(form, "tags")):
            piece = piece.strip().lower()
            piece = re.sub(r"[^a-z0-9 &'-]+", "", piece)[:30].strip()
            if piece and piece not in tags:
                tags.append(piece)
        if tags:
            moderation.check(" ".join(tags), "tags")

        return {
            "title": title,
            "description": description,
            "cuisine": field(form, "cuisine")[:40],
            "meal": field(form, "meal")[:40],
            "credited_to": moderation.check_credit(form.get("credited_to")),
            "servings": servings,
            "servings_label": field(form, "servings_label") or "servings",
            "prep_minutes": optional_int(form, "prep_minutes", "prep time"),
            "cook_minutes": optional_int(form, "cook_minutes", "cook time"),
            "steps": steps,
            "notes": notes,
            "source": source,
            "tags": tags[:12],
            "parsed": parsed,
        }

    def _save_ingredients(conn, recipe_id, parsed):
        for position, item in enumerate(parsed):
            conn.execute(
                "INSERT INTO ingredients(recipe_id, position, raw, qty, qty_max, "
                "unit, item, is_heading) VALUES(?,?,?,?,?,?,?,?)",
                (
                    recipe_id, position, item["raw"], item["qty"], item["qty_max"],
                    item["unit"], item["item"], 1 if item["is_heading"] else 0,
                ),
            )

    def _save_tags(conn, recipe_id, tags):
        for name in tags:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
            row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO recipe_tags(recipe_id, tag_id) VALUES(?,?)",
                (recipe_id, row["id"]),
            )

    def _refresh_search(conn, recipe_id):
        """One denormalized lowercase column keeps search a single LIKE."""
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        items = conn.execute(
            "SELECT raw FROM ingredients WHERE recipe_id = ?", (recipe_id,)
        ).fetchall()
        tags = conn.execute(
            "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id "
            "WHERE rt.recipe_id = ?", (recipe_id,)
        ).fetchall()
        blob = " ".join([
            row["title"], row["description"], row["cuisine"], row["meal"],
            row["notes"], row["source"], row["submitted_by_name"],
            row["credited_to"],
            " ".join(i["raw"] for i in items),
            " ".join(t["name"] for t in tags),
        ]).lower()
        conn.execute(
            "UPDATE recipes SET search_blob = ? WHERE id = ?", (blob, recipe_id)
        )

    # -- comments ----------------------------------------------------------

    @app.route("/recipe/<slug>/comment", methods=["POST"])
    def comment(slug):
        redirect_response = require_login()
        if redirect_response:
            return redirect_response
        conn = db.get()
        row = conn.execute("SELECT * FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            abort(404)
        user = current_user()
        try:
            check_bot_traps(request.form)
            body = field(form=request.form, name="comment", required=True,
                         label="note")
            bucket = "comment:%s" % user["id"]
            if not db.rate_ok(conn, bucket, limit=15, minutes=60):
                raise moderation.Rejected(
                    "You have added several notes in a row. Please take a short "
                    "break and come back."
                )
            duplicate = conn.execute(
                "SELECT id FROM comments WHERE user_id = ? AND body = ? "
                "AND recipe_id = ?",
                (user["id"], body, row["id"]),
            ).fetchone()
            if duplicate:
                raise moderation.Rejected("You already added that same note.")
            moderation.check(body, "comment")
            conn.execute(
                "INSERT INTO comments(recipe_id, user_id, author_name, body, "
                "created_at) VALUES(?,?,?,?,?)",
                (row["id"], user["id"], user["full_name"], body, db.now()),
            )
            db.rate_hit(conn, bucket)
            conn.commit()
            flash("Thank you for the note!", "success")
        except moderation.Rejected as exc:
            flash(str(exc), "error")
        return redirect(url_for("recipe", slug=slug) + "#notes")

    @app.route("/comment/<int:comment_id>/delete", methods=["POST"])
    def delete_comment(comment_id):
        redirect_response = require_login()
        if redirect_response:
            return redirect_response
        conn = db.get()
        row = conn.execute(
            "SELECT c.*, r.slug AS slug FROM comments c "
            "JOIN recipes r ON r.id = c.recipe_id WHERE c.id = ?",
            (comment_id,),
        ).fetchone()
        if row is None:
            abort(404)
        user = current_user()
        if not (is_admin() or row["user_id"] == user["id"]):
            abort(403)
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()
        flash("That note was removed.", "success")
        return redirect(url_for("recipe", slug=row["slug"]) + "#notes")

    # -- signing in --------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            return redirect(url_for("index"))
        if request.method == "GET":
            return render_template("login.html", values={})
        try:
            check_bot_traps(request.form)
            email = (request.form.get("email") or "").strip().lower()
            if not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", email) or len(email) > 160:
                raise moderation.Rejected(
                    "That email address does not look right. Please check it "
                    "and try again."
                )
            conn = db.get()
            existing = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing is None:
                name = moderation.check_name(request.form.get("full_name"))
                conn.execute(
                    "INSERT INTO users(email, full_name, created_at) VALUES(?,?,?)",
                    (email, name, db.now()),
                )
                existing = conn.execute(
                    "SELECT * FROM users WHERE email = ?", (email,)
                ).fetchone()
            elif existing["is_blocked"]:
                raise moderation.Rejected("That account cannot sign in.")

            for bucket, limit, minutes in (
                ("login-ip:%s" % client_ip(), 20, 60),
                ("login-user:%s" % existing["id"], 6, 60),
            ):
                if not db.rate_ok(conn, bucket, limit, minutes):
                    raise moderation.Rejected(
                        "Too many sign-in emails were requested. Please wait an "
                        "hour, then try again."
                    )
                db.rate_hit(conn, bucket)

            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO login_tokens(user_id, token_hash, created_at, "
                "expires_at) VALUES(?,?,?,?)",
                (
                    existing["id"], _hash(token), db.now(),
                    (datetime.utcnow() + timedelta(minutes=TOKEN_MINUTES))
                    .isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
            link = url_for("login_with_token", token=token, _external=True)
            mailer.send(
                email,
                "Your sign-in link for %s" % SITE_NAME,
                "Hello %s,\n\nClick the link below to sign in to %s:\n\n%s\n\n"
                "The link works once and expires in %d minutes.\n\n"
                "If you did not ask to sign in, you can ignore this email."
                % (existing["full_name"], SITE_NAME, link, TOKEN_MINUTES),
            )
            return render_template("check_email.html", email=email)
        except moderation.Rejected as exc:
            flash(str(exc), "error")
            return render_template("login.html", values=request.form)

    @app.route("/login/<token>")
    def login_with_token(token):
        conn = db.get()
        row = conn.execute(
            "SELECT * FROM login_tokens WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
        if row is None or row["used_at"] or row["expires_at"] < db.now():
            flash(
                "That sign-in link has expired or was already used. "
                "Please request a new one -- it only takes a moment.",
                "error",
            )
            return redirect(url_for("login"))
        conn.execute(
            "UPDATE login_tokens SET used_at = ? WHERE id = ?", (db.now(), row["id"])
        )
        conn.execute(
            "UPDATE users SET last_seen_at = ? WHERE id = ?", (db.now(), row["user_id"])
        )
        conn.commit()
        session["user_id"] = row["user_id"]
        session.permanent = True
        target = session.pop("next_url", None)
        flash("You are signed in. Welcome!", "success")
        if target and target.startswith("/") and not target.startswith("//"):
            return redirect(target)
        return redirect(url_for("index"))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You are signed out.", "info")
        return redirect(url_for("index"))

    @app.route("/me")
    def me():
        redirect_response = require_login()
        if redirect_response:
            return redirect_response
        conn = db.get()
        user = current_user()
        rows = conn.execute(
            "SELECT r.*, " + FIRST_PHOTO + " AS photo FROM recipes r "
            "WHERE r.submitted_by_id = ? ORDER BY r.created_at DESC",
            (user["id"],),
        ).fetchall()
        return render_template("me.html", recipes=rows)

    # -- odds and ends -----------------------------------------------------

    @app.route("/help")
    def help_page():
        return render_template("help.html")

    @app.route("/api/parse", methods=["POST"])
    def api_parse():
        """Powers the live preview on the add form so people can see how their
        ingredients were understood before they save."""
        if request.form.get("csrf") != session.get("csrf"):
            abort(400)
        lines = (request.form.get("ingredients") or "")[:8000].splitlines()
        out = []
        for line in lines[:120]:
            line = line.strip()
            if line:
                out.append(units.parse_ingredient(line))
        return {"ingredients": out}

    @app.route("/photo/<name>")
    def photo(name):
        """Photos live on the data disk, not in the static folder, so they are
        served by hand. The name pattern is checked before anything touches the
        filesystem -- these names are generated by us, so anything else is
        someone probing for a path traversal."""
        if not photos.FILENAME_RE.match(name):
            abort(404)
        response = send_from_directory(
            photos.photo_dir(), name, max_age=60 * 60 * 24 * 365
        )
        response.headers["Content-Type"] = "image/jpeg"
        return response

    @app.route("/healthz")
    def healthz():
        db.get().execute("SELECT 1")
        return {"ok": True}

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html",
                               title="We could not find that page",
                               message="It may have been removed or the link "
                                       "may have a typo in it."), 404

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html",
                               title="That is not yours to change",
                               message="You can always add a note in the "
                                       "comments instead."), 403

    @app.errorhandler(413)
    def too_big(_error):
        return render_template(
            "error.html",
            title="That submission was too large",
            message="If you were adding photos, try adding fewer at a time. "
                    "Otherwise, please shorten the recipe and try again."), 413

    @app.errorhandler(500)
    def server_error(error):
        log.exception("unhandled error: %s", error)
        return render_template("error.html",
                               title="Something went wrong on our end",
                               message="Please try again in a moment."), 500


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
