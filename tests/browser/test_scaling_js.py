"""Browser-side scaling (static/app.js) against the Python side (units.py).

R19: app.js exposes its pure scaling functions on a frozen
     window.ValentineScaling.
R20: ValentineScaling.formatAmount(q) == units.format_amount(q) for q in
     (0, 1000], and ValentineScaling.unitLabel(unit, q) == units.unit_label(unit, q).
     Both take the same arguments in the same order as their Python twins.
R22: the README's scaling table holds on a real recipe page (÷ 2, × 3 buttons),
     and below 1/8 teaspoon a scaled amount reads "a pinch of" (factor != 1
     only; decision OQ3).
R23: for any parsed amount and factor in [1/3, 3], renderAmount's text is
     accurate. "Within 0.035 of the displayed unit" is implemented as:

         |shown - true_in_shown_unit| <= 0.035 (+1e-9 for float noise)

     where `shown` is the number parsed from the text ("1 1/2" -> 1.5) and
     `true_in_shown_unit` is qty * factor converted from the original unit to
     the unit the text names. Equivalently, back in the original unit, the
     error is at most 0.035 of one displayed unit (decision OQ5). For a range,
     the max is checked the same way, and it must be shown whenever the scaled
     max is displayed in the same unit as the min; only a max that lands in
     another unit may be dropped (then only the min is checked). "a pinch of" is
     accepted only when qty * factor is a volume under 1/8 teaspoon.

Red by design until T9 exposes window.ValentineScaling (and, per OQ1, aligns
JS formatAmount with units.format_amount).

All calls into the page are batched: one page.evaluate per hypothesis example.
"""

import re
from fractions import Fraction

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

import units
from tests.browser.conftest import fill_like_a_person

# --- the one recipe page every test loads -------------------------------------

README_LINES = [
    "3/4 cup sugar",
    "1 1/2 teaspoons cinnamon",
    "1/4 teaspoon nutmeg",
    "1/8 teaspoon salt",
]


@pytest.fixture
def recipe_page(page, sign_in):
    """A signed-in browser on a freshly added recipe page (loads app.js)."""
    sign_in("scaling.cook@example.com", "Synthetic Scaling Cook")
    page.goto("/add")
    page.fill("#title", "Synthetic Spice Cake")
    page.fill("#ingredients", "\n".join(README_LINES))
    page.fill("#steps", "Mix everything. Bake until done.")
    page.fill("#servings", "4")
    fill_like_a_person(page, "form.recipe-form")
    page.click("form.recipe-form button[type=submit]")
    page.wait_for_url(re.compile(r"/recipe/[^/]+$"))
    assert page.locator(".recipe [data-scaler]").count() == 1
    return page


def scaling(page):
    """Fails with a clear message while app.js does not expose the API."""
    kind = page.evaluate("typeof window.ValentineScaling")
    assert kind == "object", (
        "window.ValentineScaling is %s (R19: app.js must expose it)" % kind
    )


# --- R19 ----------------------------------------------------------------------


def test_valentine_scaling_is_exposed(recipe_page):
    assert recipe_page.evaluate("typeof window.ValentineScaling") == "object"


def test_valentine_scaling_is_frozen(recipe_page):
    assert (
        recipe_page.evaluate(
            "typeof window.ValentineScaling === 'object' && Object.isFrozen(window.ValentineScaling)"
        )
        is True
    )


def test_valentine_scaling_has_the_pure_functions(recipe_page):
    kinds = recipe_page.evaluate(
        """() => {
            const api = window.ValentineScaling || {};
            return {
                formatAmount: typeof api.formatAmount,
                unitLabel: typeof api.unitLabel,
                renderAmount: typeof api.renderAmount,
            };
        }"""
    )
    assert kinds == {
        "formatAmount": "function",
        "unitLabel": "function",
        "renderAmount": "function",
    }


# --- R20: formatAmount == units.format_amount ---------------------------------

FRACTION_GRID = sorted(
    {float(Fraction(n, d)) for d in (1, 2, 3, 4, 8, 16) for n in range(1, 10 * d + 1)}
)
OQ1_COUNTEREXAMPLES = [0.03125, 2.03125, 0.322916666666]
# Found while writing T8a: the decimal fallback rounds differently. Python's
# "%.2f" rounds the exact binary value (0.995 is 0.99499..., so "0.99"); JS
# Math.round(v * 100) / 100 rounds 99.5 up ("1").
DECIMAL_COUNTEREXAMPLES = [0.995, 3.025, 14.995]

