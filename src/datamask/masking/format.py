"""Format-preserving transformations.

Requirement #5 stresses that replacements must keep the *shape* of the original
value: a 6-character password becomes another 6-character string, a digit stays
a digit, a letter stays a letter, and separators (``-``, ``@``, spaces) are kept
in place. These helpers provide that, driven by a seeded RNG so the mapping is
deterministic (consistent) for a given input.
"""
from __future__ import annotations

import hashlib
import random
import string
from typing import Optional


def seeded_rng(value: str, seed: Optional[str]) -> random.Random:
    """Return a deterministic RNG derived from ``value`` and a global ``seed``.

    The same (value, seed) pair always yields the same RNG, so the same input is
    always masked to the same output — across columns, tables and runs.
    """
    digest = hashlib.sha256(f"{seed or ''}:{value}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def random_char_like(ch: str, rng: random.Random) -> str:
    """Return a random character of the same *class* as ``ch``."""
    if ch.isdigit():
        return rng.choice(string.digits)
    if ch.isupper():
        return rng.choice(string.ascii_uppercase)
    if ch.islower():
        return rng.choice(string.ascii_lowercase)
    # Punctuation, whitespace, separators: preserve as-is.
    return ch


def format_preserving_random(value: str, rng: random.Random) -> str:
    """Replace each character with a random one of the same class.

    Examples
    --------
    ``"Ab3-9z"`` -> ``"Qf7-2k"`` (same length, same digit/letter/sep layout).
    """
    return "".join(random_char_like(ch, rng) for ch in value)


def match_length(replacement: str, original: str, rng: random.Random) -> str:
    """Pad or trim ``replacement`` so it matches ``len(original)``.

    Padding uses characters consistent with the replacement's own style.
    """
    if len(replacement) == len(original):
        return replacement
    if len(replacement) > len(original):
        return replacement[: len(original)]
    pad_pool = string.ascii_lowercase
    pad = "".join(rng.choice(pad_pool) for _ in range(len(original) - len(replacement)))
    return replacement + pad


def preserve_case(template: str, replacement: str) -> str:
    """Apply the upper/lower case pattern of ``template`` onto ``replacement``."""
    out = []
    for i, ch in enumerate(replacement):
        if i < len(template) and template[i].isupper():
            out.append(ch.upper())
        else:
            out.append(ch.lower())
    return "".join(out)
