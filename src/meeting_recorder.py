"""Discord voice recording: bot connection, per-user capture, storage.

Scope note (see step-1-recording-infrastructure.md): this runs the Discord
bot connection and audio capture on whichever single machine starts the
recording — not on every participant's install. "Start recording" is a
single-host action, similar in spirit to single_instance.py's single-owner
pattern but applied to a meeting rather than the whole app.

Fully local: the only network traffic this module produces is the bot's own
connection to Discord's gateway/voice servers. No other API calls, no
server-side processing.

Threading model mirrors the rest of this codebase's background workers
(meeting.py's _run_watcher, commit_transaction.py's worker thread,
system_tray.py's tray thread): a dedicated daemon thread owns its own
asyncio event loop; a threading.Event signals clean shutdown from outside
that thread. MeetingRecorder itself is not responsible for marshaling
callbacks back to the Tk main loop — callers (see meeting.py) wrap the
on_status/on_error/on_stopped callbacks with self.after(0, ...) themselves.

py-cord version note: requirements.txt pins py-cord[voice]>=2.7, and the
finished-recording callback signature changed around that version (older
releases call ``callback(sink, *args)``; 2.7+ calls ``callback(exception)``
with no sink argument at all). Rather than depend on either shape, the sink
object is captured via closure and the callback below accepts ``*args`` and
only looks for an exception among them, so it works either way.

py-cord's own per-user silence padding (inside VoiceClient.recv_audio) is
keyed to that user's RTP timestamps and resets whenever Discord assigns a
new SSRC — which happens on every reconnect — so it cannot be relied on for
either the meeting-start alignment or the reconnect-continuity requirement
called out in the plan. AlignedWaveSink below does not depend on it: it
independently tracks how many bytes *should* exist for a user at the moment
each packet arrives (based on wall-clock elapsed time since the shared
meeting start) and tops up with silence to close any gap before writing the
real payload. That makes it self-correcting regardless of whatever padding
py-cord already did, and it is what actually satisfies:
  - late joiners: padded from meeting start to their real join/first-speech
    time, not from 0.
  - disconnect/rejoin: keyed by persistent Discord user id (not SSRC), so a
    user's audio always lands in the same file; the deficit-padding closes
    the gap across the reconnect automatically.
  - every participant's file ends up the same total duration, aligned to
    one shared wall-clock origin.
"""
from __future__ import annotations

import asyncio
import threading
import time
import wave
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord

from app_secrets import (
    get_discord_bot_token,
    get_discord_guild_id,
)
from meeting_recorder_paths import (
    raw_audio_dir,
    user_wav_filename,
    write_meeting_meta,
)

# Discord voice PCM as decoded by py-cord: 48kHz, stereo, 16-bit — matches
# discord.sinks.WaveSink's own format constants.
CHANNELS = 2
SAMPLE_WIDTH = 2  # bytes per sample
SAMPLING_RATE = 48000  # Hz
FRAME_SIZE = CHANNELS * SAMPLE_WIDTH  # bytes per sample-frame across all channels
BYTES_PER_SECOND = FRAME_SIZE * SAMPLING_RATE
_SILENCE_BYTE = b"\x00"

_READY_TIMEOUT_SECONDS = 30
_STOP_DRAIN_TIMEOUT_SECONDS = 30
_CLOSE_TIMEOUT_SECONDS = 10
_POLL_INTERVAL_SECONDS = 0.2


class RecorderConfigError(Exception):
    """The Discord bot token/guild secrets aren't configured."""


# Fallback meeting room when the host isn't currently connected to voice (see
# resolve_recording_channel below). Not a secret/config value — this is a
# fixed, named room in the Discord server, the same idea as naming a
# physical meeting room.
RECORDING_CHANNEL_NAME = "\U0001F935Boardroom"


def get_recorder_guild_config() -> int:
    """Return guild_id if the recording feature is configured.

    Raises RecorderConfigError if the token/guild secrets are missing, so
    callers (meeting.py) can simply hide the recording control rather than
    show a button that would immediately fail to start. The voice channel
    itself is resolved dynamically at recording-start time (see
    resolve_recording_channel) rather than pinned to a fixed channel id.
    """
    try:
        get_discord_bot_token()
        guild_id = get_discord_guild_id()
    except RuntimeError as exc:
        raise RecorderConfigError(str(exc)) from exc
    return guild_id


