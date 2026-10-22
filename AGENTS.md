# AGENTS.md — Valentine-Cookbook

Read by Claude Code (via CLAUDE.md), Codex and other agents. Project facts only; how agents work
(verify, specs, safety) comes from the web agent kit's rules. README.md has the long-form rationale.

## What this is
A family recipe box at valentinecookbook.com. Live, with real family members' names and emails in it.
Design rules, in priority order (app.py docstring): usable by a 5-year-old and a 105-year-old; nobody
does arithmetic; nobody has to moderate it.

Journeys:
- **Visitor** (not signed in): browse/search/filter recipes (`/`), read one (`/recipe/<slug>`), scale it
  (× ÷ buttons and servings stepper, in the browser), `/help`.
- **Family member** (signed in by emailed magic link, no passwords): add a recipe with up to 5 photos
  (`/add`), edit/delete **only recipes they submitted**, leave notes (comments) on any recipe, delete
  their own notes, see their recipes (`/me`).
- **Admin** (email listed in the `ADMIN_EMAILS` env var; there is no admin table or UI): can edit or
  delete anything and sees hidden recipes. Blocked users (`users.is_blocked`) are signed out.

## Layout
One flat Flask app, no packages, no build step, no JS framework, nothing loaded from another origin.
- `app.py` — every route, sign-in, sessions, CSRF, security headers, permission checks, form
  validation. `create_app()` registers routes inside `_register(app)`.
- `db.py` / `schema.sql` — SQLite access, schema, `migrate()`, slugs, settings, rate limiting.
- `units.py` — ingredient parsing (qty/unit/item) and fraction formatting (server side).
- `static/app.js` — browser-side scaling, text-size buttons, live ingredient preview (`/api/parse`).
  It **re-implements** the unit tables and fraction formatting from `units.py`; change both together.
- `moderation.py` + `blocked_words.txt` — automatic content checks; optional Claude Haiku layer
  (`MODERATION_AI=1` plus `ANTHROPIC_API_KEY`, fails open).
- `photos.py` — decode, orient, resize, strip metadata, re-encode as JPEG, store under random names.
- `mailer.py` — sign-in emails: `console` (default, prints the link to the log), `resend`, `smtp`.
- `templates/` — Jinja pages extending `base.html`. `static/style.css` — all styling.
- `test_cookbook.py` — unittest suite (parsing, formatting, moderation, photos, migrations, outbound
  User-Agent, credit names).

## Stack and conventions
- Python 3.12, Flask 3, Pillow + pillow-heif, gunicorn. Deps pinned in `requirements.txt`; there is
  no pyproject.toml. Stdlib `urllib` for outbound HTTP (no requests).
- **New route**: add it inside `_register()` in app.py. Signed-in only → start with
  `redirect_response = require_login()`. Ownership → `is_admin() or row["submitted_by_id"] == user["id"]`.
  Public listings filter `is_hidden = 0`. Template extends `base.html`.
- **Every POST** is checked against the session CSRF token by the `guard()` before_request hook, so
  every form needs `<input type="hidden" name="csrf" value="{{ csrf_token() }}">`.
- **User text**: length cap in `MAX_LENGTHS`, read with `field()`, passed through `moderation.check()`
  (names: `check_name` / `check_credit`). Rejections raise `moderation.Rejected` with a plain-language
  message shown to the user. Moderation is whole-word matching on purpose (molasses, Scunthorpe).
- SQL is always parameterised. The few `%s` interpolations in app.py/db.py are fixed column names and
  ORDER BY choices, never user input.
- Strict CSP (`script-src 'self'`, `style-src 'self'`): no inline scripts, styles or event handlers;
  no CDNs. JS goes in `static/app.js`, CSS in `static/style.css`. DOM writes use `textContent`.
- Accessibility is a requirement: large text, ≥44px targets, works without JavaScript (except the
  scaling buttons), plain-language messages. No CAPTCHAs, ever.
- **Two people per recipe**: `submitted_by_id`/`submitted_by_name` (the account that typed it in, the
  only one who can edit it) vs `credited_to` (free-text display name, e.g. a grandmother with no
  account). Never use `credited_to` for anything but display and search.
