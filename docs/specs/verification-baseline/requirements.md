# Verification baseline — requirements

## Decisions (approved by Trevor 2026-10-22, "approve with defaults")

- Q1 → **yes**: at ×1 the browser shows exactly the server's text (R21 as written).
- Q2 → `TRUSTED_PROXY_HOPS` defaults to **1**; Trevor confirms it from the R37 log line after deploy.
- Q3 → `autoDeployTrigger: checksPass`; Trevor checks the field against Render's Blueprint
  reference when T13 lands.
- Planning round (same day): JS `formatAmount` is aligned to `units.format_amount` (R20 holds as
  written; scaled text changes only at edge values). The post-sign-in redirect also rejects
  backslash paths (R14). R22's "a pinch of" applies only when scaled. A failing characterization
  test lands as a strict xfail and is reported. Details in plan.md.

## Open questions (resolved above; kept for the record)

1. **"As written" doesn't show what was written.** `static/app.js` runs `apply()` at factor 1 on page
   load and on "As written", and it applies its friendly-unit conversion even then. Measured against
   `units.py` (the server/no-JS text) for the same saved lines:

   | Saved line | Server / no-JS | Browser at ×1 |
   |---|---|---|
   | 1/2 gallon milk | 1/2 gallon | 2 quarts |
   | 1/3 tbsp sugar | 1/3 tablespoon | 1 teaspoon |
   | 1/16 tsp salt | 1/16 teaspoon | a pinch of |
   | 0.1 tsp salt | 1/8 teaspoon | a pinch of |
   | 0.2 cup water | 3/16 cup | 3 3/16 tablespoons |

   12 of 17 probed lines matched. R21 as written makes ×1 show exactly the server text (the browser
   keeps the server's text and restores it at ×1). **Recommended: yes.** If you'd rather keep the
   conversion at ×1, R21 changes to "matches except where a friendlier unit exists", and the no-JS
   page would still disagree with the JS page.
2. **Render's proxy hop count is not documented** (no Render source found). R35 defaults to one
   trusted hop (`TRUSTED_PROXY_HOPS=1`), which is the usual single-load-balancer setup. You confirm
   it after deploy (design.md, production steps). If it is wrong in the "too few" direction, every
   visitor shares one per-IP bucket (20 sign-in emails/hour site-wide) until you change the env var.
3. **`autoDeployTrigger: checksPass` syntax.** Render's docs and changelog name the values
   `commit | checksPass | off`, but I found no Blueprint-spec page showing it next to the existing
   `autoDeploy: true`. Check the Blueprint reference when applying the last task.

## Context

Valentine Cookbook is live and used by family members of every age, with real names and emails in
it. Onboarding found that nearly nothing an agent changes is checked:

- no `pyproject.toml`, so agent-verify's Python checks don't run (a stopgap `EXTRA_FAST_CMD` runs
  pyflakes-level ruff plus `test_cookbook.py`);
- no tests for any route, permission, sign-in or rate-limit behavior;
- no tests for `static/app.js`, which re-implements `units.py`'s formatting, and the two already
  disagree (open question 1);
- no browser tests, no CI, and every push to `main` deploys production;
- `client_ip()` trusts the client-supplied first `X-Forwarded-For` entry, so the per-IP sign-in
  limit is spoofable.

Users affected: **family members** (anyone signed in), **admins** (`ADMIN_EMAILS`), **visitors**
(signed out). Why now: before agents start changing this code, "done" has to mean something.

Toolchain decided with Trevor: **Python only** (pytest, hypothesis, pytest-playwright; no
package.json or Node in the repo). CI **gates Render deploys**. Ruff config and the X-Forwarded-For
fix are in scope.

## Requirements

### A. Python project and fast checks
- R1. THE repo SHALL declare its runtime dependencies in `pyproject.toml` with the same names and
  pinned versions as `requirements.txt`. IF the two differ THEN `agent-verify fast` SHALL fail.
- R2. WHEN `agent-verify fast` runs THE kit SHALL run ruff with the project's rule set and pytest
  (`-m "not e2e"`) natively, without the "no pyproject.toml" warning and without running any test
  twice.
- R3. THE ruff configuration SHALL pass on the current code without reformatting it.
- R4. THE existing 50 tests in `test_cookbook.py` SHALL run under pytest unchanged and pass.
- R5. `requirements.txt` SHALL remain Render's build input; `render.yaml`'s `buildCommand` is
  unchanged.

### B. Routes, permissions, sign-in (server tests, no browser)
- R6. WHEN a signed-out visitor requests `/add`, `/recipe/<slug>/edit` or `/me`, or POSTs to edit,
  delete, comment or delete-comment, THE app SHALL redirect to `/login` and change no data.
- R7. IF a signed-in user who is neither the submitter nor an admin POSTs an edit or delete of a
  recipe THEN THE app SHALL leave the recipe, its ingredients, tags and photos unchanged (edit:
  redirect with a message; delete: 403).
- R8. [PROPERTY] For any set of users, recipes, notes and any sequence of edit, delete, photo-remove
  and note-delete attempts, a recipe SHALL change only when the actor is its submitter or an admin,
  and a note SHALL be deleted only by its author or an admin.
- R9. [PROPERTY] `credited_to` SHALL never grant rights: for any credit value, including one equal
  to the actor's own name, a non-submitter non-admin is refused.
- R10. WHILE a recipe is hidden THE app SHALL return 404 for it to non-admins and omit it from the
  index, search and filters; an admin SHALL be able to open it. (No route sets `is_hidden` today;
  only a direct database edit can hide a recipe, so the tests set it the same way.)
- R11. IF a signed-in user becomes blocked THEN THE app SHALL treat their next request as signed out.
- R12. IF a POST's CSRF token is missing or wrong THEN THE app SHALL change no data, for every POST
  route including `/api/parse`.
- R13. WHEN an unused, unexpired sign-in link is opened THE app SHALL sign that user in. IF the link
  was already used or has expired THEN THE app SHALL refuse it. THE database SHALL never contain the
  raw token.
- R14. [PROPERTY] For any `next` path stored before sign-in, the redirect after sign-in SHALL stay on
  the site. Likewise `safe_referrer()` SHALL never return an off-site URL for any Referer.
- R15. WHEN a user requests a 7th sign-in email within an hour, or one IP requests a 21st, THE app
  SHALL refuse it with the plain-language message and send nothing.
- R16. THE app SHALL send the CSP, `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy` headers on
  every response, including errors.
- R17. WHEN a recipe title, description, ingredient, note or credit contains HTML THE pages SHALL show
  it as literal text.
- R18. IF `/photo/<name>` is requested with a name that doesn't match the generated pattern THEN THE
  app SHALL return 404 without touching the filesystem.

### C. Browser-side scaling (`static/app.js`)
- R19. `static/app.js` SHALL expose its pure scaling functions (`formatAmount`, `unitLabel`,
  `renderAmount`) on a frozen `window.ValentineScaling` object, with no other behavior change.
- R20. [PROPERTY] For any quantity in (0, 1000] THE browser's `formatAmount` SHALL equal
  `units.format_amount`, and `unitLabel` SHALL equal `units.unit_label` for every unit and quantity.
- R21. [PROPERTY] WHEN the factor is 1 (page load, "As written") THE recipe page SHALL show, for every
  ingredient, the same amount text the server rendered. *(Depends on open question 1.)*
- R22. THE README's scaling examples SHALL hold (3/4 cup halved → 6 tablespoons, tripled → 2 1/4
  cups; 1 1/2 tsp halved → 3/4 teaspoon, tripled → 1 1/2 tablespoons; 1/4 tsp halved → 1/8
  teaspoon), and below 1/8 teaspoon THE page SHALL say "a pinch of".