def resolve_recording_channel(
    guild: discord.Guild, host_discord_user_id: int | None
) -> discord.VoiceChannel:
    """Pick the voice channel to record.

    Preference order:
      1. Wherever the host is currently connected in this guild (their
         linked Discord user id, from Settings -> "Discord user-ID") — the
         meeting is wherever the host actually is. Checked via each voice
         channel's already-cached member list (populated by the gateway's
         voice-state data) rather than Member.voice, since that stays
         correct without needing the privileged Members intent.
      2. The named RECORDING_CHANNEL_NAME room, as a fallback for when the
         host's Discord account isn't linked or their voice state isn't
         visible yet.
    """
    if host_discord_user_id is not None:
        for candidate in guild.voice_channels:
            if any(member.id == host_discord_user_id for member in candidate.members):
                return candidate

    named = discord.utils.get(guild.voice_channels, name=RECORDING_CHANNEL_NAME)
    if named is not None:
        return named

    raise RuntimeError(
        "Could not determine which voice channel to record: the host isn't "
        "currently connected to voice in this server, and no channel named "
        f"'{RECORDING_CHANNEL_NAME}' was found."
    )


class _UserTrack:
    __slots__ = ("wave_writer", "written_bytes", "path")

    def __init__(self, wave_writer: wave.Wave_write, path: Path) -> None:
        self.wave_writer = wave_writer
        self.written_bytes = 0
        self.path = path


class AlignedWaveSink(discord.sinks.Sink):
    """Per-user WAV sink aligned to one shared wall-clock origin.

    Files are written incrementally to disk (not buffered fully in memory)
    via the stdlib wave module, which supports incremental writeframes()
    calls on an open file handle.
    """

    def __init__(self, *, meeting_start_perf: float, audio_dir: Path, filters=None) -> None:
        super().__init__(filters=filters)
        self._meeting_start_perf = meeting_start_perf
        self._audio_dir = audio_dir
        self._tracks: dict[int, _UserTrack] = {}
        self._track_lock = threading.Lock()

    def _resolve_username(self, user: int) -> str:
        guild = getattr(self.vc, "guild", None)
        member = guild.get_member(user) if guild is not None else None
        if member is not None:
            return member.name
        return f"user{user}"

    def _open_track_locked(self, user: int, username: str | None) -> _UserTrack:
        track = self._tracks.get(user)
        if track is not None:
            return track
        resolved_username = username or self._resolve_username(user)
        filename = user_wav_filename(user, resolved_username)
        path = self._audio_dir / filename
        wave_writer = wave.open(str(path), "wb")
        wave_writer.setnchannels(CHANNELS)
        wave_writer.setsampwidth(SAMPLE_WIDTH)
        wave_writer.setframerate(SAMPLING_RATE)
        track = _UserTrack(wave_writer, path)
        self._tracks[user] = track
        return track

    def ensure_user(self, user: int, username: str | None = None) -> None:
        """Create this user's file now (silent so far) even if they never
        speak, so a present-but-quiet participant still gets a full-length
        WAV file. Safe to call more than once per user."""
        with self._track_lock:
            self._open_track_locked(user, username)

    def _pad_to_locked(self, track: _UserTrack, expected_bytes: int) -> None:
        expected_bytes -= expected_bytes % FRAME_SIZE
        deficit = expected_bytes - track.written_bytes
        if deficit <= 0:
            return
        try:
            track.wave_writer.writeframes(_SILENCE_BYTE * deficit)
            track.written_bytes += deficit
        except Exception:
            pass

    @discord.sinks.Filters.container
    def write(self, data: bytes, user: int) -> None:
        now = time.perf_counter()
        elapsed = max(0.0, now - self._meeting_start_perf)
        expected_bytes = int(elapsed * BYTES_PER_SECOND)

        with self._track_lock:
            track = self._open_track_locked(user, None)
            self._pad_to_locked(track, expected_bytes)

            usable_len = len(data) - (len(data) % FRAME_SIZE)
            if usable_len:
                try:
                    track.wave_writer.writeframes(data[:usable_len])
                    track.written_bytes += usable_len
                except Exception:
                    pass

    def cleanup(self) -> None:
        stop_perf = time.perf_counter()
        elapsed = max(0.0, stop_perf - self._meeting_start_perf)
        expected_bytes = int(elapsed * BYTES_PER_SECOND)
        with self._track_lock:
            for track in self._tracks.values():
                self._pad_to_locked(track, expected_bytes)
                try:
                    track.wave_writer.close()
                except Exception:
                    pass
            self.finished = True

    def written_files(self) -> dict[int, tuple[Path, int]]:
        """(path, total_bytes_written) per user, valid after cleanup()."""
        with self._track_lock:
            return {user: (t.path, t.written_bytes) for user, t in self._tracks.items()}