quantities = st.one_of(
    st.floats(min_value=0, max_value=1000, exclude_min=True, allow_nan=False),
    st.integers(min_value=1, max_value=32000).map(
        lambda n: n / 32
    ),  # odd 32nds: tie rounding
    st.integers(min_value=1, max_value=48000).map(
        lambda n: n / 48
    ),  # thirds next to 16ths
    st.integers(min_value=1, max_value=1000000).map(
        lambda n: n / 1000
    ),  # typed decimals
)


def js_format_amounts(page, values):
    return page.evaluate(
        "(vs) => vs.map((v) => window.ValentineScaling.formatAmount(v))", values
    )


def format_mismatches(page, values):
    got = js_format_amounts(page, values)
    return [
        (v, js, units.format_amount(v))
        for v, js in zip(values, got, strict=True)
        if js != units.format_amount(v)
    ]


@pytest.mark.parametrize(
    "value", OQ1_COUNTEREXAMPLES + DECIMAL_COUNTEREXAMPLES, ids=lambda v: repr(v)
)
def test_format_amount_matches_python_at_known_counterexamples(recipe_page, value):
    scaling(recipe_page)
    [js] = js_format_amounts(recipe_page, [value])
    assert js == units.format_amount(value)


@settings(max_examples=300)
@given(values=st.lists(quantities, min_size=1, max_size=20))
@example(values=FRACTION_GRID)
@example(values=OQ1_COUNTEREXAMPLES)
@example(values=DECIMAL_COUNTEREXAMPLES)
def test_format_amount_matches_python(recipe_page, values):
    scaling(recipe_page)
    bad = format_mismatches(recipe_page, values)
    assert not bad, "value, JS formatAmount, units.format_amount: %r" % bad


# --- R20: unitLabel == units.unit_label ---------------------------------------

ALL_UNITS = sorted(units.UNIT_NAMES) + [""]
LABEL_SPREAD = [
    0.0625,
    0.125,
    1 / 3,
    0.5,
    0.999999,
    1.0,
    1.0000001,
    1.000001,
    1.0000011,
    1.01,
    1.5,
    2.0,
    12.0,
    1000.0,
]

label_quantities = st.one_of(
    quantities,
    st.floats(min_value=0.999995, max_value=1.000005),  # the singular/plural boundary
)


def label_mismatches(page, pairs):
    got = page.evaluate(
        "(ps) => ps.map(([u, q]) => window.ValentineScaling.unitLabel(u, q))",
        [list(p) for p in pairs],
    )
    return [
        (u, q, js, units.unit_label(u, q))
        for (u, q), js in zip(pairs, got, strict=True)
        if js != units.unit_label(u, q)
    ]


@settings(max_examples=300)
@given(
    pairs=st.lists(
        st.tuples(st.sampled_from(ALL_UNITS), label_quantities), min_size=1, max_size=20
    )
)
@example(pairs=[(u, q) for u in ALL_UNITS for q in LABEL_SPREAD])
def test_unit_label_matches_python(recipe_page, pairs):
    scaling(recipe_page)
    bad = label_mismatches(recipe_page, pairs)
    assert not bad, "unit, qty, JS unitLabel, units.unit_label: %r" % bad


# --- R22: the README table, on the page's buttons -----------------------------

DIVIDE_2 = '[data-scaler] [data-factor="0.5"]'
DIVIDE_3 = '[data-scaler] [data-factor="0.3333333333333333"]'
TIMES_3 = '[data-scaler] [data-factor="3"]'


def ingredient_texts(page):
    return [
        " ".join(t.split())
        for t in page.locator(".recipe .ing .ing-text").all_inner_texts()
    ]


def test_readme_halved(recipe_page):
    recipe_page.click(DIVIDE_2)
    texts = ingredient_texts(recipe_page)
    assert texts[:3] == [
        "6 tablespoons sugar",
        "3/4 teaspoon cinnamon",
        "1/8 teaspoon nutmeg",
    ]


def test_readme_tripled(recipe_page):
    recipe_page.click(TIMES_3)
    texts = ingredient_texts(recipe_page)
    assert texts[:3] == [
        "2 1/4 cups sugar",
        "1 1/2 tablespoons cinnamon",
        "3/4 teaspoon nutmeg",
    ]


def test_below_an_eighth_teaspoon_is_a_pinch_when_halved(recipe_page):
    recipe_page.click(DIVIDE_2)
    assert ingredient_texts(recipe_page)[3] == "a pinch of salt"  # 1/16 teaspoon


