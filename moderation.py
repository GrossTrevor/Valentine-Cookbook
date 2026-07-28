"""Automatic moderation so nobody has to babysit the site.

Everything here runs offline and costs nothing: a normalized wordlist check, a
handful of spam heuristics, a civility check for insults aimed at a person, and
rate limiting. `check()` returns a friendly, specific message when something is
refused -- being vague just makes well-meaning relatives give up.

If you ever want stronger nuance, set MODERATION_AI=1 and ANTHROPIC_API_KEY and
`ai_review()` below becomes an extra layer. It is off by default.
"""

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WORDLIST_PATH = os.path.join(HERE, "blocked_words.txt")

_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "8": "b", "@": "a", "$": "s", "!": "i", "|": "l",
})


def _load_wordlist():
    words = set()
    try:
        with open(WORDLIST_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.add(line.lower())
    except OSError:
        pass
    return words


BLOCKED_WORDS = _load_wordlist()


def normalize(text):
    """Fold away the usual tricks: accents, leetspeak, stretched letters."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().translate(_LEET)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)      # sooooo -> soo
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # "f u c k" and "f.u.c.k" both land here as single letters; glue runs of
    # them back together so the wordlist sees the real word.
    return re.sub(
        r"\b(?:[a-z] ){2,}[a-z]\b",
        lambda m: m.group(0).replace(" ", ""),
        text,
    )


URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|ru|cn|xyz|top|shop|info|biz|io)\b)", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I)
PHONE_RE = re.compile(r"\b(?:\+?\d[\s.-]?){9,}\d\b")

SPAM_PHRASES = [
    "click here", "buy now", "limited time offer", "make money", "work from home",
    "free money", "crypto", "bitcoin", "forex", "casino", "viagra", "cialis",
    "seo services", "cheap meds", "loan approval", "wire transfer", "gift card",
    "telegram me", "whatsapp me", "investment opportunity", "get rich",
]

# Insults pointed at a person, which is the thing that actually ruins a family
# comment thread. Generic frustration ("this recipe is awful") is allowed.
INSULT_RE = re.compile(
    r"\b(?:you|u|ur|your|youre|he|she|they|shes|hes|theyre)\b[^.!?]{0,24}?\b"
    r"(?:idiot|idiots|moron|morons|stupid|dumb|dummy|fool|fools|loser|losers|"
    r"pathetic|worthless|garbage|trash|disgusting|ugly|fat|hate you|shut up)\b"
)
THREAT_RE = re.compile(
    r"\b(?:kill|hurt|beat|shoot|stab)\b\s+(?:you|u|him|her|them|yourself|themselves)\b"
    r"|\bgo\s+die\b|\bkys\b"
)


class Rejected(Exception):
    """Raised with a message we are happy to show the submitter."""


def _find_blocked_word(text):
    """Whole-word matching only.

    Substring matching is tempting but wrong: it turns "molasses" and
    "Assam tea" into rejected recipes, and a cookbook that refuses molasses is
    worse than one that misses a creatively spelled insult.
    """
    tokens = set(normalize(text).split())
    hit = tokens & BLOCKED_WORDS
    return sorted(hit)[0] if hit else None


def check(text, kind="comment", allow_links=False):
    """Validate one field of user writing.

    kind is used only to word the refusal. Raises Rejected on a problem.
    """
    text = (text or "").strip()
    if not text:
        return text

    word = _find_blocked_word(text)
    if word:
        raise Rejected(
            "That wording will not post. Please rewrite it without the "
            "offensive language and try again."
        )

    if THREAT_RE.search(normalize(text)):
        raise Rejected("That reads as a threat, so it will not post.")

    if INSULT_RE.search(normalize(text)):
        raise Rejected(
            "Please keep it friendly. Comments about the food are welcome; "
            "comments about a person are not."
        )

    lowered = text.lower()
    for phrase in SPAM_PHRASES:
        if phrase in lowered:
            raise Rejected("That looks like an advertisement, so it will not post.")

    if not allow_links and URL_RE.search(text):
        raise Rejected(
            "Web links are not allowed here. Please describe it in your own "
            "words instead."
        )
    if not allow_links and (EMAIL_RE.search(text) or PHONE_RE.search(text)):
        raise Rejected(
            "Please leave out email addresses and phone numbers so they do not "
            "end up public."
        )

    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 25:
        shouting = sum(1 for c in letters if c.isupper()) / len(letters)
        if shouting > 0.7:
            raise Rejected(
                "That is written in all capital letters, which reads as "
                "shouting. Please retype it normally."
            )

    if re.search(r"(.{4,}?)\1{4,}", text):
        raise Rejected("That looks like repeated filler text, so it will not post.")

    if ai_enabled():
        verdict = ai_review(text, kind)
        if verdict:
            raise Rejected(verdict)

    return text


def check_name(name):
    name = (name or "").strip()
    if len(name) < 2:
        raise Rejected("Please enter your full name.")
    if len(name) > 80:
        raise Rejected("That name is too long.")
    if not re.match(r"^[A-Za-z][A-Za-z .'\-À-ɏ]*$", name):
        raise Rejected("Please enter your name using letters only.")
    if _find_blocked_word(name):
        raise Rejected("Please enter your real name.")
    if " " not in name.strip():
        raise Rejected("Please enter your first and last name.")
    return name


def check_credit(name):
    """Validate 'whose recipe is this' -- deliberately looser than check_name.

    An account holder gives a real first and last name, but a recipe gets
    credited to whoever the family calls them: "Aunt Ruth", "Grandma",
    "Barb Valentine". Requiring a surname here would be wrong.
    """
    name = (name or "").strip()
    if not name:
        return ""
    if len(name) < 2:
        raise Rejected("Please write out the name a little more fully.")
    if len(name) > 60:
        raise Rejected("That name is too long.")
    if not re.match(r"^[A-Za-z][A-Za-z .'\-À-ɏ]*$", name):
        raise Rejected(
            "Please use letters only for the name the recipe is credited to."
        )
    if _find_blocked_word(name):
        raise Rejected("Please use a real name for the recipe credit.")
    return name


# --- optional AI layer, dormant unless you turn it on -----------------------

def ai_enabled():
    return os.environ.get("MODERATION_AI") == "1" and os.environ.get("ANTHROPIC_API_KEY")


def ai_review(text, kind):
    """Return a refusal message, or None if the text is fine.

    Deliberately fails open: if the API is down, the wordlist checks above have
    already run and the site keeps working.
    """
    prompt = (
        "You screen submissions to a family recipe website. Reply with exactly "
        "OK if the text is acceptable, or REJECT: followed by one short polite "
        "sentence if it contains harassment, hate, threats, sexual content, "
        "advertising, or is not about food at all. Be lenient about spelling, "
        "grammar, and strong opinions about recipes.\n\n"
        "Submission type: %s\nText:\n%s" % (kind, text[:4000])
    )
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            # Same reason as in mailer.py: urllib's default "Python-urllib/3.x"
            # is a signature CDNs in front of APIs routinely block.
            "user-agent": "ValentineCookbook/1.0 (+https://valentinecookbook.com)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        reply = "".join(
            block.get("text", "") for block in body.get("content", [])
        ).strip()
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
        return None
    if reply.upper().startswith("REJECT"):
        detail = reply.split(":", 1)[1].strip() if ":" in reply else ""
        return detail or "That submission was not accepted. Please rephrase it."
    return None
