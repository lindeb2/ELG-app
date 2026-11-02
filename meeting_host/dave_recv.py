"""Decrypt inbound Discord voice (DAVE) before opus decode.

discord-ext-voice-recv 0.5.2a only undoes RTP transport encryption. Since
March 2026 Discord voice is always DAVE E2EE, so those frames are still
ciphertext: opus decode raises, the packet-router thread dies, and Meeting
Mike writes empty WAVs. discord.py 2.7 already keeps a DaveSession for
sending; reuse it on receive.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

_log = logging.getLogger(__name__)

DAVE_FRAME_MAGIC = b"\xfa\xfa"
DAVE_MIN_LEN = 12
_PATCHED_ATTR = "_elg_dave_patched"


@dataclass
class RecvStats:
    packets: int = 0
    dave_ok: int = 0
    dave_fail: int = 0
    dave_skipped: int = 0
    passthrough: int = 0
    opus_errors: int = 0
    pcm_frames: int = 0
    pcm_bytes: int = 0

    def line(self) -> str:
        return (
            f"packets={self.packets} pcm_frames={self.pcm_frames} "
            f"pcm_bytes={self.pcm_bytes} dave_ok={self.dave_ok} "
            f"dave_fail={self.dave_fail} dave_skipped={self.dave_skipped} "
            f"passthrough={self.passthrough} opus_errors={self.opus_errors}"
        )


STATS = RecvStats()
_patches_applied = False


def reset_stats() -> None:
    global STATS
    STATS = RecvStats()


def snapshot_stats() -> RecvStats:
    return RecvStats(
        packets=STATS.packets,
        dave_ok=STATS.dave_ok,
        dave_fail=STATS.dave_fail,
        dave_skipped=STATS.dave_skipped,
        passthrough=STATS.passthrough,
        opus_errors=STATS.opus_errors,
        pcm_frames=STATS.pcm_frames,
        pcm_bytes=STATS.pcm_bytes,
    )


def looks_like_dave_frame(payload: bytes | None) -> bool:
    """DAVE wraps Opus and appends 0xFAFA at the *end* of the media frame."""
    data = bytes(payload or b"")
    return len(data) >= DAVE_MIN_LEN and data.endswith(DAVE_FRAME_MAGIC)


def decrypt_dave_payload(session: Any, user_id: int, payload: bytes) -> tuple[bytes, str]:
    """Return (bytes, outcome) with outcome in ok|fail|skip|passthrough."""
    if session is None or not getattr(session, "ready", False):
        return payload, "skip"
    if not payload:
        return payload, "skip"
    if not looks_like_dave_frame(payload):
        return payload, "passthrough"
    try:
        import davey

        decrypted = session.decrypt(int(user_id), davey.MediaType.audio, bytes(payload))
        if not decrypted:
            return payload, "fail"
        plain = bytes(decrypted)
        # If davey did not unwrap the trailer, this is still ciphertext.
        if looks_like_dave_frame(plain):
            return payload, "fail"
        return plain, "ok"
    except Exception as exc:
        _log.debug("DAVE decrypt failed for user %s: %s", user_id, exc)
        return payload, "fail"


def dave_session_of(voice_client: Any) -> Any:
    state = getattr(voice_client, "_connection", None)
    return getattr(state, "dave_session", None)


def dave_status_line(voice_client: Any) -> str:
    state = getattr(voice_client, "_connection", None)
    session = getattr(state, "dave_session", None)
    mode = getattr(voice_client, "mode", "") or getattr(state, "mode", "")
    version = getattr(state, "dave_protocol_version", 0)
    ready = bool(session is not None and getattr(session, "ready", False))
    return (
        f"mode={mode or '?'} dave_version={version} dave_ready={ready} "
        f"has_session={session is not None}"
    )


def enable_dave_passthrough(voice_client: Any) -> None:
    session = dave_session_of(voice_client)
    if session is None:
        return
    try:
        session.set_passthrough_mode(True, 10)
    except Exception:
        _log.debug("Could not enable DAVE passthrough", exc_info=True)


def _other_channel_members(voice_client: Any) -> list[Any]:
    bot_id = getattr(getattr(voice_client, "user", None), "id", None)
    channel = getattr(voice_client, "channel", None)
    members = list(getattr(channel, "members", []) or [])
    others = []
    for member in members:
        member_id = getattr(member, "id", None)
        if member_id and member_id != bot_id:
            others.append(member)
    return others


def resolve_decoder_user_id(decoder: Any) -> Optional[int]:
    """SSRC map first, then the only other person in the voice channel."""
    cached = getattr(decoder, "_cached_id", None)
    if cached:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass

    voice_client = getattr(getattr(decoder, "sink", None), "voice_client", None)
    if voice_client is None:
        return None

    ssrc = getattr(decoder, "ssrc", None)
    getter = getattr(voice_client, "_get_id_from_ssrc", None)
    if callable(getter) and ssrc is not None:
        mapped = getter(ssrc)
        if mapped:
            decoder._cached_id = int(mapped)
            return int(mapped)

    others = _other_channel_members(voice_client)
    if len(others) != 1:
        return None
    user_id = int(others[0].id)
    decoder._cached_id = user_id
    adder = getattr(voice_client, "_add_ssrc", None)
    if callable(adder) and ssrc is not None:
        try:
            adder(user_id, ssrc)
        except Exception:
            _log.debug("Could not cache SSRC mapping", exc_info=True)
    return user_id


def stub_user_from_decoder(decoder: Any) -> Any | None:
    user_id = getattr(decoder, "_cached_id", None) or resolve_decoder_user_id(decoder)
    if not user_id:
        return None
    getter = getattr(decoder, "_get_user", None)
    if callable(getter):
        member = getter(int(user_id))
        if member is not None:
            return member
    return SimpleNamespace(id=int(user_id), name="", global_name="", nick="")


def resolve_audio_user(user: Any, data: Any, *, voice_client: Any, known_user_ids: list[str]) -> Any | None:
    """Map a sink callback to a speaker even when the library passes user=None."""
    if user is not None and getattr(user, "id", None):
        return user

    source = getattr(data, "source", None)
    if source is not None and getattr(source, "id", None):
        return source

    packet = getattr(data, "packet", None)
    ssrc = getattr(packet, "ssrc", None)
    getter = getattr(voice_client, "_get_id_from_ssrc", None) if voice_client is not None else None
    if callable(getter) and ssrc is not None:
        mapped = getter(ssrc)
        if mapped:
            return SimpleNamespace(id=mapped, name="", global_name="", nick="")

    unique_ids = [uid for uid in known_user_ids if uid]
    if len(unique_ids) == 1:
        return SimpleNamespace(id=unique_ids[0], name="", global_name="", nick="")

    if ssrc is not None:
        return SimpleNamespace(id=f"ssrc-{ssrc}", name="", global_name="", nick="")
    return None


def apply_voice_recv_patches() -> None:
    """Idempotent monkeypatch of voice_recv's opus decoder."""
    global _patches_applied
    if _patches_applied:
        return

    from discord.ext.voice_recv.opus import PacketDecoder, VoiceData
    from discord.ext.voice_recv.router import PacketRouter

    if getattr(PacketDecoder._process_packet, _PATCHED_ATTR, False):
        _patches_applied = True
        return

    original_process = PacketDecoder._process_packet
    original_decode = PacketDecoder._decode_packet

    def _decode_packet(self, packet):  # noqa: ANN001
        try:
            return original_decode(self, packet)
        except Exception:
            STATS.opus_errors += 1
            _log.debug("opus decode failed for ssrc %s", getattr(self, "ssrc", None), exc_info=True)
            try:
                from discord.opus import Decoder

                if not self.sink.wants_opus():
                    self._decoder = Decoder()
            except Exception:
                self._decoder = None
            return packet, b""

    def _process_packet(self, packet):  # noqa: ANN001
        STATS.packets += 1
        try:
            payload = getattr(packet, "decrypted_data", None) if packet else None
            if payload:
                if looks_like_dave_frame(payload):
                    user_id = resolve_decoder_user_id(self)
                    session = dave_session_of(getattr(self.sink, "voice_client", None))
                    if user_id is None or session is None or not getattr(session, "ready", False):
                        STATS.dave_skipped += 1
                        return VoiceData(packet, stub_user_from_decoder(self), pcm=b"")
                    decrypted, outcome = decrypt_dave_payload(session, user_id, bytes(payload))
                    if outcome != "ok":
                        STATS.dave_fail += 1
                        # Ciphertext still looks like Opus (TOC is left unencrypted).
                        # Decoding it produces noise, so drop the frame.
                        return VoiceData(packet, stub_user_from_decoder(self), pcm=b"")
                    packet.decrypted_data = decrypted
                    STATS.dave_ok += 1
                else:
                    STATS.passthrough += 1

            data = original_process(self, packet)
            if data is None:
                return None
            if getattr(data, "source", None) is None:
                data.source = stub_user_from_decoder(self)
            pcm = getattr(data, "pcm", None) or b""
            if pcm:
                STATS.pcm_frames += 1
                STATS.pcm_bytes += len(pcm)
            return data
        except Exception:
            STATS.opus_errors += 1
            _log.debug("voice packet processing failed", exc_info=True)
            return VoiceData(packet, stub_user_from_decoder(self), pcm=b"")

    def _do_run(self) -> None:  # noqa: ANN001
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in self.waiter.items:
                    try:
                        data = decoder.pop_data()
                        if data is not None:
                            self.sink.write(data.source, data)
                    except Exception:
                        STATS.opus_errors += 1
                        _log.debug("voice sink write failed", exc_info=True)

    setattr(_process_packet, _PATCHED_ATTR, True)
    setattr(_decode_packet, _PATCHED_ATTR, True)
    setattr(_do_run, _PATCHED_ATTR, True)
    PacketDecoder._process_packet = _process_packet  # type: ignore[method-assign]
    PacketDecoder._decode_packet = _decode_packet  # type: ignore[method-assign]
    PacketRouter._do_run = _do_run  # type: ignore[method-assign]
    _patches_applied = True
