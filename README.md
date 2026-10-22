# Valentine Cookbook

A family recipe box for valentinecookbook.com. Anyone in the family can add a
recipe, find one, scale it without doing arithmetic, and leave notes on it.
It moderates itself.

Built to be boring on purpose: Python, Flask, and one SQLite file. There is no
build step, no JavaScript framework, and nothing loaded from another website.

---

## What it does

**Simple enough for anyone.** Large text, high contrast, one accent colour, no
menus that hide things. `A+` / `A−` buttons in the corner scale the whole site
and remember the choice. Every button is at least a 44-pixel target. It works
on a phone, and it works with JavaScript turned off (except the scaling
buttons).

**No math, ever.** Every ingredient line is parsed into a number, a unit, and
a food when it is saved. On the recipe page, `× 2`, `÷ 2`, `× 3`, `÷ 3` and a
servings stepper rewrite every amount instantly, as fractions a person would
say out loud — and in the unit they would actually measure with:

| As written | Halved | Tripled |
|---|---|---|
| 3/4 cup sugar | 6 tablespoons sugar | 2 1/4 cups sugar |
| 1 1/2 teaspoons cinnamon | 3/4 teaspoon cinnamon | 1 1/2 tablespoons cinnamon |
| 1/4 teaspoon nutmeg | 1/8 teaspoon nutmeg | 3/4 teaspoon nutmeg |

A third of a tablespoon becomes 1 teaspoon, not 0.33 tablespoons. Below an
eighth of a teaspoon it just says "a pinch of". Lines with no number in them
("salt to taste") are left exactly as written rather than guessed at.

**Finding things.** One search box covers recipe names, descriptions,
ingredients, notes, tags, and the name of the person who added it. On the front
page there are one-tap filters for course, kind of food, and tags, plus a
"has this ingredient" box.

**Adding things.** Sign-in is by emailed link — no password to forget, and the
site remembers you on that device for a year. Full name and email are required
to post anything. While you type ingredients, a panel beside the box shows how
the site read each line, so you can see it got the amounts right before saving.

**Who a recipe belongs to.** The site tracks two different people, because in a
family cookbook they are usually not the same one:

- the **submitter** — the account that typed it in, always a real signed-in
  person, and the only one who can edit it afterwards;
- **who it is credited to** — an optional free-text name, for when you are
  entering a recipe that is somebody else's.

Every recipe reads *Recipe Name à la Whoever*, so **Stuffed French Toast à la
Barb Valentine** with *Typed in by Trevor Gross* underneath. Leave the credit
blank for your own cooking and it is simply à la you. Credited names are
searchable, so "barb" finds her recipes even though she has no account — which
is the point: a grandmother who never used a computer, or is no longer with us,
still gets her name on her food.

**Photos.** Up to 5 per recipe. Nothing is stored as it arrived: every upload
is decoded, turned upright using its rotation tag, scaled to 720p class (1280 px
on the long edge), and re-encoded as a fresh JPEG. A 5 MB phone photo lands at
roughly 150–350 KB. HEIC from iPhones is accepted. Photo *content* is
deliberately not moderated, per your call.

They display as a banner across the top of the recipe, above the title. The
banner height comes down as the count goes up — one photo is a wide hero strip,
five are close to square — so no layout leaves you with slivers. Banner cells
crop to fill; clicking any photo opens the whole uncropped picture. On a phone
they wrap to at most two rows.

**Notes.** Signed-in family members can leave notes on any recipe — timing
changes, substitutions, how their household makes it. You can edit a recipe you
added; for anyone else's, you leave a note.

**Moderation, with nobody moderating.** Every submission is checked before it
posts. Rejections explain what to change, in plain language.

---

## Deploying it

### Render (recommended)

1. Push this folder to a GitHub repository.
2. In Render: **New → Blueprint**, choose the repo. It reads `render.yaml`.
3. Approve the plan. The **1 GB disk is the important part** — it holds the
   database. Without it, a redeploy would erase every recipe.
