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
from types import SimpleNamespace

import discord
from discord.ext import voice_recv

from meeting_host import config, dave_recv
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
        self._join_started = False
        self._audio_callbacks = 0
        self._stats_stop = threading.Event()

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
        if not self._channel_id:
            self._force_close()
            raise RecorderError("Could not join a Discord voice channel.")

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
            if self._join_started or self._ready.is_set() or self._stopped.is_set():
                return
            self._join_started = True
            try:
                await self._on_ready(host_discord_user_id)
            except Exception as exc:
                self._startup_error = str(exc) or type(exc).__name__
                self._log(f"join failed: {self._startup_error}")
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
        self._log(f"guild {guild.id} {guild.name!r} members={getattr(guild, 'member_count', '?')}")

        channel = await self._resolve_channel(guild, host_discord_user_id)
        self._log(f"joining channel {getattr(channel, 'id', '')} {getattr(channel, 'name', '')!r}")
        self._assert_can_connect(guild, channel)

        self._ensure_opus()

        try:
            voice_client = await channel.connect(
                cls=voice_recv.VoiceRecvClient,
                self_deaf=False,
                self_mute=True,
                timeout=20.0,
            )
        except discord.Forbidden:
            from meeting_host.bot_invite import DYNAMIC_ROOM_HINT

            raise RecorderError(
                "Meeting Mike does not have permission to connect to "
                f"'{getattr(channel, 'name', 'that voice channel')}'. {DYNAMIC_ROOM_HINT}"
            )
        except asyncio.TimeoutError:
            from meeting_host.bot_invite import DYNAMIC_ROOM_HINT

            raise RecorderError(
                f"Timed out joining '{getattr(channel, 'name', 'the voice channel')}'. "
                f"{DYNAMIC_ROOM_HINT}"
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            self._log(f"connect exception {type(exc).__name__}: {exc!r}")
            raise RecorderError(
                f"Could not join '{getattr(channel, 'name', 'the voice channel')}': {detail}"
            ) from exc

        self._voice_client = voice_client
        self._channel_id = str(getattr(channel, "id", "") or "")
        self._channel_name = str(getattr(channel, "name", "") or "")
        if not self._channel_id:
            raise RecorderError("Could not join a Discord voice channel.")

        self._t0 = time.monotonic()
        self._events.emit("joined", channel_name=self._channel_name)

        for member in getattr(channel, "members", []):
            if member.id == getattr(client.user, "id", None):
                continue
            self._register_participant(member, late=False)

        dave_recv.reset_stats()
        dave_recv.apply_voice_recv_patches()
        dave_recv.enable_dave_passthrough(voice_client)
        await self._wait_for_dave(voice_client)
        self._log(f"listening {dave_recv.dave_status_line(voice_client)}")

        voice_client.listen(
            voice_recv.BasicSink(self._on_audio),
            after=self._on_listen_done,
        )
        self._start_stats_logger()
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
        """Host's current voice/stage channel, else the tuxedo boardroom."""
        try:
            host_id = int(str(host_discord_user_id).strip())
        except (TypeError, ValueError):
            raise RecorderError("Discord user-ID is not a valid number.")

        if not self._recordable_channels(guild):
            try:
                await guild.fetch_channels()
            except Exception:
                pass

        if guild.get_member(host_id) is None:
            try:
                await guild.query_members(user_ids=[host_id])
            except Exception:
                try:
                    await guild.fetch_member(host_id)
                except Exception:
                    pass

        deadline = time.monotonic() + config.VOICE_JOIN_WAIT_SECONDS
        while True:
            channel = await self._look_up_channel(guild, host_id)
            if channel is not None:
                return channel
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.4)

        visible = [str(getattr(ch, "name", "") or "") for ch in self._recordable_channels(guild)]
        names = ", ".join(repr(name) for name in visible) if visible else "none"
        fallback = config.FALLBACK_VOICE_CHANNEL_NAME
        raise RecorderError(
            "You are not in a voice channel that Meeting Mike can join. "
            f"It looks for your current channel, then {fallback!r}. "
            f"Visible voice channels: {names}."
        )

    async def _look_up_channel(self, guild, host_id: int):
        from meeting_host.channel_match import is_fallback_voice_channel

        channel = self._channel_from_voice_state(guild, host_id)
        if channel is not None:
            hydrated = await self._hydrate_channel(guild, channel)
            if hydrated is not None:
                return hydrated

        for candidate in self._recordable_channels(guild):
            for connected in getattr(candidate, "members", []) or []:
                if getattr(connected, "id", None) == host_id:
                    return candidate

        for candidate in self._recordable_channels(guild):
            if is_fallback_voice_channel(str(getattr(candidate, "name", "") or "")):
                return candidate
        return None

    def _channel_from_voice_state(self, guild, host_id: int):
        states = getattr(guild, "voice_states", None) or {}
        state = states.get(host_id)
        if state is None:
            member = guild.get_member(host_id)
            state = getattr(member, "voice", None)
        if state is None:
            return None
        channel = getattr(state, "channel", None)
        if channel is not None:
            return channel
        channel_id = getattr(state, "channel_id", None)
        if not channel_id:
            return None
        found = guild.get_channel(channel_id)
        if found is None and self._client is not None:
            found = self._client.get_channel(channel_id)
        if found is not None:
            return found
        return SimpleNamespace(id=channel_id, name="")

    async def _hydrate_channel(self, guild, channel):
        if callable(getattr(channel, "connect", None)):
            return channel
        channel_id = getattr(channel, "id", None)
        if not channel_id:
            return None
        found = guild.get_channel(channel_id)
        if found is None and self._client is not None:
            found = self._client.get_channel(channel_id)
        if found is not None:
            return found
        try:
            return await guild.fetch_channel(channel_id)
        except discord.Forbidden:
            from meeting_host.bot_invite import DYNAMIC_ROOM_HINT

            raise RecorderError(
                "Meeting Mike can see that you are in a voice channel "
                f"but Discord is blocking the join. {DYNAMIC_ROOM_HINT}"
            )
        except Exception:
            return None

    @staticmethod
    def _recordable_channels(guild):
        channels = list(getattr(guild, "voice_channels", []) or [])
        channels.extend(list(getattr(guild, "stage_channels", []) or []))
        return channels

    def _assert_can_connect(self, guild, channel) -> None:
        from meeting_host.channel_match import format_permission_names, missing_connect_permissions

        me = getattr(guild, "me", None)
        if me is None and self._client is not None and self._client.user is not None:
            me = guild.get_member(self._client.user.id)
        if me is None:
            return

        perms = channel.permissions_for(me)
        missing = missing_connect_permissions(
            view_channel=bool(getattr(perms, "view_channel", False)),
            connect=bool(getattr(perms, "connect", False)),
        )
        name = str(getattr(channel, "name", "") or "that voice channel")
        self._log(
            f"channel perms {name!r} view={bool(getattr(perms, 'view_channel', False))} "
            f"connect={bool(getattr(perms, 'connect', False))}"
        )
        if not missing:
            return
        from meeting_host.bot_invite import DYNAMIC_ROOM_HINT

        needed = format_permission_names(missing)
        raise RecorderError(
            f"Meeting Mike found you in '{name}' but Discord is blocking it there "
            f"(missing {needed}). {DYNAMIC_ROOM_HINT}"
        )

    def _log(self, message: str) -> None:
        try:
            path = config.logs_dir(self._base_dir) / "host.log"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"{utc_now().isoformat()} {message}\n")
        except OSError:
            pass

    async def _wait_for_dave(self, voice_client, *, timeout: float = 5.0) -> None:
        session = dave_recv.dave_session_of(voice_client)
        if session is None:
            self._log("no DAVE session; inbound audio may be transport-only")
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(session, "ready", False):
                return
            await asyncio.sleep(0.1)
        self._log(f"DAVE session not ready after {timeout:.0f}s")

    def _start_stats_logger(self) -> None:
        self._stats_stop.clear()

        def _loop() -> None:
            while not self._stats_stop.wait(5.0):
                self._log(f"receive {dave_recv.snapshot_stats().line()}")

        threading.Thread(target=_loop, name="meeting-mike-recv-stats", daemon=True).start()

    def _on_listen_done(self, error) -> None:
        stats = dave_recv.snapshot_stats().line()
        if error:
            self._log(f"voice receive stopped: {type(error).__name__}: {error}; {stats}")
        else:
            self._log(f"voice receive stopped; {stats}")

    async def _async_teardown(self) -> None:
        self._stats_stop.set()
        self._log(f"receive final {dave_recv.snapshot_stats().line()}")
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
        try:
            self._write_audio(user, data)
        except Exception as exc:
            self._log(f"audio callback failed: {type(exc).__name__}: {exc}")

    def _write_audio(self, user, data) -> None:
        if self._stopped.is_set():
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

        speaker = self._user_for_audio(user, data)
        if speaker is None:
            return

        self._audio_callbacks += 1
        if self._audio_callbacks <= 5 or self._audio_callbacks % 250 == 0:
            self._log(
                f"audio callback #{self._audio_callbacks} user={getattr(speaker, 'id', None)} "
                f"pcm={len(pcm)} ssrc={getattr(packet, 'ssrc', None)}"
            )

        samples = decode_discord_pcm(pcm)
        if samples.size == 0:
            return

        writer = self._writer_for(speaker)
        if writer is None:
            return

        offset = max(0, self._elapsed_samples() - int(samples.size))
        writer.write(samples, offset_samples=offset)

    def _user_for_audio(self, user, data):
        with self._state_lock:
            known_ids = [participant.discord_user_id for participant in self._participants.values()]
        return dave_recv.resolve_audio_user(
            user,
            data,
            voice_client=self._voice_client,
            known_user_ids=known_ids,
        )

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
