# Auto-update

ELG checks [GitHub Releases](https://github.com/lindeb2/ELG-app/releases) for newer builds and can apply updates in place on Windows and Linux. macOS opens the `.dmg` download in a browser until code signing and notarization are in place.

## Behavior

- **Startup** and **daily** background checks on packaged builds (not dev `0.0.0-dev` runs).
- When a newer release is found, a dialog offers **Update now** or **Not now**, and the release is saved as a **pending update** in settings.
- **Not now** suppresses startup/daily dialogs for that version until the app restarts or a newer release appears. The pending update remains in Settings.
- **Settings → Updates** shows the current version, pending update (if any) with **Install update** (no re-check needed), optional pre-releases, last check time, and **Check for updates**.
- Before applying an update, unlogged timetable time triggers the same discard/abort flow as exit.

## Check scheduling

- **Startup:** `root.after(2000, …)` runs one check ~2 seconds after the update scheduler starts.
- **Daily:** a chained `root.after(24 * 60 * 60 * 1000, …)` timer — first daily check 24 hours after startup, then every 24 hours while the app runs.
- Both run in a background thread; UI updates are marshalled back onto the Tk main loop.

## Platform apply paths

| Platform | Update now |
|----------|------------|
| Windows | Downloads the Inno installer, verifies SHA256, runs `/VERYSILENT` in-place upgrade, relaunches via installer |
| Linux | Downloads `ELG.tar.gz`, verifies SHA256, swaps install directory via `linux_apply_update.sh`, relaunches |
| macOS | Opens the `.dmg` download URL; user replaces `ELG.app` manually |

## Version source

Release version is baked at CI build time into `src/_version.py` (gitignored, generated from the `v*` git tag) and bundled into the Nuitka binary.

## Integrity

Each release artifact folder includes `checksums.txt` (SHA256). The updater verifies downloads against it before applying.

**Limitation:** checksums detect corruption or a broken download, not authenticity. Authenticity depends on future code signing.

## CI

[`.github/workflows/build.yml`](../.github/workflows/build.yml) writes `_version.py`, builds artifacts, emits `checksums.txt` per job, and attaches both to GitHub Releases.

## Windows installer

[`installer/windows/ELG-app.iss`](../installer/windows/ELG-app.iss) sets `AppMutex=Local\ELG_SingleInstance_v1` and `CloseApplications=force` so upgrades can close the running app cleanly.

## Code map

- `src/app_version.py` — read baked-in version
- `src/app_update.py` — GitHub API, download, verify, apply, scheduling
- `src/update_dialog.py` — prompt UI
- `scripts/linux_apply_update.sh` — Linux swap helper

## Manual test checklist

1. Dev build: version shows `0.0.0-dev`, no automatic checks.
2. Packaged build on current release: “You're up to date.”
3. Older packaged build: dialog on startup (unless dismissed this session).
4. **Not now**: no re-prompt for same version until restart; Settings still shows **Install update**.
5. **Include pre-releases**: beta tags visible when enabled.
6. Unlogged session: update blocked until discard.
7. Windows/Linux: apply restarts on new version.
8. macOS: browser opens DMG link.
