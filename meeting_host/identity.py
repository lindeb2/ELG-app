"""Map Discord speakers to ELG usernames.

Order (first hit wins), per the locked plan:
  1. discord_user_id -> ELG username (Notification Preferences)
  2. Discord global_name / username vs stored discordname (case-insensitive)
  3. Guild nickname vs stored discordname (case-insensitive)
  4. Unmapped: keep None and label with the Discord display name
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscordSpeaker:
    discord_user_id: str
    username: str = ""
    global_name: str = ""
    nick: str = ""

    @property
    def display_name(self) -> str:
        return self.nick or self.global_name or self.username or self.discord_user_id


class IdentityResolver:
    def __init__(self, prefs_map: dict | None = None) -> None:
        prefs_map = prefs_map or {}
        self._by_discord_id: dict[str, str] = {}
        self._by_name: dict[str, str] = {}

        for elg_username, prefs in prefs_map.items():
            if not isinstance(prefs, dict):
                continue
            discord_id = str(prefs.get("discord_user_id") or "").strip()
            if discord_id:
                self._by_discord_id[discord_id] = elg_username
            discord_name = str(prefs.get("discordname") or "").strip().lower()
            if discord_name:
                self._by_name.setdefault(discord_name, elg_username)
            # ELG usernames often match the Discord handle; useful last resort.
            self._by_name.setdefault(elg_username.strip().lower(), elg_username)

    @classmethod
    def load(cls) -> "IdentityResolver":
        from meeting_host import mongo_host

        try:
            return cls(mongo_host.fetch_notification_prefs_map())
        except Exception:
            return cls({})

    def resolve(self, speaker: DiscordSpeaker) -> str | None:
        by_id = self._by_discord_id.get(str(speaker.discord_user_id).strip())
        if by_id:
            return by_id

        for candidate in (speaker.global_name, speaker.username, speaker.nick):
            key = str(candidate or "").strip().lower()
            if key and key in self._by_name:
                return self._by_name[key]

        return None

    def label(self, speaker: DiscordSpeaker) -> str:
        return self.resolve(speaker) or speaker.display_name
