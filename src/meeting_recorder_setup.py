"""One-time setup for the Meeting Recorder feature (see step-4-packaging.md).

Runs when the user turns Settings -> "Meeting Recorder" on: validates and
stores the user's own Discord bot token, confirms the heavy optional
dependencies (py-cord, faster-whisper, llama-cpp-python) actually import in
this build, and downloads the model files those dependencies need - all so
a fresh install never does any of this until the user explicitly asks for
it (see Definition of Done in step-4-packaging.md).

Model files are hosted as assets on a dedicated GitHub Release
(MODELS_RELEASE_TAG below) in the same repo app_update.py's auto-updater
already points at, kept separate from the version-tagged app releases so
shipping a new app version never re-uploads multi-GB assets. Whoever
manages releases needs to publish that release with the exact asset names
below before a user can complete setup; if an asset is missing,
download_models() fails with an actionable message rather than hanging.

Threading model matches meeting_transcriber.py/meeting_summarizer.py:
run_setup() is a plain blocking function; SetupJob runs it on its own
daemon thread, and callers marshal on_status/on_error/on_done back to the
Tk main loop themselves via self.after(0, ...) - the same pattern
settings_frame.py already uses for update checks.
"""
from __future__ import annotations

import sys
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from app_secrets import get_discord_guild_id, has_discord_bot_token_configured
from app_update import GITHUB_HEADERS, GITHUB_REPO, download_with_checksum
from meeting_combined_transcriber import DEFAULT_MODEL_SIZE as HIGH_QUALITY_MODEL_SIZE
from meeting_recorder_paths import faster_whisper_model_dir, ffmpeg_binary_path, get_models_dir
from meeting_recorder_secrets import set_local_discord_bot_token
from meeting_summarizer import EMBEDDED_MODEL_FILENAME_CANDIDATES
from meeting_transcriber import DEFAULT_MODEL_SIZE

DISCORD_API_BASE = "https://discord.com/api/v10"
_TOKEN_VALIDATE_TIMEOUT_SECONDS = 10.0

# See module docstring - a release separate from the app's own version tags.
MODELS_RELEASE_TAG = "models-v1"
FASTER_WHISPER_ASSET_NAME = f"faster-whisper-{DEFAULT_MODEL_SIZE}-ct2.zip"
LLM_ASSET_NAME = EMBEDDED_MODEL_FILENAME_CANDIDATES[0]
_MODEL_ASSETS_CHECKSUMS_NAME = "checksums.txt"

# Step 5 (High-quality mode) assets - downloaded separately from the base setup
# above, only when the user turns on Settings -> Meeting Recorder -> "High-quality
# mode" (see settings_frame.py), since this quality upgrade is opt-in and its
# assets (a stronger whisper model + a static ffmpeg binary) are heavier than the
# base feature's. Same MODELS_RELEASE_TAG release as the base assets.
FASTER_WHISPER_HIGH_QUALITY_ASSET_NAME = f"faster-whisper-{HIGH_QUALITY_MODEL_SIZE}-ct2.zip"
# Windows-first per is_supported_os() below - macOS/Linux ffmpeg assets can be
# added to the same release later without changing this module's shape.
FFMPEG_ASSET_NAME = "ffmpeg-win64.zip"


class SetupError(Exception):
    """Raised with an actionable, user-facing message for any setup failure."""


@dataclass(frozen=True)
class SetupResult:
    dependencies_ok: bool
    token_ok: bool
    models_ok: bool


