"""R23's display_unit() against the real app.js friendlyUnit.

test_scaling_js.display_unit is a hand copy of app.js's unit hops (STEP_UP,
STEP_DOWN, NICE_STEPS), so R23 can tell a range max that moved to another unit
(allowed to be dropped) from one that was simply lost. A copy drifts: this
compares the two over every value/unit pair below, so changing app.js's unit
rules without the copy fails here instead of quietly weakening R23.

From the reviewer's differential check (.agent/review/test_display_unit_mirror.py),
run in Chromium instead of node so the toolchain stays Python only.
"""

import json
import random
from fractions import Fraction

import units
from tests.browser.test_scaling_js import display_unit

# Every fraction up to 60 with these denominators, plus common amounts times
# every servings-stepper factor (next / base servings, within [1/3, 3]).
DENOMINATORS = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96)
COMMON_QUANTITIES = (0.125, 0.25, 1 / 3, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16)
COMMON_QUANTITIES += (24, 32, 1000)


def mirror_cases():
    values = set()
    for den in DENOMINATORS:
        for num in range(1, 3 * den * 20 + 1):
            values.add(num / den)
    rng = random.Random(1234)
    for _ in range(4000):
        values.add(rng.uniform(0.001, 3000))
    stepper_factors = {
        Fraction(a, b)
        for a in range(1, 25)
        for b in range(1, 25)
        if Fraction(1, 3) <= Fraction(a, b) <= 3
    }
    for factor in stepper_factors:
        for qty in COMMON_QUANTITIES:
            values.add(qty * float(factor))
    unit_keys = sorted(units.UNIT_NAMES) + [""]
    return [(value, unit) for unit in unit_keys for value in sorted(values)]


def test_display_unit_matches_app_js_friendly_unit(page):
    page.goto("/")
    assert page.evaluate("typeof window.ValentineScaling.friendlyUnit") == "function"
    cases = mirror_cases()
    # One JSON string each way: Playwright's per-value serialization of ~400k
    # pairs takes half a minute, JSON.parse and join take well under a second.
    got = page.evaluate(
        """(text) => JSON.parse(text)
            .map(([v, u]) => window.ValentineScaling.friendlyUnit(v, u).unit)
            .join(",")""",
        json.dumps(cases),
    ).split(",")
    bad = [
        (value, unit, js, display_unit(value, unit))
        for (value, unit), js in zip(cases, got, strict=True)
        if display_unit(value, unit) != js
    ]
    assert not bad, "value, unit, app.js unit, display_unit: %r (of %d)" % (
        bad[:20],
        len(cases),
    )
