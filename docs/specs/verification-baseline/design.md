# Verification baseline — design

## Components

### pyproject.toml (new, root)
- `[project]`: `name = "valentine-cookbook"`, `requires-python = ">=3.12,<3.13"` (Render pins 3.12.4),
  `dependencies` = the four pins from `requirements.txt`. No build backend; it is not a package.
- `[dependency-groups] dev`: pytest, hypothesis, pytest-playwright (exact pins chosen at
  implementation; the pytest-playwright/Playwright version should match a Chromium build already in
  `~/.cache/ms-playwright` (1247/1248) so local runs don't need a download).
- `[tool.pytest.ini_options]`: `pythonpath = ["."]`, `testpaths = ["test_cookbook.py", "tests"]`,
  `markers = ["e2e: needs a browser (Chromium) and a live app server"]`, `addopts = "--strict-markers"`.
- `[tool.ruff]`: `target-version = "py312"`, `lint.select = ["E", "F", "W", "B", "I"]`,
  `lint.ignore = ["E501", "B904"]`. That set passes once the 2 import-order findings (`I001`) are
  autofixed; that's import order only, no reformat. `E501` because lines run to ~92 chars; `B904`
  because `raise Rejected(...)` inside `except ValueError` is deliberate user messaging.
- `uv.lock` committed. The kit then runs `uv run ruff check .` and `uv run pytest -q -m "not e2e"`.
- **R1 check:** `tests/test_project_config.py` parses both files with `tomllib` and a line parser,
  ignoring comments, and compares name→version maps.

### .kit/verify.env
- Remove `EXTRA_FAST_CMD` (the kit's native checks replace it; R2 "no test twice").
- `E2E_CMD="uv run pytest -m e2e"`. The live server is started by a pytest fixture in the same
  process (R31), so no compose file and nothing from a separate shell.

### tests/conftest.py (server tests)
- Sets `DATA_DIR` to a session temp dir and `MAIL_PROVIDER=console` **before** `import app`
  (import runs `create_app()`; AGENTS.md trap). Pops `RESEND_API_KEY`, `SMTP_HOST` and
  `ANTHROPIC_API_KEY` from the environment for the session.
- Per-test isolation: `monkeypatch.setenv("DATA_DIR", tmp_path)` then `db.init()`. `db.data_dir()`
  reads the env var on every connect, so the one app object works for every test.
- Fixtures: `client` (Flask test client), `mailbox` (monkeypatches `mailer.send` to record
  `(to, subject, body)` and return False), `make_user(email, name, admin=False, blocked=False)` (admin
  by `monkeypatch.setenv("ADMIN_EMAILS", ...)`), `sign_in(client, user)` (POST `/login`, pull the link
  out of `mailbox`, GET it: the real flow, no session forging), `make_recipe(owner, **fields)`
  (through POST `/add`, so search_blob and ingredients are real), `csrf(client)`.
- Bot traps: the test client omits `started_at`, which the code treats as "no timing check". One
  test posts `started_at=now` and expects the "very fast" rejection, and one fills the honeypot.
- Synthetic data only: `@example.com` emails and invented names.

### tests/test_routes.py, tests/test_permissions.py, tests/test_signin.py
Example-based tests for R6, R7, R10–R13, R15–R18. **These characterize current code and are
expected to pass on arrival.** A failure is a finding to report, not something to fix inside a
test task.

### tests/test_permission_properties.py (hypothesis)
- R8: `RuleBasedStateMachine` with rules `add_user`, `add_recipe(owner)`, `add_note(author, recipe)`,
  `attempt_edit/delete/remove_photo(actor, recipe)`, `attempt_delete_note(actor, note)`. A shadow
  model tracks who owns what. The invariant after each step: the DB's recipes and notes equal the
  model, where the model only applies an action when the actor is the owner or an admin.
  `max_examples` is kept small (~50) so fast stays fast; `deadline=None`.
- R9: `@given(credit=text())` filtered by `moderation.check_credit` acceptance, with `credited_to`
  set to the actor's name and to arbitrary values.
- R14: `@given` arbitrary `next` paths (including `//evil`, `/\evil`, `https://…`, encoded slashes)
  and Referer headers; asserts the `Location` header is relative or has the test host.

### app.py — client_ip (R35–R37)
```python
def client_ip():
    hops = _int_env("TRUSTED_PROXY_HOPS", 1)
    chain = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
    if hops > 0 and len(chain) >= hops:
        return chain[-hops]
    return request.remote_addr or "unknown"
```
- Invalid or negative env value → 1, logged once at startup next to the existing `mail:` line.
- R37: in `login()` POST, `log.info("sign-in requested ip=%s forwarded_entries=%d", ip, len(chain))`.
  No email address in that line (the mailer already logs recipients).
- Tests: `tests/test_client_ip.py` uses `app.test_request_context(headers=..., environ_base=
  {"REMOTE_ADDR": ...})`. The R36 property: for any list of client-supplied entries `P` and fixed
  proxy-appended entries `S` with `len(S) == hops`, `client_ip(P + S) == client_ip(S)`.

### static/app.js (R19, R21)
- Before `document.addEventListener("DOMContentLoaded", …)`:
  `window.ValentineScaling = Object.freeze({ formatAmount, unitLabel, renderAmount });`
- R21: in `setupScaler`, record each `.amount` node's server text (and the unitless item's text,
  which `apply()` already stores in `data-item`) before the first `apply()`. WHEN
  `Math.abs(factor - 1) < 0.001`, restore those texts instead of calling `renderAmount`. No template
  change. CSP is unaffected (no inline script).

### tests/browser/ (marker e2e)
- `conftest.py`: the `live_server` session fixture imports `app` (same env setup as above), patches
  `mailer.send` to capture into a list, and serves `werkzeug.serving.make_server("127.0.0.1", 0,
  app.app, threaded=True)` in a daemon thread. It yields the base URL and shuts down at the end.
  Never `0.0.0.0`. A per-test autouse fixture resets `DATA_DIR` to a fresh temp dir and runs
  `db.init()`.
- R30: an autouse `page.route("**/*")` handler aborts and records any request whose origin isn't the
  live server's; a teardown assertion fails the test if anything was recorded.
- `fill_like_a_person(page, form)`: backdates the hidden `started_at` input by 5 s before submit, so
  journeys don't sleep 2 s per form. The real-time path is covered by the server test above.
- `test_scaling_js.py` (R20, R22, R23): opens one recipe page and calls
  `page.evaluate("ValentineScaling.formatAmount(x)")` in batches (one evaluate per hypothesis
  example batch, not per value). It compares against `units.format_amount` and `units.unit_label`
  in-process. Uses hypothesis with `max_examples≈300` and a fixed grid of common fractions.
- `test_as_written.py` (R21): saves recipes covering the open-question table plus generated lines,
  loads each page with JS on and JS off, and compares the `.amount` texts. Then presses ×2, then
  "As written", and compares again.
- `test_journeys.py` (R24–R29): one test per journey. R29 uses `bounding_box()` on
  `[data-scaler] button, header a, header button`. The photo is a 1600×1200 JPEG generated with
  Pillow into `tmp_path`.
- **Note:** R20–R23 need Chromium, so they run in `agent-verify e2e` and CI, not in `fast`.

### .github/workflows/ci.yml (R32, R33)
- Triggers: `push` (all branches) and `pull_request`. `permissions: contents: read`. One job,
  `checks`, on `ubuntu-latest`. Steps:
  1. checkout (`fetch-depth: 0` for gitleaks);
  2. `astral-sh/setup-uv`, then `uv sync --locked`;
  3. `uv run ruff check .`;
  4. `uv run pytest -q -m "not e2e"`;
  5. `uv run playwright install --with-deps chromium`, then `uv run pytest -m e2e`;
  6. gitleaks: the pinned release binary with checksum verification, `gitleaks git --redact`.
     This avoids gitleaks-action's license prompt.
- Every `uses:` is pinned to a full SHA with the tag in a comment. No secrets; `MAIL_PROVIDER` is
  unset, so the app's default is console.
- One job means one GitHub check. Render's `checksPass` needs all checks green, and it counts
  skipped/neutral as passing.

### render.yaml (R34), last task, separate PR
- Replace `autoDeploy: true` with `autoDeployTrigger: checksPass` (confirm field name against the
  Blueprint spec; open question 3). Nothing else in the file changes.

## Data model and migrations
None. No schema, `db.migrate()` or stored-data changes.

## Authorization
Unchanged. This spec only adds tests that pin down the existing rules (server-side in `app.py`:
`require_login()`, `is_admin()`, `submitted_by_id` / `comments.user_id` checks) and fixes the IP
used by rate limits.

## State machine
Sign-in token (R13), as implemented today:
```
issued --open link (unused, now < expires_at)--> used   [session set]
issued --now >= expires_at--> expired                    [open link → refused]
used   --open link--> used                               [refused]
```
Deploy of a commit on `main` once R34 lands:
```
pushed --checks pending--> waiting
waiting --all checks pass--> deployed
waiting --any check fails--> not deployed
pushed --no checks reported--> not deployed (silently)   ← why R34 waits for CI to be live on main
```

## Failure modes
- **`TRUSTED_PROXY_HOPS` too low for Render's real chain:** the IP is a Render edge address, so all
  visitors share one 20/hour sign-in bucket. Per-user limits still apply. Visible in the R37 log
  line; fixed by setting the env var in the Render dashboard (no deploy needed beyond the restart).
- **Too high:** behaves like today (spoofable). Also visible in the R37 log line.
- **CI flaky in e2e:** with `checksPass`, a flake blocks a deploy (it does not deploy something bad).
  Re-run the job. Deploys can still be started by hand from the Render dashboard.
- **`requirements.txt` and `pyproject.toml` drift:** caught by R1 in `fast` and CI.
- **Playwright Chromium not cached locally:** `agent-verify e2e` fails with a download error. The
  fix is `uv run playwright install chromium` (needs the Playwright CDN), run by Trevor or with network
  allowed.

## Security and privacy
- Tests use synthetic data, temp `DATA_DIR`s, and a captured mailer. The e2e network guard (R30) also
  proves no external calls. CI has no secrets.
- R37 logs client IPs in Render's logs, alongside the email addresses the mailer already logs there.
- The `ValentineScaling` exposure reveals only pure formatting functions already shipped in
  `app.js`.

## Production steps for Trevor (in order)
1. Merge everything up to and including the CI task. Confirm the `checks` run is green on the
   `main` commit in GitHub.
2. After that deploy, request a sign-in link from your home network. In Render → Logs, find
   `sign-in requested ip=… forwarded_entries=N` and compare the IP with your public IP. If it shows a
   Render or private address, set `TRUSTED_PROXY_HOPS` to `N` minus the number of entries after
   yours, in Render → Environment.
3. Merge the `render.yaml` task. In Render → Settings → Build & Deploy, confirm Auto-Deploy reads
   "After CI checks pass". Push a trivial commit and watch Render wait for the check.

## Formal model
None. The token lifecycle is three states with no concurrency the tests don't cover. The deploy
gate is Render's logic, not ours.
