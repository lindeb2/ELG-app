"""Hide a baked-in secret from a casual look through the install folder.

This is not encryption. The installer contains both the sealed value and this
decoder, so anyone who can read the installed files can recover the key.
The practical control is a spend limit on the Groq account. What this stops
is the raw key showing up in a text search of the Meeting Host directory.
"""
from __future__ import annotations

import base64

_PREFIX = "sealed:"
_MASK = b"ELG-Meeting-Host-groq-v1"


def seal_secret(value: str) -> str:
    raw = value.encode("utf-8")
    mixed = bytes(byte ^ _MASK[index % len(_MASK)] for index, byte in enumerate(raw))
    return _PREFIX + base64.urlsafe_b64encode(mixed).decode("ascii")


def unseal_secret(value: str) -> str:
    if not value.startswith(_PREFIX):
        return value
    mixed = base64.urlsafe_b64decode(value[len(_PREFIX) :].encode("ascii"))
    raw = bytes(byte ^ _MASK[index % len(_MASK)] for index, byte in enumerate(mixed))
    return raw.decode("utf-8")