4. In the service's **Environment** tab set:
   - `ADMIN_EMAILS` — your email, so you can edit or remove anything.
   - `RESEND_API_KEY` — see "Email" below.
5. In **Settings → Custom Domain**, add `valentinecookbook.com` and follow the
   DNS instructions it gives you.

Cost is about $7/month for the service plus $0.25/month for the disk.
Railway works the same way using the `Procfile`; Fly.io uses the `Dockerfile`
(give it a volume mounted at `/var/data`).

### Email

Sign-in links need a way out. Without one the site still works — the links get
written to the server log instead — but nobody else can sign in.

1. Make a free account at resend.com (3,000 emails/month free; the family will
   use a few dozen).
2. Add `valentinecookbook.com` as a domain and add the DNS records it asks for.
3. Copy the API key into `RESEND_API_KEY`.
4. Set `MAIL_FROM` to something at that domain, e.g.
   `Valentine Cookbook <cookbook@valentinecookbook.com>`.

Any SMTP server works too — set `MAIL_PROVIDER=smtp` and the `SMTP_*` variables
instead. See `.env.example`.

### Settings

Every one of these is optional; see `.env.example` for the full list.

| Variable | What it does |
|---|---|
| `DATA_DIR` | Where the database file lives. **Must** be the disk mount path. |
| `ADMIN_EMAILS` | Comma-separated. These people can edit or delete anything. |
| `SECRET_KEY` | Signs the login cookie. Generated and stored if unset. |
| `MAIL_PROVIDER` | `console` (default), `resend`, or `smtp`. |
| `FORCE_HTTPS` | Set to `1` in production so the login cookie is https-only. |
| `MODERATION_AI` | `1` plus `ANTHROPIC_API_KEY` adds an AI moderation layer. |
| `TRUSTED_PROXY_HOPS` | How many proxies in front of the app append to X-Forwarded-For; default 1 (Render). |

---

## Running it here

```bash
uv sync
uv run flask --app app run --host 127.0.0.1 --port 5055
```

Open http://127.0.0.1:5055. With no mail provider configured, sign-in links are
printed to the terminal — copy the link and paste it into the browser.

```bash
uv run pytest -m "not e2e"                # server and unit tests
uv run playwright install chromium       # once, for the browser tests
uv run pytest -m e2e                      # browser tests
```

290 tests are collected: ingredient parsing, moderation, photo handling,
migrations, routes and permissions, and a browser suite that checks the scaling
in `static/app.js` against `units.py`. GitHub Actions runs the same checks on
every push.

The database and the `photos/` folder are created on first run, and both are
gitignored — the cookbook starts empty, here and in production.

**Schema changes.** `schema.sql` only ever creates tables that do not exist
yet, so it can never alter a database that already has recipes in it. Anything
that changes an existing table goes in `db.migrate()` in `db.py`, which runs on
every start, checks before it acts, and is safe to run repeatedly. That is what
makes it safe to deploy a schema change to a live site.

---

## How the moderation works

There is no review queue and nothing for you to approve. Checks run in order,
and the first one that trips explains itself to the submitter:

1. **Offensive language** — a wordlist in `blocked_words.txt`, matched after
   stripping accents, punctuation, leetspeak (`sh1t`), stretched letters
   (`shiiit`), and spacing tricks (`f u c k`).
2. **Threats and personal insults** — patterns aimed at *a person*. Criticising
   a recipe is explicitly allowed; "you are an idiot" is not.
3. **Spam** — advertising phrases, and any link, email address, or phone number
   in a comment.
4. **Shouting** — more than 70% capitals in a longer message.
5. **Bots** — a hidden honeypot field and a minimum fill time. Deliberately no
   CAPTCHA: a CAPTCHA is exactly the wall that stops a 90-year-old and not a
   script.
6. **Flooding** — rate limits per person (12 recipes/hour, 15 notes/hour,
   6 sign-in emails/hour) and per IP address, plus duplicate-note detection.

