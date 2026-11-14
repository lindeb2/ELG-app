"""Voice-channel name matching for Meeting Mike.

Discord keeps the user's capitalization and may add emoji variation
selectors, so the tuxedo boardroom fallback cannot use raw equality.
"""
from __future__ import annotations

import unicodedata

from meeting_host import config

_STRIP = ("\uFE0F", "\uFE0E", "\u200D")


def normalize_channel_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "")
    for mark in _STRIP:
        text = text.replace(mark, "")
    return " ".join(text.casefold().split())


def names_match(left: str, right: str) -> bool:
    return bool(left) and normalize_channel_name(left) == normalize_channel_name(right)


def is_fallback_voice_channel(name: str) -> bool:
    return names_match(name, config.FALLBACK_VOICE_CHANNEL_NAME)


def missing_connect_permissions(*, view_channel: bool, connect: bool) -> list[str]:
    missing: list[str] = []
    if not view_channel:
        missing.append("View Channel")
    if not connect:
        missing.append("Connect")
    return missing


def format_permission_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return " and ".join(names)