def test_below_an_eighth_teaspoon_is_a_pinch_when_divided_by_three(recipe_page):
    recipe_page.click(DIVIDE_3)
    texts = ingredient_texts(recipe_page)
    assert texts[2:] == [
        "a pinch of nutmeg",
        "a pinch of salt",
    ]  # 1/12 and 1/24 teaspoon


# --- R23: scaled text is accurate -----------------------------------------------

# Sizes within each family, in that family's smallest unit (US kitchen measures).
FAMILIES = [
    {
        "tsp": 1,
        "tbsp": 3,
        "floz": 6,
        "cup": 48,
        "pint": 96,
        "quart": 192,
        "gallon": 768,
    },
    {"ml": 1, "liter": 1000},
    {"oz": 1, "lb": 16},
    {"g": 1, "kg": 1000},
]
VOLUME = FAMILIES[0]
LABEL_TO_UNIT = {}
for _key, (_one, _many) in units.UNIT_NAMES.items():
    LABEL_TO_UNIT[_one] = _key
    LABEL_TO_UNIT[_many] = _key

_AMOUNT = r"\d+ \d+/\d+|\d+/\d+|\d+(?:\.\d+)?"
_RENDERED = re.compile(
    r"(?P<a>%s)(?:-(?P<b>%s))?(?: (?P<label>.+))?" % (_AMOUNT, _AMOUNT)
)
TOLERANCE = 0.035 + 1e-9


def parse_shown(text):
    """'1 1/2' -> 1.5, '3/4' -> 0.75, '2.25' -> 2.25."""
    if "/" in text:
        whole, _, frac = text.rpartition(" ")
        num, den = frac.split("/")
        return (int(whole) if whole else 0) + int(num) / int(den)
    return float(text)


def size_ratio(from_unit, to_unit):
    """How many `to_unit` make one `from_unit`; None across families."""
    if from_unit == to_unit:
        return 1.0
    for family in FAMILIES:
        if from_unit in family and to_unit in family:
            return family[from_unit] / family[to_unit]
    return None


# Which unit a scaled amount is displayed in. This mirrors the unit choice in
# app.js friendlyUnit (its STEP_UP / STEP_DOWN / NICE_STEPS tables), written out
# here rather than called, so R23 can tell a range whose max legitimately moved
# to another unit (JS drops it) from one whose max was simply lost (decision OQ5).
_EPS = 1e-6
_STEP_UP = {
    "tsp": ("tbsp", 3, True),
    "tbsp": ("cup", 8, False),
    "floz": ("cup", 8, False),
    "ml": ("liter", 1000, False),
    "g": ("kg", 1000, False),
    "oz": ("lb", 16, False),
}
_STEP_DOWN = {
    "cup": ("tbsp", 0.25),
    "tbsp": ("tsp", 0.25),
    "quart": ("cup", 1),
    "pint": ("cup", 1),
    "gallon": ("quart", 1),
    "liter": ("ml", 1),
    "kg": ("g", 1),
    "lb": ("oz", 0.25),
}
_NICE_STEPS = {
    "cup": (1 / 4, 1 / 3),
    "tbsp": (1 / 2,),
    "tsp": (1 / 8,),
    "floz": (1 / 2,),
    "pint": (1 / 4,),
    "quart": (1 / 4,),
    "gallon": (1 / 4,),
    "liter": (1 / 4,),
    "kg": (1 / 4,),
    "lb": (1 / 4,),
}
_SNAP_DENOMINATORS = (2, 3, 4, 8, 16)


def _on_marking(value, unit):
    steps = _NICE_STEPS.get(unit)
    if not steps:
        return True
    return any(abs(value / s - round(value / s)) < _EPS for s in steps)


def _snaps_exactly(value):
    return any(abs(value * d - round(value * d)) < _EPS for d in _SNAP_DENOMINATORS)


def _in_unit(value, from_unit, to_unit):
    for family in FAMILIES:
        if from_unit in family and to_unit in family:
            return value * family[from_unit] / family[to_unit]
    return None