Matching is whole-word only. This is deliberate — substring matching is how
filters end up rejecting "molasses", "Scunthorpe", and "Assam tea". There are
tests for exactly those.

**To adjust the list**, edit `blocked_words.txt` (one word per line) and
redeploy. No code changes.

**For stronger nuance**, set `MODERATION_AI=1` and `ANTHROPIC_API_KEY`. Every
submission then also gets read by Claude Haiku for harassment, hate, and
off-topic content, at roughly pennies per month. It fails open: if the API is
unreachable, the checks above still run and the site keeps working.

---

## About that "any user can add recipes" question

You asked whether letting anyone post is a security risk. It is a manageable
one, and here is exactly what is and is not done about it.

**Handled:**

- Nothing a user writes is ever rendered as HTML, so a submitted `<script>` tag
  shows up as literal text. This is the big one.
- Every form is CSRF-protected; every database query is parameterised.
- Login links are random 32-byte tokens, stored hashed, single-use, 45-minute
  expiry. There are no passwords to leak.
- The login cookie is HttpOnly, SameSite=Lax, and https-only in production.
- A strict Content-Security-Policy — the page may load scripts, styles, and
  images only from itself, and nothing may frame it.
- **Photo uploads never touch disk in their original form.** Every one is
  decoded by Pillow, re-encoded as a new JPEG, and written under a random
  32-character name we generate — the uploaded filename is discarded entirely,
  so there is no path to traverse and no extension to smuggle. A file that only
  claims to be an image does not survive being decoded, and is refused with a
  polite message. Files are served from the data disk by a route that matches
  the name against a strict pattern before touching the filesystem, always as
  `image/jpeg`, with `nosniff` set.
- **Photo metadata is stripped**, which matters more than it sounds: phone
  photos carry the GPS coordinates of the kitchen they were taken in, and those
  must not end up on a public website. There are tests asserting it is gone.
- Decompression bombs are capped at 50 megapixels; uploads at 12 MB each, five
  per recipe.
- Redirects after a rejected form are checked against the site's own host, so
  the site cannot be used to bounce someone to a phishing page.
- Rate limits on posting and on sign-in emails.

**Accepted, knowingly:**

- **Anyone with an email address can sign in and post.** That is the point — it
  is what makes it usable by relatives who will not manage an account. The
  moderation filter, not the sign-in, is what keeps the content clean.
- **Someone determined can still get something unpleasant past a wordlist.**
  With no moderator, the backstop is that you are an admin: you can delete any
  recipe or note from the page itself. Turning on `MODERATION_AI` narrows this
  gap considerably.
- **A person can delete their own recipes.** If Aunt Ruth removes her cookies,
  they are gone. Back up the database file to get them back.
- **Photo content is not checked** — your call, and a reasonable one for a
  family site. Worth knowing what it means: the filter reads recipe text, so an
  unwelcome picture is the one thing that could go up and stay up until someone
  tells you. You can delete any photo or recipe as an admin.

**Worth doing when you have a moment:** back up the database *and the photos*.
On Render, the disk is not automatically backed up. Copy `cookbook.db` and the
`photos/` folder beside it — that is the whole cookbook. The 1 GB disk in
`render.yaml` holds roughly 600 recipes carrying all five photos, or a couple of
thousand at the more usual one or two each. Raise `sizeGB` if that day comes;
it costs $0.25/GB/month.

---

## The files

| File | What it is |
|---|---|
| `app.py` | Routes, forms, sessions, permissions. |
| `units.py` | Ingredient parsing and fraction formatting. |
| `moderation.py` | The automatic checks. |
| `photos.py` | Decoding, resizing, and storing uploaded photos. |
| `blocked_words.txt` | The wordlist. Edit freely. |
| `mailer.py` | Sending sign-in links (console / Resend / SMTP). |
| `db.py`, `schema.sql` | SQLite access and the tables. |
| `static/app.js` | Scaling in the browser, text size, live preview. |
| `static/style.css` | All of the styling. |
| `templates/` | The pages. |
| `test_cookbook.py`, `tests/` | The tests. |