class _MeetingMetaBuilder:
    """Accumulates participant join/leave offsets for meta.json."""

    def __init__(self, *, guild_id: int, channel_id: int, meeting_start_utc: datetime) -> None:
        self._guild_id = guild_id
        self._channel_id = channel_id
        self._meeting_start_utc = meeting_start_utc
        self._meeting_end_utc: datetime | None = None
        self._participants: dict[int, dict[str, Any]] = {}

    def _entry(self, user_id: int, username: str, display_name: str) -> dict[str, Any]:
        entry = self._participants.get(user_id)
        if entry is None:
            entry = {
                "username": username,
                "display_name": display_name,
                "events": [],
                "file": None,
                "duration_seconds": None,
            }
            self._participants[user_id] = entry
        return entry

    def record_join(self, user_id: int, username: str, display_name: str, *, offset_seconds: float) -> None:
        entry = self._entry(user_id, username, display_name)
        entry["events"].append({"type": "join", "offset_seconds": round(offset_seconds, 3)})

    def record_leave(self, user_id: int, *, offset_seconds: float) -> None:
        if user_id not in self._participants:
            return
        self._participants[user_id]["events"].append(
            {"type": "leave", "offset_seconds": round(offset_seconds, 3)}
        )

    def finalize(self, meeting_end_utc: datetime) -> None:
        self._meeting_end_utc = meeting_end_utc

    def attach_files(self, written: dict[int, tuple[Path, int]]) -> None:
        for user_id, (path, total_bytes) in written.items():
            entry = self._participants.get(user_id)
            if entry is None:
                # Audio arrived from a user we never saw a voice-state join
                # for (e.g. present before the bot's cache warmed up).
                entry = self._entry(user_id, f"user{user_id}", f"user{user_id}")
            entry["file"] = path.name
            entry["duration_seconds"] = round(total_bytes / BYTES_PER_SECOND, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "guild_id": self._guild_id,
            "channel_id": self._channel_id,
            "meeting_start": self._meeting_start_utc.isoformat(),
            "meeting_end": self._meeting_end_utc.isoformat() if self._meeting_end_utc else None,
            "participants": {str(uid): entry for uid, entry in self._participants.items()},
        }


