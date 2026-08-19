"""Parse the model's structured output into recommendations.

Tolerant to minor format drift: mixed case markers, ``-``/``–`` instead of
``:``, markdown bold/headers, numbered-list prefixes, missing RECOMMENDATION
headers, out-of-range priorities. Returns at most ``MAX_RECOMMENDATIONS``
items; returns ``[]`` when nothing parseable is found (callers then fall back
to rule-based recommendations).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.prompts import MAX_RECOMMENDATIONS

_PRIORITY_RE = re.compile(r"PRIORITY\s*[:\-–—=]?\s*(\d+)", re.IGNORECASE)
_TITLE_RE = re.compile(r"TITLE\s*[:\-–—=]?\s*(?P<title>[^\n]+)", re.IGNORECASE)
_BODY_RE = re.compile(r"BODY\s*[:\-–—=]?\s*(?P<body>.+)", re.IGNORECASE | re.DOTALL)
# Trailing "RECOMMENDATION 2" / "2." / "2)" header bleeding into a body chunk.
_TRAILING_HEADER_RE = re.compile(
    r"\s*(?:RECOMMENDATION\s*#?\s*\d*\.?|\d+\s*[.)])\s*$", re.IGNORECASE
)
_MD_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)

DEFAULT_PRIORITY = 3
MIN_PRIORITY, MAX_PRIORITY = 1, 5


@dataclass(frozen=True)
class ParsedRecommendation:
    priority: int
    title: str
    body: str


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return _MD_HEADER_RE.sub("", text)


def _clamp_priority(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:  # pragma: no cover - regex guarantees digits
        return DEFAULT_PRIORITY
    return max(MIN_PRIORITY, min(MAX_PRIORITY, value))


def _normalize(text: str) -> str:
    text = _TRAILING_HEADER_RE.sub("", text.strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("<>").strip("\"'").strip()


def _extract_title_body(chunk: str) -> tuple[str, str]:
    title = ""
    body = ""

    body_match = _BODY_RE.search(chunk)
    title_search_zone = chunk[: body_match.start()] if body_match else chunk
    title_match = _TITLE_RE.search(title_search_zone)

    if title_match:
        title = _normalize(title_match.group("title"))
    if body_match:
        body = _normalize(body_match.group("body"))

    if not body:
        # No BODY marker: use whatever text follows the title line (or the
        # whole chunk when there is no TITLE marker either).
        remainder = chunk[title_match.end():] if title_match else chunk
        body = _normalize(remainder)
    if not title and body:
        # Derive a title from the first sentence of the body.
        first_sentence = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
        title = first_sentence[:80].rstrip(".")
    if not body and title:
        body = title
    return title, body


def parse_recommendations(text: str | None) -> list[ParsedRecommendation]:
    """Parse model output into 1..MAX_RECOMMENDATIONS recommendations."""
    if not text or not text.strip():
        return []

    cleaned = _clean(text)
    matches = list(_PRIORITY_RE.finditer(cleaned))
    if not matches:
        return []

    results: list[ParsedRecommendation] = []
    seen_titles: set[str] = set()
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(cleaned)
        chunk = cleaned[match.end():end]
        title, body = _extract_title_body(chunk)
        if not title and not body:
            continue
        key = title.casefold()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        results.append(
            ParsedRecommendation(
                priority=_clamp_priority(match.group(1)), title=title, body=body
            )
        )
        if len(results) >= MAX_RECOMMENDATIONS:
            break
    return results
