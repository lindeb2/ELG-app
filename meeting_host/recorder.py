"""Discord voice recorder: one aligned WAV per speaker plus attendance.

The Discord client runs on its own asyncio loop in a background thread so the
main thread stays free to read commands from ELG.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import voice_recv

from meeting_host import config
from meeting_host.audio_mix import AlignedWavWriter, decode_discord_pcm
from meeting_host.host_secrets import get_discord_bot_token, get_discord_guild_id
from meeting_host.identity import DiscordSpeaker, IdentityResolver
from meeting_host.period import utc_now


class RecorderError(RuntimeError):
    """Fatal, user-presentable recorder failure."""


@dataclass
class Participant:
    discord_user_id: str
    discord_username: str = ""
    discord_global_name: str = ""
    discord_nick: str = ""
    elg_username: str | None = None
    joined_at: str = ""
    left_at: str | None = None
    joined_late: bool = False
    audio_path: str = ""

    def to_json(self) -> dict:
        return {
            "discord_user_id": self.discord_user_id,
            "discord_username": self.discord_username,
            "discord_global_name": self.discord_global_name,
            "discord_nick": self.discord_nick,
            "elg_username": self.elg_username,
            "joined_at": self.joined_at,
            "left_at": self.left_at,
            "joined_late": self.joined_late,
            "audio_path": self.audio_path,
        }


@dataclass
class RecordingResult:
    meeting_dir: Path
    channel_id: str
    channel_name: str
    started_at: datetime
    ended_at: datetime
    participants: list[Participant] = field(default_factory=list)


def _speaker_from_member(member) -> DiscordSpeaker:
    return DiscordSpeaker(
        discord_user_id=str(getattr(member, "id", "")),
        username=str(getattr(member, "name", "") or ""),
        global_name=str(getattr(member, "global_name", "") or ""),
        nick=str(getattr(member, "nick", "") or ""),
    )


class MeetingRecorder:
    def __init__(self, *, events, base_dir: Path) -> None:
        self._events = events
        self._base_dir = base_dir
        self._resolver = IdentityResolver.load()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: discord.Client | None = None
        self._voice_client = None

        self._writers: dict[str, AlignedWavWriter] = {}
        self._participants: dict[str, Participant] = {}
        self._state_lock = threading.Lock()

        self._t0: float | None = None
        self._started_at: datetime | None = None
        self._meeting_dir: Path | None = None
        self._channel_id = ""
        self._channel_name = ""
        self._host_elg_user = ""
        self._host_discord_user_id = ""
        self._ready = threading.Event()
        self._startup_error: str | None = None
        self._stopped = threading.Event()

    # --- public API ------------------------------------------------------
    @property
    def meeting_dir(self) -> Path | None:
        return self._meeting_dir

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def start(self, *, host_discord_user_id: str, timeout: float = 90.0) -> None:
        """Join voice and begin recording. Raises RecorderError on failure."""
        self._started_at = utc_now()
        self._meeting_dir = config.meeting_dir(self._base_dir, self._started_at)

        self._thread = threading.Thread(
            target=self._run_loop,
            args=(host_discord_user_id,),
            name="meeting-mike-discord",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout):
            self._force_close()
            raise RecorderError("Timed out connecting to Discord.")
        if self._startup_error:
            self._force_close()
            raise RecorderError(self._startup_error)

    def stop(self, *, host_elg_user: str, host_discord_user_id: str) -> RecordingResult:
        """Leave voice, flush every stream and write attendance.json."""
        if self._stopped.is_set():
            return self._build_result(host_elg_user, host_discord_user_id)
        self._stopped.set()

        loop = self._loop
        if loop is not None and not loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(self._async_teardown(), loop)
            try:
                future.result(timeout=30)
            except Exception:
                pass

        if self._thread is not None:
            self._thread.join(timeout=15)

        ended_at = utc_now()
        with self._state_lock:
            longest = max((writer.samples_written for writer in self._writers.values()), default=0)
            for writer in self._writers.values():
                writer.pad_to(longest)
                writer.close()
            for participant in self._participants.values():
                if participant.left_at is None:
                    participant.left_at = ended_at.isoformat()

        result = self._build_result(host_elg_user, host_discord_user_id, ended_at=ended_at)
        self._write_attendance(result)
        return result

    def _build_result(
        self,
        host_elg_user: str,
        host_discord_user_id: str,
        *,
        ended_at: datetime | None = None,
    ) -> RecordingResult:
        with self._state_lock:
            participants = list(self._participants.values())
        return RecordingResult(
            meeting_dir=self._meeting_dir or config.mike_dir(self._base_dir),
            channel_id=self._channel_id,
            channel_name=self._channel_name,
            started_at=self._started_at or utc_now(),
            ended_at=ended_at or utc_now(),
            participants=participants,
        )

    def _write_attendance(self, result: RecordingResult) -> None:
        payload = {
            "started_at": result.started_at.isoformat(),
            "ended_at": result.ended_at.isoformat(),
            "channel_id": result.channel_id,
            "channel_name": result.channel_name,
            "host_elg_user": self._host_elg_user,
            "host_discord_user_id": self._host_discord_user_id,
            "participants": [p.to_json() for p in result.participants],
        }
        path = result.meeting_dir / "attendance.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- asyncio plumbing ------------------------------------------------
    def _run_loop(self, host_discord_user_id: str) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main(host_discord_user_id))
        except Exception as exc:
            if not self._ready.is_set():
                self._startup_error = str(exc)
                self._ready.set()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _async_main(self, host_discord_user_id: str) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True

        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready() -> None:
            try:
                await self._on_ready(host_discord_user_id)
            except Exception as exc:
                self._startup_error = str(exc)
                self._ready.set()
                await self._async_teardown()

        @client.event
        async def on_voice_state_update(member, before, after) -> None:
            self._handle_voice_state(member, before, after)

        try:
            await client.start(get_discord_bot_token())
        except discord.LoginFailure:
            self._startup_error = "Discord rejected the Meeting Mike bot token."
            self._ready.set()
        except Exception as exc:
            if not self._ready.is_set():
                self._startup_error = f"Discord connection failed: {exc}"
                self._ready.set()

    async def _on_ready(self, host_discord_user_id: str) -> None:
        client = self._client
        assert client is not None

        guild = client.get_guild(get_discord_guild_id())
        if guild is None:
            try:
                guild = await client.fetch_guild(get_discord_guild_id())
            except Exception:
                guild = None
        if guild is None:
            raise RecorderError("Meeting Mike is not in the configured Discord server.")

        channel = await self._resolve_channel(guild, host_discord_user_id)

        self._ensure_opus()

        voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False, self_mute=True)
        self._voice_client = voice_client
        self._channel_id = str(channel.id)
        self._channel_name = str(channel.name)

        self._t0 = time.monotonic()
        self._events.emit("joined", channel_name=self._channel_name)

        for member in getattr(channel, "members", []):
            if member.id == getattr(client.user, "id", None):
                continue
            self._register_participant(member, late=False)

        voice_client.listen(voice_recv.BasicSink(self._on_audio))
        self._ready.set()

    @staticmethod
    def _ensure_opus() -> None:
        if discord.opus.is_loaded():
            return
        try:
            discord.opus._load_default()
        except Exception:
            pass
        if not discord.opus.is_loaded():
            raise RecorderError(
                "Opus audio library is unavailable, so Discord audio cannot be decoded."
            )

    async def _resolve_channel(self, guild, host_discord_user_id: str):
        """Host's current voice channel, else the tuxedo boardroom channel."""
        member = guild.get_member(int(host_discord_user_id)) if host_discord_user_id else None
        if member is None and host_discord_user_id:
            try:
                member = await guild.fetch_member(int(host_discord_user_id))
            except Exception:
                member = None

        voice_state = getattr(member, "voice", None)
        channel = getattr(voice_state, "channel", None)
        if channel is not None:
            return channel

        for candidate in getattr(guild, "voice_channels", []):
            if candidate.name == config.FALLBACK_VOICE_CHANNEL_NAME:
                return candidate

        raise RecorderError(
            "You are not in a voice channel and "
            f"'{config.FALLBACK_VOICE_CHANNEL_NAME}' does not exist."
        )

    async def _async_teardown(self) -> None:
        voice_client = self._voice_client
        if voice_client is not None:
            try:
                voice_client.stop_listening()
            except Exception:
                pass
            try:
                await voice_client.disconnect(force=True)
            except Exception:
                pass
            self._voice_client = None

        client = self._client
        if client is not None and not client.is_closed():
            try:
                await client.close()
            except Exception:
                pass

    def _force_close(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._async_teardown(), loop).result(timeout=10)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=10)

    # --- recording callbacks ---------------------------------------------
    def _elapsed_samples(self) -> int:
        if self._t0 is None:
            return 0
        return int(max(0.0, time.monotonic() - self._t0) * config.SAMPLE_RATE)

    def _on_audio(self, user, data) -> None:
        """Sink callback on a library thread: keep it cheap and thread-safe."""
        if user is None or self._stopped.is_set():
            return

        packet = getattr(data, "packet", None)
        if packet is not None:
            is_silence = getattr(packet, "is_silence", None)
            try:
                if callable(is_silence) and is_silence():
                    return
            except Exception:
                pass

        pcm = getattr(data, "pcm", None)
        if not pcm:
            return

        samples = decode_discord_pcm(pcm)
        if samples.size == 0:
            return

        writer = self._writer_for(user)
        if writer is None:
            return

        offset = max(0, self._elapsed_samples() - int(samples.size))
        writer.write(samples, offset_samples=offset)

    def _writer_for(self, member) -> AlignedWavWriter | None:
        discord_id = str(getattr(member, "id", "") or "")
        if not discord_id or self._meeting_dir is None:
            return None

        with self._state_lock:
            writer = self._writers.get(discord_id)
            if writer is not None:
                return writer

        participant = self._register_participant(member, late=None)
        path = self._meeting_dir / "audio" / "users" / f"{discord_id}.wav"

        with self._state_lock:
            writer = self._writers.get(discord_id)
            if writer is None:
                try:
                    writer = AlignedWavWriter(path)
                except OSError:
                    return None
                # Everyone shares one timeline, so a first packet arriving late
                # means real silence before it.
                writer.pad_to(max(0, self._elapsed_samples()))
                self._writers[discord_id] = writer
                if participant is not None:
                    participant.audio_path = str(path.relative_to(self._meeting_dir).as_posix())
            return writer

    def _register_participant(self, member, *, late: bool | None) -> Participant | None:
        discord_id = str(getattr(member, "id", "") or "")
        if not discord_id:
            return None

        speaker = _speaker_from_member(member)
        elapsed = 0.0 if self._t0 is None else max(0.0, time.monotonic() - self._t0)
        if late is None:
            late = elapsed > config.LATE_JOIN_GRACE_SECONDS

        with self._state_lock:
            existing = self._participants.get(discord_id)
            if existing is not None:
                existing.left_at = None
                if not existing.discord_nick:
                    existing.discord_nick = speaker.nick
                return existing

            participant = Participant(
                discord_user_id=discord_id,
                discord_username=speaker.username,
                discord_global_name=speaker.global_name,
                discord_nick=speaker.nick,
                elg_username=self._resolver.resolve(speaker),
                joined_at=utc_now().isoformat(),
                joined_late=bool(late),
            )
            self._participants[discord_id] = participant

        self._events.emit(
            "participant",
            discord_user_id=discord_id,
            elg_username=participant.elg_username,
            display_name=speaker.display_name,
            action="join",
            joined_late=participant.joined_late,
        )
        return participant

    def _handle_voice_state(self, member, before, after) -> None:
        if self._stopped.is_set() or not self._channel_id:
            return
        client = self._client
        if client is not None and member.id == getattr(client.user, "id", None):
            return

        channel_id = self._channel_id
        was_here = str(getattr(getattr(before, "channel", None), "id", "")) == channel_id
        is_here = str(getattr(getattr(after, "channel", None), "id", "")) == channel_id

        if is_here and not was_here:
            self._register_participant(member, late=None)
        elif was_here and not is_here:
            discord_id = str(member.id)
            with self._state_lock:
                participant = self._participants.get(discord_id)
                if participant is not None:
                    participant.left_at = utc_now().isoformat()
            self._events.emit(
                "participant",
                discord_user_id=discord_id,
                elg_username=(participant.elg_username if participant else None),
                display_name=_speaker_from_member(member).display_name,
                action="leave",
            )

    def set_host(self, *, elg_username: str, discord_user_id: str) -> None:
        self._host_elg_user = elg_username
        self._host_discord_user_id = discord_user_id