def _notify(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception:
        pass


def dependencies_importable() -> tuple[bool, str | None]:
    """True if the optional heavy dependencies actually import in this build.

    Note: meeting.py already imports py-cord and faster-whisper at module
    level (meeting_recorder.py / meeting_transcriber.py), so on a packaged
    build that failed to bundle their native extensions correctly the app
    would already have failed to start - this check exists mainly as an
    explicit, user-visible confirmation (and a safety net for llama-cpp-
    python, which meeting_summarizer.py only imports lazily on first use).
    """
    try:
        import discord  # noqa: F401
        import faster_whisper  # noqa: F401
        import llama_cpp  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, None


def is_supported_os() -> bool:
    """Windows-first per step-4-packaging.md - macOS/Linux stay gated behind
    this until the Windows path is confirmed working end-to-end."""
    return sys.platform.startswith("win")


def _models_release_asset_url(asset_name: str) -> tuple[str, str | None]:
    """(asset_download_url, checksums_url) for asset_name in
    MODELS_RELEASE_TAG, resolved the same way app_update.py resolves
    version-release assets."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{MODELS_RELEASE_TAG}"
    response = requests.get(url, headers=GITHUB_HEADERS, timeout=30)
    if response.status_code == 404:
        raise SetupError(
            f"No '{MODELS_RELEASE_TAG}' release found on {GITHUB_REPO} - the model files "
            "haven't been published yet. Try again later or contact whoever manages releases."
        )
    response.raise_for_status()
    payload = response.json()
    assets = {a["name"]: a["browser_download_url"] for a in payload.get("assets", []) if a.get("name")}
    if asset_name not in assets:
        raise SetupError(f"The '{MODELS_RELEASE_TAG}' release doesn't have an asset named '{asset_name}'.")
    checksums_url = assets.get(_MODEL_ASSETS_CHECKSUMS_NAME)
    return assets[asset_name], checksums_url


def models_ready() -> bool:
    whisper_dir = faster_whisper_model_dir(DEFAULT_MODEL_SIZE)
    whisper_ready = whisper_dir.is_dir() and any(whisper_dir.iterdir())
    llm_ready = any((get_models_dir() / name).is_file() for name in EMBEDDED_MODEL_FILENAME_CANDIDATES)
    return whisper_ready and llm_ready


def _download_and_extract_zip(asset_name, dest_dir, *, on_progress) -> None:
    _notify(on_progress, f"Downloading {asset_name}…")
    asset_url, checksums_url = _models_release_asset_url(asset_name)
    download_dir = get_models_dir() / "_downloads"
    zip_path = download_with_checksum(asset_url, download_dir, asset_name, checksums_url)
    _notify(on_progress, f"Extracting {asset_name}…")
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest_dir)
    zip_path.unlink(missing_ok=True)


def _download_llm_asset(*, on_progress) -> None:
    _notify(on_progress, f"Downloading {LLM_ASSET_NAME}…")
    asset_url, checksums_url = _models_release_asset_url(LLM_ASSET_NAME)
    download_with_checksum(asset_url, get_models_dir(), LLM_ASSET_NAME, checksums_url)


def download_models(*, on_progress: Callable[[str], None] | None = None) -> None:
    """Idempotent - skips whichever piece is already present, so re-running
    setup after a partial failure only fetches what's missing."""
    whisper_dir = faster_whisper_model_dir(DEFAULT_MODEL_SIZE)
    if not (whisper_dir.is_dir() and any(whisper_dir.iterdir())):
        _download_and_extract_zip(FASTER_WHISPER_ASSET_NAME, whisper_dir, on_progress=on_progress)

    if not any((get_models_dir() / name).is_file() for name in EMBEDDED_MODEL_FILENAME_CANDIDATES):
        _download_llm_asset(on_progress=on_progress)


def high_quality_assets_ready() -> bool:
    """True once Step 5's extra assets (the stronger whisper model + ffmpeg) are
    both present - checked by meeting.py before starting a combined-audio pass
    (see meeting_combined_transcriber.py), the same way models_ready() is checked
    before the base transcription/summarization pipeline."""
    whisper_dir = faster_whisper_model_dir(HIGH_QUALITY_MODEL_SIZE)
    whisper_ready = whisper_dir.is_dir() and any(whisper_dir.iterdir())
    return whisper_ready and ffmpeg_binary_path().is_file()


def best_quality_ready() -> bool:
    """True once Step 6's reconciliation pass (see meeting_reconciler.py) has
    everything it needs to run for a meeting. Step 6 downloads nothing new of
    its own - it reuses Step 3's summarization LLM and Step 5's combined.json
    - so this is just an alias for high_quality_assets_ready(): reconciliation
    needs a completed combined-audio pass (Step 5) to have a second transcript
    to reconcile against in the first place. Kept as its own function (rather
    than callers checking high_quality_assets_ready() directly) so meeting.py
    doesn't need to know *why* the two gates happen to be the same today, and
    so this can later diverge (e.g. if Step 6 ever needs its own asset) without
    changing meeting.py's call site."""
    return high_quality_assets_ready()


def download_high_quality_assets(*, on_progress: Callable[[str], None] | None = None) -> None:
    """Idempotent, mirrors download_models() above - only fetches what's missing.
    Only called when the user turns on Settings -> Meeting Recorder -> "High-
    quality mode" (see settings_frame.py), not as part of the base Meeting
    Recorder setup, since this quality upgrade (Step 5) is opt-in and its assets
    are heavier than the base feature's."""
    whisper_dir = faster_whisper_model_dir(HIGH_QUALITY_MODEL_SIZE)
    if not (whisper_dir.is_dir() and any(whisper_dir.iterdir())):
        _download_and_extract_zip(FASTER_WHISPER_HIGH_QUALITY_ASSET_NAME, whisper_dir, on_progress=on_progress)

    ffmpeg_path = ffmpeg_binary_path()
    if not ffmpeg_path.is_file():
        _notify(on_progress, f"Downloading {FFMPEG_ASSET_NAME}…")
        asset_url, checksums_url = _models_release_asset_url(FFMPEG_ASSET_NAME)
        download_dir = get_models_dir() / "_downloads"
        zip_path = download_with_checksum(asset_url, download_dir, FFMPEG_ASSET_NAME, checksums_url)
        _notify(on_progress, f"Extracting {FFMPEG_ASSET_NAME}…")
        ffmpeg_dir = ffmpeg_path.parent
        ffmpeg_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(ffmpeg_dir)
        zip_path.unlink(missing_ok=True)
        if not ffmpeg_path.is_file():
            raise SetupError(
                f"Extracted {FFMPEG_ASSET_NAME} but didn't find {ffmpeg_path.name} inside it."
            )


def validate_and_store_token(token: str) -> None:
    """Raises SetupError with an actionable message if the token doesn't work."""
    token = token.strip()
    if not token:
        raise SetupError("Enter a Discord bot token.")
    try:
        response = requests.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=_TOKEN_VALIDATE_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        raise SetupError(f"Couldn't reach Discord to validate the token: {exc}") from exc
    if response.status_code == 401:
        raise SetupError("Discord rejected that bot token — double-check it and try again.")
    response.raise_for_status()
    try:
        get_discord_guild_id()
    except RuntimeError as exc:
        raise SetupError(str(exc)) from exc
    set_local_discord_bot_token(token)


def run_setup(discord_bot_token: str | None, *, on_progress: Callable[[str], None] | None = None) -> SetupResult:
    """Blocking - see module docstring for the threading model."""
    if not is_supported_os():
        raise SetupError("Meeting Recorder isn't supported on this OS yet.")

    ok, import_error = dependencies_importable()
    if not ok:
        raise SetupError(
            "Meeting Recorder's dependencies aren't available in this build "
            f"({import_error}). This install may need to be updated."
        )

    if not has_discord_bot_token_configured():
        if not discord_bot_token:
            raise SetupError("A Discord bot token is required to enable Meeting Recorder.")
        _notify(on_progress, "Validating Discord bot token…")
        validate_and_store_token(discord_bot_token)

    download_models(on_progress=on_progress)

    _notify(on_progress, "Meeting Recorder is ready.")
    return SetupResult(dependencies_ok=True, token_ok=True, models_ok=True)


class SetupJob:
    """Runs run_setup() on its own daemon thread - mirrors TranscriptionJob/
    SummarizationJob's shape (see meeting_transcriber.py)."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(
        self,
        discord_bot_token: str | None,
        *,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_done: Callable[[SetupResult], None],
    ) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run,
            name="meeting_recorder_setup",
            daemon=True,
            args=(discord_bot_token, on_status, on_error, on_done),
        )
        self._thread.start()

    def _run(self, discord_bot_token, on_status, on_error, on_done) -> None:
        try:
            result = run_setup(discord_bot_token, on_progress=on_status)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via on_error
            self._safe_call(on_error, exc)
        else:
            self._safe_call(on_done, result)
        finally:
            self._running.clear()

    @staticmethod
    def _safe_call(fn: Callable | None, *args: Any) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            pass


class HighQualitySetupJob:
    """Runs download_high_quality_assets() on its own daemon thread - mirrors
    SetupJob's shape above, kept as a separate job/class (rather than reusing
    SetupJob) since it has no discord-token argument and can run independently
    of (and after) the base Meeting Recorder setup."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(
        self,
        *,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_done: Callable[[None], None],
    ) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run,
            name="meeting_recorder_high_quality_setup",
            daemon=True,
            args=(on_status, on_error, on_done),
        )
        self._thread.start()

    def _run(self, on_status, on_error, on_done) -> None:
        try:
            download_high_quality_assets(on_progress=on_status)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via on_error
            self._safe_call(on_error, exc)
        else:
            self._safe_call(on_done, None)
        finally:
            self._running.clear()

    @staticmethod
    def _safe_call(fn: Callable | None, *args: Any) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            pass