- R23. [PROPERTY] For any parsed amount and factor in [1/3, 3], the scaled text SHALL, converted
  back to the original unit, be within 0.035 of the displayed unit of `qty × factor`, or be
  "a pinch of" when that is under 1/8 teaspoon. A wrong number is worse than no number.

### D. Browser journeys (e2e, pytest-playwright, marker `e2e`)
- R24. A visitor SHALL be able to browse, search by a credited name (no account), and filter by
  course, cuisine, tag and ingredient.
- R25. A family member SHALL be able to sign in with the emailed link, add a recipe with a photo,
  see it titled "… à la <credit>" with "Typed in by <name>", scale it ×2 and back to "As written",
  and use the servings stepper.
- R26. A second family member SHALL NOT see edit or delete controls on that recipe, and SHALL be able
  to leave a note. The submitter SHALL be able to edit and then delete the recipe.
- R27. WITH JavaScript disabled, a recipe page SHALL show its ingredients and amounts, and the add
  form SHALL still submit.
- R28. WHEN a visitor presses A+ THE text size SHALL grow and SHALL persist after reload.
- R29. Every button and link in the recipe page's scaler and header SHALL be at least 44×44 CSS px.
- R30. IF a page requests anything outside the local test server's origin THEN the e2e run SHALL
  fail. No e2e test sends real email.
- R31. `agent-verify e2e` SHALL run the e2e suite, starting the app server within the same command.

### E. CI and deploy gating
- R32. WHEN a commit is pushed to any branch, or a pull request is opened or updated, THE GitHub
  Actions workflow SHALL run ruff, pytest (not e2e), the e2e suite in Chromium, and gitleaks.
- R33. THE workflow SHALL use no repository secrets, read-only `contents` permission, and actions
  pinned to full commit SHAs.
- R34. WHILE `render.yaml` sets `autoDeployTrigger: checksPass` THE Render service SHALL deploy only
  commits whose checks pass. THE switch SHALL land only after the workflow is merged and green on
  `main`, because Render skips commits that have no checks at all.

### F. Client IP for rate limiting
- R35. THE app SHALL take the client IP from `X-Forwarded-For` by counting `TRUSTED_PROXY_HOPS`
  entries from the right (default 1). IF the header has fewer entries than that THEN THE app SHALL
  use the socket address.
- R36. [PROPERTY] For any client-supplied `X-Forwarded-For` prefix, the computed client IP SHALL be the
  same as without it (a client cannot choose its rate-limit bucket).
- R37. WHEN a sign-in email is requested THE app SHALL log the computed client IP and the number of
  `X-Forwarded-For` entries, so the hop count can be checked in Render's logs.

## What changes, what must not

Changes: new `pyproject.toml`, `uv.lock`, `tests/`, `.github/workflows/ci.yml`; `app.py`
(`client_ip()` and one log line only); `static/app.js` (exposure and ×1 text only); `render.yaml`
(`autoDeployTrigger` only, last task); `.kit/verify.env`; `AGENTS.md`, `README.md`.

Must not change: runtime dependencies or versions, `render.yaml`'s build/start commands, disk and
env, templates, CSS, the schema, moderation rules, photo handling, sign-in token rules, any
user-visible text except the ×1 amounts (R21).

## Out of scope
- Fixing the 33 `UP031` %-format and other style findings, or reformatting with `ruff format`.
- Bugs the new tests uncover beyond R21 and R35–R37: each is reported to Trevor and gets its own
  bugfix spec, unless he says to fold it in.
- GitHub branch protection rules, semgrep in CI, visual regression, load tests.
- Moving `test_cookbook.py` into `tests/`.
