"""Parsing ingredient lines into scalable amounts.

The whole point of the site is that nobody should have to do arithmetic, so
every ingredient line gets split into (quantity, unit, item) at save time.
The browser then multiplies the quantity and re-renders it as a friendly
fraction. Anything we cannot confidently parse is kept as literal text and
never scaled -- a wrong number is far worse than an unscaled one.
"""

import re
from fractions import Fraction

# Unicode fractions people paste in from old documents.
VULGAR = {
    "¼": Fraction(1, 4),
    "½": Fraction(1, 2),
    "¾": Fraction(3, 4),
    "⅐": Fraction(1, 7),
    "⅑": Fraction(1, 9),
    "⅒": Fraction(1, 10),
    "⅓": Fraction(1, 3),
    "⅔": Fraction(2, 3),
    "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5),
    "⅗": Fraction(3, 5),
    "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6),
    "⅚": Fraction(5, 6),
    "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8),
    "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
}

# canonical key -> (singular, plural)
UNIT_NAMES = {
    "tsp": ("teaspoon", "teaspoons"),
    "tbsp": ("tablespoon", "tablespoons"),
    "cup": ("cup", "cups"),
    "floz": ("fluid ounce", "fluid ounces"),
    "pint": ("pint", "pints"),
    "quart": ("quart", "quarts"),
    "gallon": ("gallon", "gallons"),
    "ml": ("milliliter", "milliliters"),
    "liter": ("liter", "liters"),
    "oz": ("ounce", "ounces"),
    "lb": ("pound", "pounds"),
    "g": ("gram", "grams"),
    "kg": ("kilogram", "kilograms"),
    "pinch": ("pinch", "pinches"),
    "dash": ("dash", "dashes"),
    "clove": ("clove", "cloves"),
    "can": ("can", "cans"),
    "jar": ("jar", "jars"),
    "package": ("package", "packages"),
    "stick": ("stick", "sticks"),
    "slice": ("slice", "slices"),
    "loaf": ("loaf", "loaves"),
    "sprig": ("sprig", "sprigs"),
    "head": ("head", "heads"),
    "stalk": ("stalk", "stalks"),
    "bunch": ("bunch", "bunches"),
    "handful": ("handful", "handfuls"),
    "quart_box": ("box", "boxes"),
}

_ALIAS_SOURCE = {
    "tsp": ["t", "ts", "tsp", "tsps", "tspn", "teaspoon", "teaspoons"],
    "tbsp": ["T", "tb", "tbs", "tbsp", "tbsps", "tblsp", "tablespoon", "tablespoons"],
    "cup": ["c", "cup", "cups"],
    "floz": ["floz", "fl oz", "fl ozs", "fluid ounce", "fluid ounces", "fl. oz"],
    "pint": ["pt", "pts", "pint", "pints"],
    "quart": ["qt", "qts", "quart", "quarts"],
    "gallon": ["gal", "gals", "gallon", "gallons"],
    "ml": ["ml", "mls", "milliliter", "milliliters", "millilitre", "millilitres"],
    "liter": ["l", "liter", "liters", "litre", "litres"],
    "oz": ["oz", "ozs", "ounce", "ounces"],
    "lb": ["lb", "lbs", "pound", "pounds"],
    "g": ["g", "gr", "gram", "grams", "gramme", "grammes"],
    "kg": ["kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"],
    "pinch": ["pinch", "pinches"],
    "dash": ["dash", "dashes"],
    "clove": ["clove", "cloves"],
    "can": ["can", "cans"],
    "jar": ["jar", "jars"],
    "package": ["pkg", "pkgs", "package", "packages", "packet", "packets"],
    "stick": ["stick", "sticks"],
    "slice": ["slice", "slices"],
    "loaf": ["loaf", "loaves"],
    "sprig": ["sprig", "sprigs"],
    "head": ["head", "heads"],
    "stalk": ["stalk", "stalks"],
    "bunch": ["bunch", "bunches"],
    "handful": ["handful", "handfuls"],
    "quart_box": ["box", "boxes"],
}

# "T" means tablespoon and "t" means teaspoon, so the lookup is case sensitive
# first and case insensitive second.
ALIASES_EXACT = {"T": "tbsp", "t": "tsp"}
ALIASES = {}
for _key, _words in _ALIAS_SOURCE.items():
    for _w in _words:
        ALIASES.setdefault(_w.lower(), _key)

MAX_UNIT_WORDS = 3

_NUMBER = r"\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.\d+|\d+"
_RANGE_RE = re.compile(
    r"^\s*(?P<a>" + _NUMBER + r")\s*(?:-|–|—|to)\s*(?P<b>" + _NUMBER + r")\b"
)
_SINGLE_RE = re.compile(r"^\s*(?P<a>" + _NUMBER + r")")


