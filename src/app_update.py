"""GitHub Releases auto-update: check, download, verify, and apply."""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import requests
from packaging.version import InvalidVersion, Version

from app_config import app_preferences_from_config, merge_app_preferences, read_config, write_config
from app_version import current_version, is_dev_build
from runtime_paths import bundle_dir, is_packaged_build
from session_guard import has_unlogged_time, prompt_unlogged_exit

GITHUB_REPO = "lindeb2/ELG-app"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_LIST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "ELG-app-updater",
}

CheckReason = Literal["startup", "daily", "manual"]
DAILY_INTERVAL_MS = 24 * 60 * 60 * 1000
STARTUP_DELAY_MS = 2000

# Suppresses startup/daily dialogs for this version until restart or a newer release.
_session_dismissed_version: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: str
    body: str
    prerelease: bool
    assets: dict[str, str]
    html_url: str


@dataclass
class UpdateCheckResult:
    release: ReleaseInfo | None
    error: str | None = None
    up_to_date: bool = False


def platform_asset_name() -> str:
    if sys.platform == "win32":
        machine = platform.machine().upper()
        if machine in ("ARM64", "AARCH64"):
            return "ELG-arm64.exe"
        return "ELG.exe"
    if sys.platform == "darwin":
        return "ELG.dmg"
    if sys.platform.startswith("linux"):
        return "ELG.tar.gz"
    raise RuntimeError(f"Unsupported platform for auto-update: {sys.platform}")


def _parse_version(value: str) -> Version | None:
    try:
        return Version(value)
    except InvalidVersion:
        return None


def is_newer_version(remote: str, local: str) -> bool:
    remote_v = _parse_version(remote)
    local_v = _parse_version(local)
    if remote_v is None or local_v is None:
        return remote != local
    return remote_v > local_v


def should_prompt(release: ReleaseInfo) -> bool:
    if _session_dismissed_version is None:
        return True
    return release.version != _session_dismissed_version


def dismiss_for_session(version: str) -> None:
    global _session_dismissed_version
    _session_dismissed_version = version


def release_to_dict(release: ReleaseInfo) -> dict:
    return {
        "tag_name": release.tag_name,
        "version": release.version,
        "body": release.body,
        "prerelease": release.prerelease,
        "assets": dict(release.assets),
        "html_url": release.html_url,
    }


