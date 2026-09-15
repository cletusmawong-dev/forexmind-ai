"""Plain-text guarantee: every user-visible string leaves the server as ASCII.

Fancy typography (curly quotes, arrows, middle dots, fullwidth characters)
renders through CJK fallback fonts on many phones and reads as "Chinese".
The AI provider can emit such characters, so every AI-written or composed
string is normalized before it is stored or pushed.
"""
from __future__ import annotations

_TABLE = str.maketrans({
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00b7": "-",
    "\u2192": "->", "\u2190": "<-", "\u2248": "~", "\u00b1": "+/-",
    "\u00d7": "x", "\uff0b": "+", "\u2022": "-", "\u25cf": "*",
    "\u25b2": "^", "\u25bc": "v", "\u00a0": " ",
})


def to_plain(text: str | None) -> str:
    """Normalize fancy punctuation to plain ASCII."""
    return (text or "").translate(_TABLE)
