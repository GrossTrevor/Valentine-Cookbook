# Verification baseline — tasks

Ordered. Each task ≈200 lines or less, owns only the files listed, and is done when its verify
command passes. Tests come before the code they test. Characterization tests (T3, T4) are expected to
pass on current code; a failure there is reported to Trevor, not fixed in the task.

- [ ] **T1. Python project config** (R1–R5)
  Owns: `pyproject.toml`, `uv.lock`, `tests/__init__.py`, `tests/test_project_config.py`,
  `.kit/verify.env`; import order in `app.py`/`test_cookbook.py` via `ruff check --fix --select I`
  only.
  Verify: `agent-verify fast --all` (no "no pyproject.toml" warning; ruff and pytest steps listed;
  51+ tests).

- [ ] **T2. Server test fixtures** (supports R6–R18)
  Owns: `tests/conftest.py`, `tests/test_fixtures_smoke.py` (sign-in round trip, isolation between
  tests, mailbox captures, no env leakage).
  Verify: `agent-verify fast`.

- [ ] **T3. Route, permission and sign-in tests** (R6, R7, R10–R13, R15–R18)
  Owns: `tests/test_routes.py`, `tests/test_permissions.py`, `tests/test_signin.py`.
  Verify: `agent-verify fast`.

- [ ] **T4. Permission and redirect property tests** (R8, R9, R14)
  Owns: `tests/test_permission_properties.py`.
  Verify: `agent-verify fast`.

- [ ] **T5. Client IP tests** (R35, R36, R37), written first and failing
  Owns: `tests/test_client_ip.py`.
  Verify: `uv run pytest tests/test_client_ip.py` fails on the R35/R36 cases (expected); the rest of
  `agent-verify fast` is unaffected.

- [ ] **T6. Client IP fix** (R35–R37)
  Owns: `app.py` (`client_ip()`, a `_int_env` helper, the startup log, the sign-in log line).
  Verify: `agent-verify fast` (T5 now green).

- [ ] **T7. Browser fixtures** (R30, R31)
  Owns: `tests/browser/__init__.py`, `tests/browser/conftest.py`, `tests/browser/test_harness.py`
  (server on 127.0.0.1, a deliberate off-origin request is caught, no mail sent).
  Verify: `agent-verify e2e`.

- [ ] **T8. app.js scaling tests**, written first and failing (R20–R23)
  Owns: `tests/browser/test_scaling_js.py`, `tests/browser/test_as_written.py`.
  Verify: `agent-verify e2e` fails only on the missing `ValentineScaling` and the R21 mismatches
  (expected).

- [ ] **T9. app.js exposure and the "As written" fix** (R19, R21). Waits on open question 1.
  Owns: `static/app.js`.
  Verify: `agent-verify e2e` and `agent-verify fast`.

- [ ] **T10. Journey tests** (R24–R29)
  Owns: `tests/browser/test_journeys.py`.
  Verify: `agent-verify e2e`. A failure that shows a product bug is reported, not worked around.

- [ ] **T11. CI workflow** (R32, R33)
  Owns: `.github/workflows/ci.yml`.
  Verify: `agent-verify full`; `actionlint` if available; Trevor sees the run green on GitHub after
  he pushes (agents don't push).

- [ ] **T12. Docs**
  Owns: `AGENTS.md` (testing section, traps: remove the EXTRA_FAST_CMD and "no tests" notes, add the
  `e2e` marker, `TRUSTED_PROXY_HOPS`, deploy gate), `README.md` ("Running it here" commands, test
  count, `TRUSTED_PROXY_HOPS` in the env table).
  Verify: `agent-verify fast`.

- [ ] **T13. Gate Render deploys on CI** (R34). Separate PR, only after T11 is green on `main`
  Owns: `render.yaml` (`autoDeploy: true` → `autoDeployTrigger: checksPass`; nothing else).
  Verify: `agent-verify full`; then Trevor's production steps in design.md.

- [x] **T14. A pinch in every volume unit; display_unit drift test** (R22, R23; follow-up after
  merge). Owns: `static/app.js` (`underAPinch`, `renderAmount`, `friendlyUnit` on
  `ValentineScaling`), `tests/browser/test_scaling_js.py`, `tests/browser/test_display_unit_mirror.py`.
  Verify: `agent-verify e2e` and `agent-verify fast`.