class MeetingRecorder:
    """Owns the bot connection + recording lifecycle for one meeting."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_signal = threading.Event()
        self._state_lock = threading.Lock()
        self._recording = False
        self._client: discord.Client | None = None
        self._vc: discord.VoiceClient | None = None
        self._on_status: Callable[[str], None] | None = None
        self._on_error: Callable[[Exception], None] | None = None
        self._on_stopped: Callable[[], None] | None = None
        self._last_meeting_start_utc: datetime | None = None

    @property
    def is_recording(self) -> bool:
        with self._state_lock:
            return self._recording

    @property
    def last_meeting_start_utc(self) -> datetime | None:
        """UTC start time of the most recently started recording.

        Lets callers (see meeting.py) locate that meeting's folder under
        meetings/{week}/ — e.g. to kick off Step 2 transcription once
        on_stopped fires — via meeting_recorder_paths.week_folder(dt),
        without re-deriving or guessing the timestamp themselves.
        """
        with self._state_lock:
            return self._last_meeting_start_utc

    def start(
        self,
        *,
        guild_id: int,
        host_discord_user_id: int | None,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_stopped: Callable[[], None],
    ) -> None:
        with self._state_lock:
            if self._recording:
                return
            self._recording = True

        self._on_status = on_status
        self._on_error = on_error
        self._on_stopped = on_stopped
        self._stop_signal = threading.Event()

        self._thread = threading.Thread(
            target=self._run,
            name="meeting_recorder",
            daemon=True,
            args=(guild_id, host_discord_user_id),
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_signal.set()

    @staticmethod
    def _safe_call(fn: Callable | None, *args: Any) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            pass

    def _run(self, guild_id: int, host_discord_user_id: int | None) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main(guild_id, host_discord_user_id))
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via on_error
            self._safe_call(self._on_error, exc)
        finally:
            with self._state_lock:
                self._recording = False
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None
            self._vc = None
            self._client = None
            self._safe_call(self._on_stopped)

    async def _async_main(self, guild_id: int, host_discord_user_id: int | None) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        client = discord.Client(intents=intents)
        self._client = client
        ready_event = asyncio.Event()

        @client.event
        async def on_ready() -> None:
            ready_event.set()

        token = get_discord_bot_token()
        bot_task = asyncio.ensure_future(client.start(token))
        ready_task = asyncio.ensure_future(ready_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {bot_task, ready_task},
                timeout=_READY_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_task not in done:
                # Either the bot connection errored out before becoming ready,
                # or we timed out — either way, do NOT cancel bot_task blindly:
                # on the success path it's the live client connection loop and
                # must keep running for the rest of the recording.
                ready_task.cancel()
                if bot_task in done:
                    exc = bot_task.exception()
                    if exc is not None:
                        raise exc
                    raise RuntimeError("Discord bot connection closed before becoming ready.")
                bot_task.cancel()
                raise RuntimeError("Timed out connecting the recording bot to Discord.")

            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await client.fetch_guild(guild_id)
                await guild.fetch_channels()

            channel = resolve_recording_channel(guild, host_discord_user_id)

            vc = await channel.connect()
            self._vc = vc

            meeting_start_utc = datetime.now(timezone.utc)
            meeting_start_perf = time.perf_counter()
            with self._state_lock:
                self._last_meeting_start_utc = meeting_start_utc
            audio_dir = raw_audio_dir(meeting_start_utc)

            sink = AlignedWaveSink(meeting_start_perf=meeting_start_perf, audio_dir=audio_dir)
            meta = _MeetingMetaBuilder(
                guild_id=guild_id, channel_id=channel.id, meeting_start_utc=meeting_start_utc
            )

            for member in list(channel.members):
                if member.bot:
                    continue
                sink.ensure_user(member.id, member.name)
                meta.record_join(member.id, member.name, member.display_name, offset_seconds=0.0)

            async def on_voice_state_update(member, before, after) -> None:
                if member.bot:
                    return
                was_here = before.channel is not None and before.channel.id == channel.id
                now_here = after.channel is not None and after.channel.id == channel.id
                if now_here and not was_here:
                    offset = max(0.0, time.perf_counter() - meeting_start_perf)
                    sink.ensure_user(member.id, member.name)
                    meta.record_join(member.id, member.name, member.display_name, offset_seconds=offset)
                elif was_here and not now_here:
                    offset = max(0.0, time.perf_counter() - meeting_start_perf)
                    meta.record_leave(member.id, offset_seconds=offset)

            client.add_listener(on_voice_state_update, "on_voice_state_update")

            finished_event = asyncio.Event()
            recording_exceptions: list[BaseException] = []

            async def _on_recording_finished(*args: Any, **_kwargs: Any) -> None:
                for arg in args:
                    if isinstance(arg, BaseException):
                        recording_exceptions.append(arg)
                finished_event.set()

            vc.start_recording(sink, _on_recording_finished)
            self._safe_call(self._on_status, "recording")

            while not self._stop_signal.is_set():
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

            if vc.recording:
                vc.stop_recording()

            try:
                await asyncio.wait_for(finished_event.wait(), timeout=_STOP_DRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                pass  # Best-effort: cleanup() has already flushed/closed the files regardless.

            meta.finalize(datetime.now(timezone.utc))
            meta.attach_files(sink.written_files())
            write_meeting_meta(meeting_start_utc, meta.to_dict())

            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

            if recording_exceptions:
                raise recording_exceptions[0]
        finally:
            try:
                await client.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(bot_task, timeout=_CLOSE_TIMEOUT_SECONDS)
            except Exception:
                pass
