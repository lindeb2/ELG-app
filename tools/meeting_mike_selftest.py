"""Offline checks for the Meeting Mike pipeline.

Exercises audio alignment, mixing, speaker attribution and the summary
template with a stub model, so the parts that do not need Discord, Whisper or
llama.cpp can be verified on any machine.

Usage: python tools/meeting_mike_selftest.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from meeting_host import config, merge, pipeline, summarize
from meeting_host.audio_mix import (
    AlignedWavWriter,
    decode_discord_pcm,
    mix_wavs,
    read_wav_mono,
    wav_duration_seconds,
)
from meeting_host.facts import MeetingFacts, UserGoalFact, classify_goal
from meeting_host.identity import DiscordSpeaker, IdentityResolver
from meeting_host.llama_server import LlmUnavailable, _parse_json_object
from meeting_host.protocol import EventWriter, decode_command

_failures: list[str] = []
_checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(' - ' + detail) if detail else ''}")
        _failures.append(label)


def _tone(seconds: float, *, rate: int = config.DISCORD_SAMPLE_RATE, amplitude: int = 8000) -> bytes:
    """48 kHz stereo s16le test signal."""
    count = int(seconds * rate)
    t = np.arange(count, dtype=np.float64) / rate
    mono = (np.sin(2 * np.pi * 440 * t) * amplitude).astype(np.int16)
    stereo = np.repeat(mono, config.DISCORD_CHANNELS)
    return stereo.tobytes()


def test_decode() -> None:
    print("decode_discord_pcm")
    pcm = _tone(1.0)
    samples = decode_discord_pcm(pcm)
    check("1s of 48k stereo becomes 16k mono", samples.size == config.SAMPLE_RATE, f"got {samples.size}")
    check("output is int16", samples.dtype == np.int16, str(samples.dtype))
    check("empty input is safe", decode_discord_pcm(b"").size == 0)
    check("odd-length input is safe", decode_discord_pcm(b"\x01\x02\x03").size == 0)


def test_alignment(tmp: Path) -> None:
    print("AlignedWavWriter")
    path = tmp / "aligned.wav"
    writer = AlignedWavWriter(path)
    chunk = decode_discord_pcm(_tone(0.5))

    # Speaker starts 2s in, so silence must be padded ahead of the audio.
    writer.write(chunk, offset_samples=2 * config.SAMPLE_RATE)
    writer.close()

    samples = read_wav_mono(path)
    expected = 2 * config.SAMPLE_RATE + chunk.size
    check("padded to the shared timeline", samples.size == expected, f"got {samples.size}")
    check("leading region is silent", bool(np.all(samples[: config.SAMPLE_RATE] == 0)))
    check("audio lands after the pad", bool(np.any(samples[2 * config.SAMPLE_RATE :] != 0)))

    # A gap under one frame must not shift the stream.
    path2 = tmp / "tolerance.wav"
    writer2 = AlignedWavWriter(path2)
    writer2.write(chunk, offset_samples=0)
    writer2.write(chunk, offset_samples=chunk.size + 10)
    writer2.close()
    check(
        "sub-frame drift is ignored",
        read_wav_mono(path2).size == chunk.size * 2,
        f"got {read_wav_mono(path2).size}",
    )


def test_mix(tmp: Path) -> None:
    print("mix_wavs")
    users = tmp / "users"
    users.mkdir(exist_ok=True)

    early = AlignedWavWriter(users / "111.wav")
    early.write(decode_discord_pcm(_tone(1.0)), offset_samples=0)
    early.close()

    late = AlignedWavWriter(users / "222.wav")
    late.write(decode_discord_pcm(_tone(1.0)), offset_samples=3 * config.SAMPLE_RATE)
    late.close()

    destination = tmp / "mix.wav"
    duration = mix_wavs(sorted(users.glob("*.wav")), destination)
    check("mix spans the longest stream", abs(duration - 4.0) < 0.05, f"{duration:.2f}s")
    check("mix duration on disk matches", abs(wav_duration_seconds(destination) - 4.0) < 0.05)

    mixed = read_wav_mono(destination)
    check("no clipping overflow", bool(np.all(np.abs(mixed) <= 32767)))
    check("late speaker present in mix", bool(np.any(mixed[3 * config.SAMPLE_RATE :] != 0)))
    check("empty source list is safe", mix_wavs([], tmp / "empty.wav") == 0.0)


def test_identity() -> None:
    print("IdentityResolver")
    resolver = IdentityResolver(
        {
            "Johan": {"discord_user_id": "1001", "discordname": "johan_elg"},
            "Ada": {"discordname": "ada#0001"},
        }
    )
    check(
        "discord id wins",
        resolver.resolve(DiscordSpeaker(discord_user_id="1001", username="someone_else")) == "Johan",
    )
    check(
        "falls back to stored discordname",
        resolver.resolve(DiscordSpeaker(discord_user_id="2002", global_name="ADA#0001")) == "Ada",
    )
    check(
        "falls back to elg username match",
        resolver.resolve(DiscordSpeaker(discord_user_id="3003", username="johan")) == "Johan",
    )
    unknown = DiscordSpeaker(discord_user_id="4004", nick="Guest")
    check("unknown stays unmapped", resolver.resolve(unknown) is None)
    check("unknown labels by display name", resolver.label(unknown) == "Guest")


def _mix_transcript() -> dict:
    return {
        "language": "en",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "Welcome to the status meeting.", "words": []},
            {"start": 2.5, "end": 4.0, "text": "I hit my hours this week.", "words": []},
            {"start": 4.5, "end": 6.0, "text": "aaaa aaaa aaaa", "words": []},
        ],
    }


def _user_transcripts() -> dict[str, dict]:
    return {
        "111": {
            "segments": [
                {"start": 0.1, "end": 1.9, "text": "Welcome to the status meeting", "words": []},
            ]
        },
        "222": {
            "segments": [
                {"start": 2.6, "end": 3.9, "text": "I hit my hours this week", "words": []},
                {"start": 4.6, "end": 5.9, "text": "We should ship the demo Friday", "words": []},
            ]
        },
    }


def test_merge_baseline(tmp: Path) -> None:
    print("merge (deterministic baseline)")
    meeting_dir = tmp / "meeting-baseline"
    (meeting_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    labels = {"111": "Johan", "222": "Ada"}

    payload = merge.merge_transcripts(
        meeting_dir, _mix_transcript(), _user_transcripts(), labels, llm=None
    )
    segments = payload["segments"]

    check("attribution method recorded", payload["speaker_attribution"] == "timestamp_overlap")
    check("every mix line kept", len(segments) == 3, str(len(segments)))
    check("first line attributed by overlap", segments[0]["elg_username"] == "Johan")
    check("second line attributed by overlap", segments[1]["elg_username"] == "Ada")
    check("mix wording preserved", segments[0]["text"] == "Welcome to the status meeting.")
    check("source marked as mix", all(seg["source"] == "mix" for seg in segments))
    check("diarized.json written", (meeting_dir / "transcripts" / "diarized.json").is_file())

    rendered = merge.transcript_as_text(payload)
    check("renders timestamped speaker lines", "[00:02] Ada: I hit my hours this week." in rendered, rendered)


class _StubLlm:
    """Stands in for llama-server: relabels a line and splices a fallback."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def complete_json(self, *, system, user, schema, max_tokens=4096) -> dict:
        self.calls += 1
        if self.fail:
            raise LlmUnavailable("stub failure")
        payload = json.loads(user)
        lines = []
        for line in payload["lines"]:
            if line["combined_text"].startswith("aaaa"):
                lines.append(
                    {
                        "id": line["id"],
                        "speaker_id": "222",
                        "text": "We should ship the demo Friday",
                        "source": "user_fallback",
                    }
                )
            else:
                lines.append(
                    {
                        "id": line["id"],
                        "speaker_id": line["candidates"][0]["speaker_id"],
                        "text": line["combined_text"],
                        "source": "mix",
                    }
                )
        return {"lines": lines}

    def complete(self, **_kwargs) -> str:
        raise LlmUnavailable("not used")


