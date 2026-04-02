from __future__ import annotations

import re


CONTACT_PATTERNS = [
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", flags=re.IGNORECASE),
    re.compile(r"\+?\d[\d\s\-()]{7,}\d"),
    re.compile(r"(?:https?://)?t\.me/[\w_]+", flags=re.IGNORECASE),
    re.compile(r"@[A-Za-z0-9_]{4,}"),
]

INTENT_KEYWORDS = {
    "цена",
    "стоимость",
    "купить",
    "заказать",
    "интересно",
    "прайс",
    "свяжитесь",
    "написать",
    "консультация",
}


def analyze_comment(text: str) -> tuple[bool, bool, int, set[str]]:
    normalized = text.lower()

    has_contact = any(pattern.search(text) for pattern in CONTACT_PATTERNS)
    has_intent = any(keyword in normalized for keyword in INTENT_KEYWORDS)

    score = 0
    tags: set[str] = set()

    if has_contact:
        score += 3
        tags.add("contact_hint")

    if has_intent:
        score += 2
        tags.add("buying_intent")

    if len(text.strip()) > 100:
        score += 1
        tags.add("long_message")

    return has_contact, has_intent, score, tags
