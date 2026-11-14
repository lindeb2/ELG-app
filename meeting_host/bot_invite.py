"""OAuth invite for Meeting Mike.

Dynamic voice rooms apply their own overwrites, so guild-level View Channel
and Connect are not enough. Administrator lets the bot join whichever room
the host is sitting in.
"""
from __future__ import annotations

import base64

from meeting_host.host_secrets import get_discord_bot_token, get_discord_guild_id

# discord.Permissions.administrator
ADMINISTRATOR = 1 << 3

DYNAMIC_ROOM_HINT = (
    "Meeting Mike needs Administrator so it can join dynamically created voice "
    "rooms. Re-invite it from the host PC with: python -m meeting_host --invite"
)


def application_id_from_token(token: str) -> str:
    prefix = (token or "").split(".", 1)[0]
    padding = "=" * ((4 - len(prefix) % 4) % 4)
    return base64.b64decode(prefix + padding).decode("ascii")


def invite_url(*, permissions: int = ADMINISTRATOR) -> str:
    client_id = application_id_from_token(get_discord_bot_token())
    guild_id = get_discord_guild_id()
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions={int(permissions)}"
        "&scope=bot"
        f"&guild_id={guild_id}"
        "&disable_guild_select=true"
    )