def test_merge_llm(tmp: Path) -> None:
    print("merge (model-assisted)")
    meeting_dir = tmp / "meeting-llm"
    (meeting_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    labels = {"111": "Johan", "222": "Ada"}

    stub = _StubLlm()
    payload = merge.merge_transcripts(
        meeting_dir, _mix_transcript(), _user_transcripts(), labels, llm=stub
    )
    segments = payload["segments"]

    check("model was consulted", stub.calls == 1, f"{stub.calls} calls")
    check("attribution method recorded", payload["speaker_attribution"] == "llm")
    check("garbled line replaced", segments[2]["text"] == "We should ship the demo Friday")
    check("fallback source recorded", segments[2]["source"] == "user_fallback")
    check("clean lines stay mix-sourced", segments[0]["source"] == "mix")
    check("clean wording untouched", segments[1]["text"] == "I hit my hours this week.")

    failing = _StubLlm(fail=True)
    fallback_dir = tmp / "meeting-llm-fail"
    (fallback_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    payload = merge.merge_transcripts(
        fallback_dir, _mix_transcript(), _user_transcripts(), labels, llm=failing
    )
    check("model failure degrades to baseline", payload["speaker_attribution"] == "timestamp_overlap")
    check("baseline still complete", len(payload["segments"]) == 3)

    bad_ids = {"lines": [{"id": 99, "speaker_id": "nope", "text": "x", "source": "mix"}]}
    lines = merge.baseline_merge(_mix_transcript(), _user_transcripts(), labels)
    applied = merge._apply_response(bad_ids, lines, [0, 1, 2], labels)
    check("invalid model ids rejected", applied == 0, str(applied))


def test_energy_diarization(tmp: Path) -> None:
    print("merge (microphone energy)")
    from meeting_host.audio_mix import write_wav_mono

    meeting_dir = tmp / "meeting-energy"
    users = meeting_dir / "audio" / "users"
    users.mkdir(parents=True)
    total = config.SAMPLE_RATE * 4
    johan = np.zeros(total, dtype=np.int16)
    ada = np.zeros(total, dtype=np.int16)
    johan[int(0.2 * config.SAMPLE_RATE) : int(1.0 * config.SAMPLE_RATE)] = 8000
    ada[int(2.0 * config.SAMPLE_RATE) : int(3.0 * config.SAMPLE_RATE)] = 8000
    johan[int(3.2 * config.SAMPLE_RATE) : int(3.6 * config.SAMPLE_RATE)] = 9000
    ada[int(3.2 * config.SAMPLE_RATE) : int(3.6 * config.SAMPLE_RATE)] = 2000
    write_wav_mono(users / "111.wav", johan)
    write_wav_mono(users / "222.wav", ada)

    mix = {
        "segments": [
            {
                "start": 0.2,
                "end": 3.6,
                "text": "hello there hours both",
                "words": [
                    {"start": 0.3, "end": 0.8, "word": "hello"},
                    {"start": 1.2, "end": 1.2, "word": "there"},
                    {"start": 2.1, "end": 2.8, "word": "hours"},
                    {"start": 3.2, "end": 3.5, "word": "both"},
                ],
            }
        ]
    }
    stub = _StubLlm()
    payload = merge.merge_transcripts(
        meeting_dir, mix, _user_transcripts(), {"111": "Johan", "222": "Ada"}, llm=stub
    )
    segments = payload["segments"]
    check("energy attribution recorded", payload["speaker_attribution"] == "energy", payload["speaker_attribution"])
    check("model is not asked to relabel mics", stub.calls == 0, str(stub.calls))
    check("turn split into three lines", len(segments) == 3, str(segments))
    check("opening is Johan", segments[0]["elg_username"] == "Johan" and "hello" in segments[0]["text"])
    check("silent word stays with the previous speaker", "there" in segments[0]["text"], segments[0]["text"])
    check("middle turn is Ada", segments[1]["elg_username"] == "Ada" and segments[1]["text"] == "hours")
    check("overlap goes to the louder mic", segments[2]["elg_username"] == "Johan" and segments[2]["text"] == "both")


def test_goal_rules() -> None:
    print("goal classification")
    check("no goal", classify_goal(None, None, 12.0, 4) == "none")
    check("hours reached", classify_goal(10, None, 10.0, 1) == "reached")
    check("hours missed", classify_goal(10, None, 9.99, 7) == "missed")
    check("days reached", classify_goal(None, 3, 0.5, 3) == "reached")
    check("both needed, one short", classify_goal(10, 3, 12.0, 2) == "missed")
    check("both reached", classify_goal(10, 3, 10.0, 3) == "reached")
    check("zero goal treated as unset", classify_goal(0, 0, 0.0, 0) == "none")


def _facts() -> MeetingFacts:
    return MeetingFacts(
        iso_year="2026",
        iso_week="38",
        next_iso_year="2026",
        next_iso_week="39",
        goals=[
            UserGoalFact("Johan", 10.0, 3, 12.5, 4, "reached"),
            UserGoalFact("Ada", 8.0, None, 4.0, 2, "missed"),
        ],
        next_goals={"Johan": {"hours": 12, "days": 4}, "Ada": {}},
        discussion_points=[
            {"title": "Demo scope", "description": "What ships Friday"},
            {"title": "Build times", "description": ""},
        ],
        logs=[{"user": "Johan", "name": "Level art", "seconds": 7200, "date": "Mon 15 Sep"}],
    )


def _attendance() -> dict:
    return {
        "started_at": "2026-09-17T17:00:00+00:00",
        "ended_at": "2026-09-17T17:12:00+00:00",
        "channel_name": "boardroom",
        "participants": [
            {"discord_user_id": "111", "elg_username": "Johan", "joined_late": False},
            {"discord_user_id": "222", "elg_username": "Ada", "joined_late": True},
            {"discord_user_id": "333", "discord_username": "guest", "joined_late": True},
        ],
    }


class _SummaryStub:
    def complete_json(self, *, system, user, schema, max_tokens=4096) -> dict:
        return {
            "discussion": "Johan cleared his goal; Ada lost time to a build issue.",
            "points": [
                {"index": 0, "notes": "Agreed to cut the tutorial from the demo."},
                {"index": 1, "notes": "Not discussed."},
            ],
            "actions": ["Johan cuts tutorial scope", "Ada fixes the build"],
        }


def test_summary() -> None:
    print("summary template")
    content = summarize.build_summary(_facts(), _attendance(), "[00:00] Johan: hi", llm=_SummaryStub())

    for heading in (
        "# Weekly Status Meeting — Week 38, 2026",
        "## Attendance",
        "## This week's goals",
        "## Logs",
        "## Discussion points",
        "## Next week's goals",
        "## What was said",
        "## Decisions and action items",
    ):
        check(f"section present: {heading}", heading in content)

    goals_section = content.split("## Logs")[0]
    said_section = content.split("## What was said", 1)[1]
    check("goal section is data only", "cleared his goal" not in goals_section)
    check("discussion placed under What was said", "cleared his goal" in said_section)

    check("late join flagged", "Ada (joined late)" in content)
    check("unlinked account flagged", "(unlinked Discord account)" in content)
    check("reached goal stated as fact", "12.5h of 10h, 4/3 days — reached" in content)
    check("missed goal stated as fact", "**Ada**: 4h of 8h" in content and "missed" in content)
    check("next-week goal rendered", "**Johan**: 12h, 4 days" in content)
    check("unset next-week goal rendered", "**Ada**: not set" in content)
    check("discussion point heading kept", "### Demo scope" in content)
    check("model notes inserted", "cut the tutorial" in content)
    check("undiscussed point marked", "Not discussed." in content)
    check("actions listed", "- Johan cuts tutorial scope" in content)

    degraded = summarize.build_summary(_facts(), _attendance(), "", llm=None)
    check("summary still produced without a model", "# Weekly Status Meeting" in degraded)
    check("facts survive without a model", "12.5h of 10h" in degraded)
    check("missing narrative is explicit", "Local model unavailable" in degraded)


def test_protocol_and_pipeline(tmp: Path) -> None:
    print("protocol and pipeline helpers")
    check("valid command decodes", decode_command('{"cmd":"stop"}') == {"cmd": "stop"})
    check("noise is ignored", decode_command("llama_server: loading model") is None)
    check("blank line is ignored", decode_command("   ") is None)
    check("non-command json is ignored", decode_command('{"hello":1}') is None)

    class _Sink:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def write(self, text: str) -> None:
            self.lines.append(text)

        def flush(self) -> None:
            pass

    sink = _Sink()
    writer = EventWriter(sink)
    writer.emit("joined", channel_name="boardroom")
    writer.phase("merge", 0.5)
    payloads = [json.loads(line) for line in sink.lines if line.strip()]
    check("events serialize as jsonl", payloads[0] == {"event": "joined", "channel_name": "boardroom"})
    check("phase carries progress", payloads[1]["progress"] == 0.5)

    check(
        "labels derive from attendance",
        pipeline.speaker_labels_from_attendance(_attendance())
        == {"111": "Johan", "222": "Ada", "333": "guest"},
    )

    status_dir = tmp / "status-meeting"
    status_dir.mkdir(parents=True, exist_ok=True)
    pipeline.write_status(status_dir, pipeline="pending")
    pipeline.write_status(status_dir, pipeline="done", segments=3)
    saved = json.loads((status_dir / "status.json").read_text(encoding="utf-8"))
    check("status merges across writes", saved == {"pipeline": "done", "segments": 3}, str(saved))

    check("fenced json recovers", _parse_json_object('```json\n{"a":1}\n```') == {"a": 1})
    check("prose-wrapped json recovers", _parse_json_object('Sure! {"a": 2} done') == {"a": 2})
    check("garbage json rejected", _parse_json_object("no json here") is None)

    print("voice channel matching")
    from meeting_host.channel_match import is_fallback_voice_channel, normalize_channel_name
    from meeting_host.models import choose_llm_candidate

    check("boardroom capital B matches", is_fallback_voice_channel("\U0001F935Boardroom"))
    check(
        "variation selector matches",
        is_fallback_voice_channel("\U0001F935\uFE0Fboardroom"),
    )
    check("unrelated name rejected", not is_fallback_voice_channel("Enter Current Room"))
    check("empty name rejected", not is_fallback_voice_channel(""))
    check("normalize is case-insensitive", normalize_channel_name("Board Room") == "board room")
    check("8B fits on a large disk", choose_llm_candidate(8_000_000_000)["filename"] == config.LLM_FILENAME)
    check("8B refused when disk is tight", choose_llm_candidate(3_400_000_000) is None)
    check("no candidate when disk is empty", choose_llm_candidate(10_000_000) is None)

    leftover = tmp / "llm-cleanup"
    leftover.mkdir(parents=True, exist_ok=True)
    from meeting_host.models import llm_dir, remove_noncanonical_llms

    llm_folder = llm_dir(leftover)
    (llm_folder / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"old")
    (llm_folder / "Qwen3-1.7B-Q8_0.gguf").write_bytes(b"old")
    (llm_folder / config.LLM_FILENAME).write_bytes(b"keep")
    removed = {path.name for path in remove_noncanonical_llms(leftover)}
    check("legacy 4B GGUF is deleted", "Qwen3-4B-Q4_K_M.gguf" in removed)
    check("legacy 1.7B GGUF is deleted", "Qwen3-1.7B-Q8_0.gguf" in removed)
    check("8B GGUF is kept", (llm_folder / config.LLM_FILENAME).is_file())

    from meeting_host.channel_match import format_permission_names, missing_connect_permissions

    check(
        "both voice perms reported",
        missing_connect_permissions(view_channel=False, connect=False)
        == ["View Channel", "Connect"],
    )
    check("connect-only missing", missing_connect_permissions(view_channel=True, connect=False) == ["Connect"])
    check("no missing perms when granted", missing_connect_permissions(view_channel=True, connect=True) == [])
    check("permission names joined", format_permission_names(["View Channel", "Connect"]) == "View Channel and Connect")

    from meeting_host.bot_invite import ADMINISTRATOR, application_id_from_token

    fake_id = "123456789012345678"
    import base64

    fake_token = base64.b64encode(fake_id.encode("ascii")).decode("ascii").rstrip("=") + ".sig.sig"
    check("bot id decoded from token prefix", application_id_from_token(fake_token) == fake_id)
    check("administrator bit is 8", ADMINISTRATOR == 8)

    print("DAVE inbound decrypt")
    from meeting_host import dave_recv

    dave_recv.reset_stats()
    check("empty payload is not a DAVE frame", not dave_recv.looks_like_dave_frame(b""))
    check("FAFA prefix only is not a DAVE frame", not dave_recv.looks_like_dave_frame(b"\xfa\xfa\x00\x01"))
    check("opus silence is not a DAVE frame", not dave_recv.looks_like_dave_frame(b"\xf8\xff\xfe"))
    dave_frame = b"\x78" + (b"\x00" * 20) + b"\xfa\xfa"
    check("FAFA suffix is a DAVE frame", dave_recv.looks_like_dave_frame(dave_frame))

    class _NotReady:
        ready = False

        def decrypt(self, *_args, **_kwargs):
            raise AssertionError("should not decrypt when not ready")

    unchanged, outcome = dave_recv.decrypt_dave_payload(_NotReady(), 1, dave_frame)
    check("skip when session not ready", outcome == "skip" and unchanged == dave_frame)

    class _Ready:
        ready = True
        calls: list = []

        def decrypt(self, user_id, media, data):
            self.calls.append((user_id, data))
            return b"\xf8\xff\xfe"

    ready = _Ready()
    decrypted, outcome = dave_recv.decrypt_dave_payload(ready, 42, dave_frame)
    check("decrypt when ready and magic present", outcome == "ok" and decrypted == b"\xf8\xff\xfe")
    check("decrypt used the speaker id", ready.calls and ready.calls[0][0] == 42)
    check("decrypt received the full DAVE frame", ready.calls and ready.calls[0][1] == dave_frame)

    passthrough, outcome = dave_recv.decrypt_dave_payload(ready, 42, b"\xf8\xff\xfe")
    check("plaintext opus is passthrough", outcome == "passthrough" and passthrough == b"\xf8\xff\xfe")

    class _Broken:
        ready = True

        def decrypt(self, *_args, **_kwargs):
            raise ValueError("no cryptor")

    failed, outcome = dave_recv.decrypt_dave_payload(_Broken(), 1, dave_frame)
    check("decrypt failure keeps original bytes", outcome == "fail" and failed == dave_frame)

    class _NoUnwrap:
        ready = True

        def decrypt(self, *_args, **_kwargs):
            return dave_frame

    still_wrapped, outcome = dave_recv.decrypt_dave_payload(_NoUnwrap(), 1, dave_frame)
    check("still-wrapped decrypt is failure", outcome == "fail" and still_wrapped == dave_frame)

    class _Sink:
        def __init__(self, voice_client):
            self.voice_client = voice_client

    class _Voice:
        def __init__(self):
            self.user = SimpleNamespace(id=99)
            self.channel = SimpleNamespace(members=[SimpleNamespace(id=99), SimpleNamespace(id=7)])
            self._ssrc = {}

        def _get_id_from_ssrc(self, ssrc):
            return self._ssrc.get(ssrc)

        def _add_ssrc(self, user_id, ssrc):
            self._ssrc[ssrc] = user_id

    voice = _Voice()
    decoder = SimpleNamespace(_cached_id=None, ssrc=1234, sink=_Sink(voice))
    check("sole other member fills missing SSRC", dave_recv.resolve_decoder_user_id(decoder) == 7)
    check("sole other member is cached on the decoder", decoder._cached_id == 7)
    check("SSRC map updated from fallback", voice._ssrc.get(1234) == 7)

    mapped = SimpleNamespace(_cached_id=None, ssrc=55, sink=_Sink(voice))
    voice._ssrc[55] = 88
    check("SSRC map wins over channel fallback", dave_recv.resolve_decoder_user_id(mapped) == 88)

    packet = SimpleNamespace(ssrc=55)
    data = SimpleNamespace(source=None, packet=packet, pcm=b"\x00\x01")
    speaker = dave_recv.resolve_audio_user(None, data, voice_client=voice, known_user_ids=["7"])
    check("audio user resolved from SSRC", getattr(speaker, "id", None) == 88)
    solo = dave_recv.resolve_audio_user(
        None, SimpleNamespace(source=None, packet=SimpleNamespace(ssrc=1)), voice_client=None, known_user_ids=["358269533779132426"]
    )
    check("audio user falls back to sole attendee", str(getattr(solo, "id", None)) == "358269533779132426")

    dave_recv.apply_voice_recv_patches()
    dave_recv.apply_voice_recv_patches()
    check("voice recv patches are idempotent", True)


def test_meeting_paths(tmp: Path) -> None:
    print("meeting directory layout")
    from datetime import datetime, timedelta, timezone

    base = tmp / "paths"
    started = datetime(2026, 9, 17, 17, 4, 5, tzinfo=timezone.utc)
    path = config.meeting_dir(base, started)
    check("week folder in path", "2026-W38" in str(path), str(path))
    check("timestamped meeting folder", path.name == "20260917T170405Z", path.name)
    check("audio/users created", (path / "audio" / "users").is_dir())
    check("transcripts/users created", (path / "transcripts" / "users").is_dir())

    print("resume discovery")
    check("unfinished meeting is found", pipeline.latest_pending(base) == path, str(pipeline.latest_pending(base)))
    pipeline.write_status(path, pipeline="done")
    check("finished meeting is skipped", pipeline.latest_pending(base) is None)

    newer = config.meeting_dir(base, started + timedelta(hours=1))
    pipeline.write_status(newer, pipeline="pending")
    check("newest unfinished meeting wins", pipeline.latest_pending(base) == newer, str(pipeline.latest_pending(base)))
    check("empty base is safe", pipeline.latest_pending(tmp / "nothing-here") is None)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mike-selftest-") as raw_tmp:
        tmp = Path(raw_tmp)
        test_decode()
        test_alignment(tmp)
        test_mix(tmp)
        test_identity()
        test_merge_baseline(tmp)
        test_merge_llm(tmp)
        test_energy_diarization(tmp)
        test_goal_rules()
        test_summary()
        test_protocol_and_pipeline(tmp)
        test_meeting_paths(tmp)

    print()
    if _failures:
        print(f"FAILED {len(_failures)}/{_checks}:")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print(f"All {_checks} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