def display_unit(value, unit):
    """The unit app.js shows `value` `unit` in after its friendly-unit hops."""
    visited = {unit}
    for _ in range(4):
        up = _STEP_UP.get(unit)
        if up and up[0] not in visited and value >= up[1] - _EPS:
            bigger = _in_unit(value, unit, up[0])
            if bigger is not None and (
                _on_marking(bigger, up[0]) or (not up[2] and bigger >= 1 - _EPS)
            ):
                value, unit = bigger, up[0]
                visited.add(unit)
                continue
        down = _STEP_DOWN.get(unit)
        if down and down[0] not in visited:
            smaller = _in_unit(value, unit, down[0])
            if smaller is not None:
                if value < down[1] - _EPS or (
                    value < 1 - _EPS
                    and not _on_marking(value, unit)
                    and (_on_marking(smaller, down[0]) or _snaps_exactly(smaller))
                ):
                    value, unit = smaller, down[0]
                    visited.add(unit)
                    continue
        break
    return unit


def accuracy_problem(qty, qty_max, unit, factor, text):
    """None if `text` is an accurate rendering of qty * factor `unit`, else why not.

    For a range, the max must be shown (and accurate) whenever qty_max * factor
    is displayed in the same unit as the min. Leaving it out is allowed only
    when the max lands in a different unit (decision OQ5).
    """
    true = qty * factor
    if text == "a pinch of":
        # Strictly under 1/8 teaspoon: exactly 1/8 is a real spoon marking.
        if unit in VOLUME and true * VOLUME[unit] < 0.125 - 1e-9:
            return None
        return "says 'a pinch of' for %r %s" % (true, unit)
    match = _RENDERED.fullmatch(text)
    if not match:
        return "unreadable amount text"
    label = match.group("label")
    if unit == "":
        if label is not None:
            return "unitless amount got a label"
        shown_unit = ""
    else:
        shown_unit = LABEL_TO_UNIT.get(label)
        if shown_unit is None:
            return "unknown unit label %r" % label
    ratio = size_ratio(unit, shown_unit)
    if ratio is None:
        return "converted %s into another family (%s)" % (unit, shown_unit)
    error = abs(parse_shown(match.group("a")) - true * ratio)
    if error > TOLERANCE:
        return "min off by %.4f %s" % (error, shown_unit)
    if match.group("b") is not None:
        if not qty_max:
            return "range shown for a single amount"
        error = abs(parse_shown(match.group("b")) - qty_max * factor * ratio)
        if error > TOLERANCE:
            return "max off by %.4f %s" % (error, shown_unit)
    elif qty_max and display_unit(qty_max * factor, unit) == shown_unit:
        return "max dropped although %r %s stays in %s" % (
            qty_max * factor,
            unit,
            shown_unit or "no unit",
        )
    return None


_NUMBER_TEXT = st.one_of(
    st.integers(min_value=1, max_value=999).map(str),
    st.builds(
        lambda n, d: "%d/%d" % (n, d),
        st.integers(1, 15),
        st.sampled_from([2, 3, 4, 5, 6, 8, 16]),
    ),
    st.builds(
        lambda w, n, d: "%d %d/%d" % (w, n, d),
        st.integers(1, 20),
        st.integers(1, 15),
        st.sampled_from([2, 3, 4, 8, 16]),
    ),
    st.integers(min_value=1, max_value=99999).map(
        lambda n: "%d.%03d" % divmod(n, 1000)
    ),
    st.sampled_from(sorted(units.VULGAR)),
    st.builds(
        lambda w, v: "%d%s" % (w, v),
        st.integers(1, 9),
        st.sampled_from(sorted(units.VULGAR)),
    ),
)
_UNIT_WORD = st.one_of(
    st.just(""),
    # Spoons and cups are where unit hops and "a pinch of" happen, so they get
    # extra weight; every other unit still comes up through the next branch.
    st.sampled_from(["teaspoon", "teaspoons", "tablespoon", "cup", "cups"]),
    st.sampled_from(sorted(units.UNIT_NAMES)).flatmap(
        lambda k: st.sampled_from(units.UNIT_NAMES[k])
    ),
)


@st.composite
def parsed_amounts(draw):
    """A real parsed ingredient line with a quantity (sometimes a range)."""
    amount = draw(_NUMBER_TEXT)
    if draw(st.booleans()) and not draw(st.booleans()):  # about one in four is a range
        amount = "%s to %s" % (amount, draw(_NUMBER_TEXT))
    word = draw(_UNIT_WORD)
    line = " ".join(p for p in (amount, word, "synthetic flour") if p)
    parsed = units.parse_ingredient(line)
    if parsed["qty"] is None:
        parsed = units.parse_ingredient("%s synthetic flour" % amount.split(" to ")[0])
    assert parsed["qty"] is not None, line
    return parsed["qty"], parsed["qty_max"] or 0, parsed["unit"]


