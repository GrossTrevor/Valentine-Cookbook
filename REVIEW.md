# REVIEW.md — what matters when reviewing Valentine-Cookbook changes

Report a finding only with evidence: a failing test, a repro, or an exact trace. No style comments.

Project-specific, first:

- **Only the submitter or an admin changes a recipe.** Edit, delete and photo removal must check
  `is_admin() or row["submitted_by_id"] == user["id"]` on the server. `credited_to` is a display name
  and never grants anything. Notes are deleted only by their author or an admin.
- **Hidden recipes stay hidden.** Any query that lists or shows recipes or their notes to non-admins
  filters `is_hidden = 0`; a blocked user (`is_blocked`) is never treated as signed in.
- **Sign-in tokens.** Random, stored only as a SHA-256 hash, single use, 45-minute expiry; the
  post-login redirect only goes to a local path. No change may log, email or show a token anywhere
  other than the sign-in email (or the console provider's log line).
- **Nothing a user wrote is rendered as HTML**: no `|safe`/`Markup`, no `innerHTML` with user data,
  and no loosening of the CSP or the other headers in `headers()`.
- **Every new user-entered field** gets a `MAX_LENGTHS` cap and goes through `moderation.check`
  (or `check_name`/`check_credit`). Moderation changes keep whole-word matching: the innocent-word
  tests (molasses, Scunthorpe, Assam) must still pass.
- **Photos**: uploads only via `photos.save()` (decode, re-encode, strip EXIF/GPS, generated names);
  served only after `FILENAME_RE` matches. Caps (12 MB, 50 MP, 5 per recipe) stay.
- **Migrations run against the live family database on the next deploy.** Schema changes to existing
  tables go in `db.migrate()`, are idempotent, never drop or rewrite user data, and come with a
  `MigrationTests` case built from the old table shape.
- **Amounts must be right.** A parsing/formatting change in `units.py` comes with tests and the
  matching change in `static/app.js` (which has no automated tests — ask for a manual check).
- **Outbound requests** send a real User-Agent and never break sign-in when the provider fails.
- **Usability is a requirement**, not polish: works without JS, ≥44px targets, plain-language error
  messages, no CAPTCHA, nothing loaded from another origin.

Generic:

1. **Spec mismatch** — the change doesn't do what its spec in docs/specs/ says, or does more.
2. **Authorization** — a route, server action, query or access rule lets a user see or change data
   that isn't theirs (e.g. a student reaching admin data). Check both the UI and the server side.
3. **Data integrity** — migrations that lose data or can't roll back; writes without validation;
   races on concurrent edits.
4. **Injection and XSS** — raw SQL with interpolation, unsanitized HTML, open redirects.
5. **Secrets and config** — secrets in client bundles (NEXT_PUBLIC_/VITE_), committed .env, missing
   env validation.
6. **Cross-package breakage** — a shared package change that breaks a consumer, API contract changes
   without matching client changes.
7. **Tests** — tests that assert implementation details instead of requirements; missing tests for
   new behavior; e2e coverage for changed user journeys.
<!-- /web-onboard adds project-specific priorities above the generic ones -->