def release_from_dict(data: dict | None) -> ReleaseInfo | None:
    if not data or not data.get("version"):
        return None
    assets = data.get("assets")
    if not isinstance(assets, dict):
        return None
    asset_name = platform_asset_name()
    if asset_name not in assets:
        return None
    return ReleaseInfo(
        tag_name=str(data.get("tag_name") or f"v{data['version']}"),
        version=str(data["version"]),
        body=str(data.get("body") or ""),
        prerelease=bool(data.get("prerelease")),
        assets={str(k): str(v) for k, v in assets.items()},
        html_url=str(data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"),
    )


def save_pending_update(release: ReleaseInfo) -> None:
    config = read_config()
    app_prefs = app_preferences_from_config(config)
    app_prefs["pending_update"] = release_to_dict(release)
    write_config(merge_app_preferences(config, app_prefs))


def clear_pending_update() -> None:
    config = read_config()
    app_prefs = app_preferences_from_config(config)
    if app_prefs.get("pending_update") is None:
        return
    app_prefs["pending_update"] = None
    write_config(merge_app_preferences(config, app_prefs))


def load_pending_update() -> ReleaseInfo | None:
    app_prefs = app_preferences_from_config(read_config())
    release = release_from_dict(app_prefs.get("pending_update"))
    if release is None:
        return None
    local = current_version()
    if not is_newer_version(release.version, local):
        clear_pending_update()
        return None
    return release


def _release_from_payload(payload: dict) -> ReleaseInfo | None:
    assets: dict[str, str] = {}
    for asset in payload.get("assets", []):
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if name and url:
            assets[name] = url

    tag_name = str(payload.get("tag_name", "")).strip()
    if not tag_name:
        return None

    version = tag_name[1:] if tag_name.startswith("v") else tag_name
    return ReleaseInfo(
        tag_name=tag_name,
        version=version,
        body=str(payload.get("body") or "").strip(),
        prerelease=bool(payload.get("prerelease")),
        assets=assets,
        html_url=str(payload.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"),
    )


def _fetch_json(url: str) -> dict | list:
    response = requests.get(url, headers=GITHUB_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def _fetch_latest_release(*, include_prerelease: bool) -> ReleaseInfo | None:
    if include_prerelease:
        payloads = _fetch_json(RELEASES_LIST_URL)
        if not isinstance(payloads, list):
            return None
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            release = _release_from_payload(payload)
            if release is not None:
                return release
        return None

    payload = _fetch_json(RELEASES_LATEST_URL)
    if not isinstance(payload, dict):
        return None
    return _release_from_payload(payload)


def check_for_update(*, include_prerelease: bool) -> UpdateCheckResult:
    if is_dev_build():
        return UpdateCheckResult(release=None, up_to_date=True)

    try:
        release = _fetch_latest_release(include_prerelease=include_prerelease)
    except requests.RequestException as exc:
        return UpdateCheckResult(release=None, error=str(exc))

    if release is None:
        return UpdateCheckResult(release=None, error="No release information found.")

    local = current_version()
    if not is_newer_version(release.version, local):
        return UpdateCheckResult(release=None, up_to_date=True)

    asset_name = platform_asset_name()
    if asset_name not in release.assets:
        return UpdateCheckResult(
            release=None,
            error=f"Release {release.version} has no asset {asset_name}.",
        )

    return UpdateCheckResult(release=release)


def record_update_check_timestamp() -> None:
    config = read_config()
    app_prefs = app_preferences_from_config(config)
    app_prefs["last_update_check_at"] = datetime.now(timezone.utc).isoformat()
    write_config(merge_app_preferences(config, app_prefs))


def _apply_check_result(result: UpdateCheckResult, *, on_pending_changed: Callable[[], None] | None) -> None:
    if result.up_to_date:
        clear_pending_update()
        if on_pending_changed is not None:
            on_pending_changed()
        return
    if result.release is not None:
        save_pending_update(result.release)
        if on_pending_changed is not None:
            on_pending_changed()


def _parse_checksums(text: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0].lower()
        filename = parts[-1]
        checksums[filename] = digest
    return checksums


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_with_checksum(
    asset_url: str,
    dest_dir: Path,
    expected_name: str,
    checksums_url: str | None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / expected_name

    with requests.get(asset_url, stream=True, timeout=120, headers=GITHUB_HEADERS) as response:
        response.raise_for_status()
        with dest_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

    expected_digest: str | None = None
    if checksums_url:
        checksum_response = requests.get(checksums_url, timeout=30, headers=GITHUB_HEADERS)
        checksum_response.raise_for_status()
        checksums = _parse_checksums(checksum_response.text)
        expected_digest = checksums.get(expected_name)

    actual_digest = _sha256_file(dest_path)
    if expected_digest and actual_digest != expected_digest.lower():
        dest_path.unlink(missing_ok=True)
        raise ValueError(
            f"Checksum mismatch for {expected_name}: expected {expected_digest}, got {actual_digest}"
        )

    return dest_path


def _checksums_url_for_release(release: ReleaseInfo) -> str | None:
    return release.assets.get("checksums.txt")


def confirm_update_allowed(shell) -> bool:
    if shell is None:
        return True
    timetable = shell.get_timetable()
    if not has_unlogged_time(timetable):
        return True
    choice = prompt_unlogged_exit(shell._window, timetable)
    if choice != "discard":
        return False
    shell.discard_timetable_session()
    return True


def _apply_windows(installer_path: Path, *, instance_guard) -> None:
    if instance_guard is not None:
        instance_guard.release()

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)

    subprocess.Popen(
        [
            str(installer_path),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
        ],
        creationflags=creationflags,
        close_fds=True,
    )
    os._exit(0)


def _linux_updater_script() -> Path:
    bundled = bundle_dir() / "linux_apply_update.sh"
    if bundled.is_file():
        return bundled

    repo_script = Path(__file__).resolve().parent.parent / "scripts" / "linux_apply_update.sh"
    if repo_script.is_file():
        return repo_script

    raise FileNotFoundError("linux_apply_update.sh not found in bundle or repository.")


def _apply_linux(archive_path: Path, *, instance_guard) -> None:
    install_dir = Path(os.path.dirname(os.path.abspath(sys.executable)))
    exe_path = install_dir / "main"
    if not exe_path.is_file():
        exe_path = Path(sys.executable)

    extract_root = Path(tempfile.mkdtemp(prefix="elg-update-"))
    new_dir = extract_root / "new"
    new_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(new_dir)

    updater_script = _linux_updater_script()
    staged_updater = extract_root / "linux_apply_update.sh"
    shutil.copy2(updater_script, staged_updater)
    staged_updater.chmod(0o755)

    if instance_guard is not None:
        instance_guard.release()

    subprocess.Popen(
        [
            "/bin/bash",
            str(staged_updater),
            str(os.getpid()),
            str(install_dir),
            str(new_dir),
            str(exe_path),
        ],
        start_new_session=True,
        close_fds=True,
    )
    os._exit(0)


def _apply_macos(release: ReleaseInfo) -> None:
    asset_name = platform_asset_name()
    url = release.assets.get(asset_name) or release.html_url
    webbrowser.open(url)


def apply_update(
    release: ReleaseInfo,
    downloaded: Path,
    *,
    shell=None,
    instance_guard=None,
) -> None:
    if not is_packaged_build():
        raise RuntimeError("Updates can only be applied from a packaged build.")

    if not confirm_update_allowed(shell):
        return

    if sys.platform == "win32":
        _apply_windows(downloaded, instance_guard=instance_guard)
    elif sys.platform.startswith("linux"):
        _apply_linux(downloaded, instance_guard=instance_guard)
    elif sys.platform == "darwin":
        _apply_macos(release)
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


def download_update_artifact(release: ReleaseInfo, dest_dir: Path) -> Path:
    asset_name = platform_asset_name()
    asset_url = release.assets[asset_name]
    checksums_url = _checksums_url_for_release(release)
    return download_with_checksum(asset_url, dest_dir, asset_name, checksums_url)


def format_last_checked(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = parsed.astimezone()
        return local.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def run_check_in_background(
    *,
    include_prerelease: bool,
    on_complete: Callable[[UpdateCheckResult], None],
    on_pending_changed: Callable[[], None] | None = None,
) -> None:
    def worker() -> None:
        try:
            result = check_for_update(include_prerelease=include_prerelease)
            record_update_check_timestamp()
            if result.error is None:
                _apply_check_result(result, on_pending_changed=on_pending_changed)
        except Exception as exc:  # noqa: BLE001 — surface to UI
            result = UpdateCheckResult(release=None, error=str(exc))
        on_complete(result)

    threading.Thread(target=worker, daemon=True).start()


def schedule_automatic_checks(
    root,
    *,
    shell_holder: dict,
    instance_guard,
    show_dialog: Callable,
    on_pending_changed: Callable[[], None] | None = None,
) -> Callable[[Callable[[str, str], None]], None]:
    def _maybe_show(result: UpdateCheckResult, *, reason: CheckReason) -> None:
        if result.error or result.up_to_date or result.release is None:
            return
        if reason == "manual":
            show_dialog(result.release, force=True)
            return
        if should_prompt(result.release):
            show_dialog(result.release, force=False)

    def _run_check(reason: CheckReason) -> None:
        if is_dev_build():
            return

        prefs = app_preferences_from_config(read_config())

        def on_complete(result: UpdateCheckResult) -> None:
            root.after(0, lambda: _maybe_show(result, reason=reason))

        run_check_in_background(
            include_prerelease=bool(prefs.get("include_prereleases")),
            on_complete=on_complete,
            on_pending_changed=on_pending_changed,
        )

    def _schedule_daily() -> None:
        root.after(DAILY_INTERVAL_MS, _daily_tick)

    def _daily_tick() -> None:
        _run_check("daily")
        _schedule_daily()

    root.after(STARTUP_DELAY_MS, lambda: _run_check("startup"))
    _schedule_daily()

    def _manual_check_callback(on_status: Callable[[str, str], None]) -> None:
        prefs = app_preferences_from_config(read_config())

        def on_complete(result: UpdateCheckResult) -> None:
            def ui() -> None:
                if result.error:
                    on_status(f"Update check failed: {result.error}", "#FF4444")
                    return
                if result.up_to_date:
                    on_status("You're up to date.", "#00AD00")
                    return
                if result.release is not None:
                    on_status(f"Update {result.release.version} available.", "#B0B0B0")
                    show_dialog(result.release, force=True)

            root.after(0, ui)

        run_check_in_background(
            include_prerelease=bool(prefs.get("include_prereleases")),
            on_complete=on_complete,
            on_pending_changed=on_pending_changed,
        )

    return _manual_check_callback
