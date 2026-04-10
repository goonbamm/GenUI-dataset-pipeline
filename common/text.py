"""Shared text normalization helpers for pipeline stages."""

from __future__ import annotations

import re


def normalize_spaces(text: str) -> str:
    """Collapse whitespace runs to single spaces after trimming ends."""
    return re.sub(r"\s+", " ", text.strip())


def strip_list_prefix(text: str) -> str:
    """Remove common list prefixes such as '-', '1.' or '2)'."""
    return re.sub(r"^[\-\d\.)\s]+", "", text)


def normalize_text(text: str, *, strip_prefix: bool = False) -> str:
    """Normalize text for comparisons.

    Args:
        text: Raw text.
        strip_prefix: If True, also remove bullet/number prefixes.
    """
    normalized = text.strip().lower()
    if strip_prefix:
        normalized = strip_list_prefix(normalized)
    return normalize_spaces(normalized)