def _expand_vulgar(text):
    """Turn '1½' into '1 1/2' and '½' into '1/2'."""
    out = []
    for i, ch in enumerate(text):
        if ch in VULGAR:
            frac = VULGAR[ch]
            prefix = "".join(out)
            if prefix and prefix[-1].isdigit():
                out.append(" ")
            out.append("%d/%d" % (frac.numerator, frac.denominator))
            if i + 1 < len(text) and text[i + 1].isalnum():
                out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _to_float(text):
    text = text.strip()
    try:
        if " " in text and "/" in text:
            whole, frac = text.split(None, 1)
            num, den = frac.split("/")
            return float(whole) + float(num) / float(den)
        if "/" in text:
            num, den = text.split("/")
            den = float(den)
            if den == 0:
                return None
            return float(num) / den
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def _match_unit(rest):
    """Pull a unit off the front of `rest`. Returns (unit_key, remainder)."""
    tokens = rest.split()
    if not tokens:
        return None, rest
    for count in range(min(MAX_UNIT_WORDS, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:count])
        stripped = candidate.rstrip(".")
        key = ALIASES_EXACT.get(stripped)
        if key is None:
            key = ALIASES.get(stripped.lower())
        if key:
            remainder = " ".join(tokens[count:])
            # "1 cup" with nothing after it is fine, but "2 cans" as the whole
            # line is more likely shorthand than a unit with a missing item.
            return key, remainder
    return None, rest


def parse_ingredient(raw):
    """Split one written ingredient line into structured parts.

    Returns a dict with: raw, is_heading, qty, qty_max, unit, item.
    qty is None whenever we could not find a number we trust.
    """
    raw = (raw or "").strip()
    result = {
        "raw": raw,
        "is_heading": False,
        "qty": None,
        "qty_max": None,
        "unit": "",
        "item": raw,
    }
    if not raw:
        return result

    # "For the frosting:" style section labels.
    if raw.endswith(":") and not raw[0].isdigit():
        result["is_heading"] = True
        result["item"] = raw.rstrip(":").strip()
        return result

    text = _expand_vulgar(raw)

    qty = qty_max = None
    match = _RANGE_RE.match(text)
    if match:
        qty = _to_float(match.group("a"))
        qty_max = _to_float(match.group("b"))
        if qty is None or qty_max is None or qty_max < qty:
            qty = qty_max = None
            match = None
    if match is None:
        match = _SINGLE_RE.match(text)
        if match:
            qty = _to_float(match.group("a"))

    if match is None or qty is None or qty <= 0 or qty > 100000:
        # No usable number: keep the line exactly as written.
        result["item"] = raw
        return result

    rest = text[match.end():].strip()
    unit, remainder = _match_unit(rest)
    if unit and remainder.strip():
        result["unit"] = unit
        result["item"] = remainder.strip()
    else:
        result["item"] = rest

    result["qty"] = qty
    result["qty_max"] = qty_max
    if not result["item"]:
        # Something like "3 cups" with no food named; leave it alone.
        result["qty"] = None
        result["qty_max"] = None
        result["unit"] = ""
        result["item"] = raw
    return result


# ---------------------------------------------------------------------------
# Display helpers. The browser has its own copy of this logic in app.js so the
# multiply buttons feel instant; these are used for server-rendered output.
# ---------------------------------------------------------------------------

_DENOMINATORS = (2, 3, 4, 8, 16)


def format_amount(value):
    """Render a number the way a recipe card would: 1 1/2, not 1.5."""
    if value is None:
        return ""
    if value < 0.0001:
        return "0"
    whole = int(value)
    remainder = value - whole
    if remainder < 0.02:
        return str(whole) if whole else "0"

    best = None
    for den in _DENOMINATORS:
        num = round(remainder * den)
        if num <= 0 or num >= den:
            continue
        error = abs(remainder - num / den)
        if best is None or error < best[0] - 1e-9:
            best = (error, num, den)

    if best is None or best[0] > 0.035:
        text = ("%.2f" % value).rstrip("0").rstrip(".")
        return text

    _, num, den = best
    frac = Fraction(num, den)
    if whole:
        return "%d %d/%d" % (whole, frac.numerator, frac.denominator)
    return "%d/%d" % (frac.numerator, frac.denominator)


def unit_label(unit, qty):
    """English says "3/4 cup" and "1 1/2 cups": anything up to one is singular."""
    if not unit:
        return ""
    singular, plural = UNIT_NAMES.get(unit, (unit, unit))
    if qty is not None and qty <= 1.0 + 1e-6:
        return singular
    return plural


def describe(parsed, factor=1.0):
    """Full human-readable ingredient line at a given scale."""
    if parsed["qty"] is None:
        return parsed["raw"]
    qty = parsed["qty"] * factor
    text = format_amount(qty)
    if parsed.get("qty_max"):
        text += "-" + format_amount(parsed["qty_max"] * factor)
    label = unit_label(parsed["unit"], qty)
    parts = [text]
    if label:
        parts.append(label)
    parts.append(parsed["item"])
    return " ".join(p for p in parts if p)