factors = st.one_of(
    st.floats(min_value=1 / 3, max_value=3),
    st.sampled_from([1 / 3, 0.5, 1.0, 2.0, 3.0]),
    st.builds(Fraction, st.integers(1, 24), st.integers(1, 24))
    .filter(lambda f: Fraction(1, 3) <= f <= 3)
    .map(float),  # servings stepper: next / base servings
)


def render_problems(page, cases):
    texts = page.evaluate(
        "(cs) => cs.map(([q, m, u, f]) => window.ValentineScaling.renderAmount(q, m, u, f))",
        [list(c) for c in cases],
    )
    problems = []
    for (qty, qty_max, unit, factor), text in zip(cases, texts, strict=True):
        why = accuracy_problem(qty, qty_max, unit, factor, text)
        if why:
            problems.append((qty, qty_max, unit, factor, text, why))
    return problems


RENDER_CASES = [
    (0.375, 0, "tsp", 0.5),  # 3/16 teaspoon: not a pinch
    (0.125, 0, "tsp", 1.0),  # exactly 1/8 teaspoon: not a pinch
    (0.75, 0, "cup", 0.5),
    (0.75, 0, "cup", 3.0),
    (1.5, 0, "tsp", 0.5),
    (1.5, 0, "tsp", 3.0),
    (0.25, 0, "tsp", 0.5),
    (0.25, 0, "tsp", 1 / 3),
    (1 / 3, 0, "tbsp", 1.0),
    (0.0625, 0, "tbsp", 1 / 3),
    (13 / 3, 0, "tbsp", 3.0),
    (1.5, 2.5, "cup", 1 / 3),
    (0.03125, 0, "", 1.0),
    (2.03125, 0, "lb", 1.0),
    (1, 2, "cup", 2.0),  # "2-4 cups": both ends stay in cups, so the max must show
    (6, 7, "tbsp", 0.5),  # "3-3 1/2 tablespoons"
    (2, 3, "", 3.0),  # unitless range "6-9"
    (6, 12, "tbsp", 1.0),  # max becomes 3/4 cup: dropping it is allowed
    (0.125, 1, "cup", 1.0),  # min becomes 2 tablespoons, max stays 1 cup
]

# R23 / OQ5 on accuracy_problem itself (no browser): when may a range lose its max?
RANGE_TEXT_CASES = [
    # (qty, qty_max, unit, factor, text, accurate?)
    (1, 2, "cup", 2.0, "2-4 cups", True),
    (1, 2, "cup", 2.0, "2 cups", False),  # reviewer's case: the max was lost
    (1, 2, "cup", 2.0, "2-5 cups", False),  # max shown but wrong
    (6, 7, "tbsp", 0.5, "3-3 1/2 tablespoons", True),
    (6, 7, "tbsp", 0.5, "3 tablespoons", False),
    (2, 3, "", 3.0, "6-9", True),
    (2, 3, "", 3.0, "6", False),
    (2, 3, "clove", 1.0, "2 cloves", False),  # a unit with no hops never moves
    # The max lands in another unit, so JS legitimately shows only the min.
    (6, 12, "tbsp", 1.0, "6 tablespoons", True),  # 12 tbsp -> 3/4 cup
    (0.125, 1, "cup", 1.0, "2 tablespoons", True),  # 1/8 cup -> 2 tbsp; max stays 1 cup
]


@pytest.mark.parametrize(
    "qty, qty_max, unit, factor, text, accurate",
    RANGE_TEXT_CASES,
    ids=lambda v: repr(v),
)
def test_accuracy_check_requires_the_range_max_in_the_same_unit(
    qty, qty_max, unit, factor, text, accurate
):
    why = accuracy_problem(qty, qty_max, unit, factor, text)
    if accurate:
        assert why is None, why
    else:
        assert why is not None, "%r was accepted for %r-%r %s x%r" % (
            text,
            qty,
            qty_max,
            unit,
            factor,
        )


@settings(max_examples=300)
@given(
    cases=st.lists(
        st.tuples(parsed_amounts(), factors).map(lambda t: (*t[0], t[1])),
        min_size=1,
        max_size=10,
    )
)
@example(cases=RENDER_CASES)
def test_scaled_text_is_accurate(recipe_page, cases):
    scaling(recipe_page)
    bad = render_problems(recipe_page, cases)
    assert not bad, "qty, qty_max, unit, factor, text, problem: %r" % bad