- **New table**: `CREATE TABLE IF NOT EXISTS` in schema.sql. **Change to an existing table**: a
  check-before-act step in `db.migrate()` (runs on every start, must be idempotent) plus a test in
  `MigrationTests` that migrates an old-shape database without losing rows.
- **Testing**: stdlib unittest; `agent-verify fast` runs ruff (pyflakes rules) and every `test*.py`
  in the root. Tests never hit the network (outbound calls are stubbed by swapping
  `urllib.request.urlopen`) and set `DATA_DIR` to a temp dir. There are **no route/permission tests**
  (no Flask test client yet), **no tests for static/app.js**, and **no browser/e2e suite**: UI and
  authorization changes are untested unless you add tests for them. No CI workflows exist.
- Run locally: `uv run --no-project --with-requirements requirements.txt flask --app app run
  --host 127.0.0.1 --port 5055`. Sign-in links print to the terminal (console mail provider).

## Data and environments
- Local: SQLite at `data/cookbook.db` plus `data/photos/` (gitignored; created on first import of
  app.py). `DATA_DIR` overrides the location. Starts empty; there is no seed script. Use synthetic
  data only (example.com emails, made-up names).
- Production: the SQLite file and photos on the Render disk `cookbook-data` mounted at `/var/data`.
  It holds real family emails, login-token hashes and possibly the stored `secret_key` setting. No
  automatic backups. Agents never touch it, and never copy a backup of it into this repo.
- Migrations happen in `db.migrate()` at app start, so deploying a schema change migrates prod
  automatically. Treat every schema.sql / db.py change as a production migration.

## Deploy
- Render web service `valentine-cookbook` from the `render.yaml` blueprint, **`autoDeploy: true`: a push
  to `main` deploys production.** Domain valentinecookbook.com. Secrets (`RESEND_API_KEY`,
  `ADMIN_EMAILS`, optional `ANTHROPIC_API_KEY`) live in the Render dashboard.
- `Procfile` (Railway) and `Dockerfile` (Fly.io) are documented alternatives, not in use.
- External services: Resend (sign-in email), Anthropic API (only if `MODERATION_AI=1`).
- Agents make **no** deploys, including previews.

## Never
- Never push (any `git push` can deploy prod; blocked in `.kit/deny-commands`).
- Never run the Render CLI (blocked).
- Never send real email: no `MAIL_PROVIDER=resend|smtp`, no calls to api.resend.com (blocked).
  Locally the console provider is the only one used.
- Never call the real Anthropic API from tests; stub `urlopen` like `OutboundRequestTests` does.
- Never render user text as HTML (`|safe`, `Markup`, `innerHTML` with user data) or loosen the CSP.
- Never let `credited_to`, a form field, or anything but `submitted_by_id`/`ADMIN_EMAILS` grant edit rights.
- Never store an uploaded file as it arrived or use its filename; everything goes through `photos.save()`.
- Never add a CAPTCHA or a third-party script/font/CDN.

## Known traps
- `app = create_app()` runs at import: importing app.py runs `db.init()`, creates `DATA_DIR` and
  writes a `secret_key` setting. Set `DATA_DIR` to a temp dir **before** `import app` in tests.
- `python app.py` binds `0.0.0.0`; use the `flask ... --host 127.0.0.1` command above instead.
- Don't point `ENV_FALLBACK_FROM` at `.env.example`: it sets `MAIL_PROVIDER=resend`,
  `DATA_DIR=/var/data` and `FORCE_HTTPS=1` (https-only cookie breaks local sign-in). Nothing here
  reads `.env` files anyway; config comes from the process environment.
- Every outbound urllib request must send a real `User-Agent`. The default `Python-urllib/3.x` is
  rejected by Cloudflare (error 1010) in front of Resend. This silently killed every sign-in email
  once. Tests enforce it.
- Changing a unit conversion or fraction rule in `units.py` without the same change in
  `static/app.js` makes the page show one amount, then a different one once someone scales it.
- `client_ip()` trusts the first `X-Forwarded-For` value, so the per-IP sign-in rate limit can be
  spoofed. Known, not yet addressed.
- Timestamps are naive UTC ISO strings compared as text (`expires_at < db.now()`); keep that format.
- README says 48 tests; there are 50.
