"""First-run model downloads and device selection.

Nothing model-shaped ships in the installer; everything lands in
{ELG data}/meeting_mike/models/ on first use.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from meeting_host import config

_WHISPER_REPOS = {
    # faster-whisper resolves bare names via HF; pin the CTranslate2 repos.
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "small.en": "Systran/faster-whisper-small.en",
}

_DOWNLOAD_SLACK_BYTES = 400_000_000
LLAMA_BUILD = "b11026"
_LLAMA_BASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"


def whisper_cache_dir(base: Path) -> Path:
    path = config.models_dir(base) / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_dir(base: Path) -> Path:
    path = config.models_dir(base) / "llm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_path(base: Path) -> Path:
    return llm_dir(base) / config.LLM_FILENAME


def llama_dir(base: Path) -> Path:
    path = config.models_dir(base) / "llama"
    path.mkdir(parents=True, exist_ok=True)
    return path


def whisper_model_dir(base: Path, model: str) -> Path:
    return whisper_cache_dir(base) / model.replace("/", "_")


def _dir_has_model(path: Path) -> bool:
    if not path.is_dir():
        return False
    weights = path / "model.bin"
    return weights.is_file() and weights.stat().st_size > 0


def installed_llm(base: Path) -> Path | None:
    path = llm_path(base)
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def choose_llm_candidate(free_bytes: int) -> dict | None:
    """The locked 8B GGUF when it fits; otherwise nothing."""
    if free_bytes >= config.LLM_BYTES + _DOWNLOAD_SLACK_BYTES:
        return {
            "repo": config.LLM_REPO,
            "filename": config.LLM_FILENAME,
            "bytes": config.LLM_BYTES,
        }
    return None


def remove_noncanonical_llms(base: Path) -> list[Path]:
    """Delete leftover GGUFs so a smaller model cannot be picked up later."""
    directory = llm_dir(base)
    keep = config.LLM_FILENAME.lower()
    removed: list[Path] = []
    for path in directory.glob("*.gguf"):
        if path.name.lower() == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


def missing_assets(base: Path) -> list[str]:
    missing: list[str] = []
    for model in (config.MIX_MODEL, config.USER_MODEL):
        if not _dir_has_model(whisper_model_dir(base, model)):
            missing.append(model)
    if installed_llm(base) is None:
        missing.append(config.LLM_FILENAME)
    if llama_server_path(base) is None:
        missing.append("llama-server")
    return missing


def ensure_whisper_model(base: Path, model: str, *, on_progress=None) -> Path:
    """Local dir for a faster-whisper model, downloading it if needed."""
    destination = whisper_model_dir(base, model)
    if _dir_has_model(destination):
        return destination

    repo_id = _WHISPER_REPOS.get(model, model)
    if on_progress is not None:
        on_progress(f"Downloading speech model {model}")

    from huggingface_hub import snapshot_download

    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(destination),
        allow_patterns=["*.bin", "*.json", "*.txt", "*.model"],
    )
    if not _dir_has_model(destination):
        raise RuntimeError(f"Speech model '{model}' did not download correctly.")
    return destination


def _download_gguf(candidate: dict, destination: Path, *, on_progress=None) -> Path:
    if on_progress is not None:
        on_progress(f"Downloading language model {candidate['filename']}")

    from huggingface_hub import hf_hub_download

    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded = hf_hub_download(
        repo_id=str(candidate["repo"]),
        filename=str(candidate["filename"]),
        local_dir=str(destination.parent),
    )
    resolved = Path(downloaded)
    if resolved != destination:
        try:
            resolved.replace(destination)
        except OSError:
            return resolved
    cache = destination.parent / ".cache"
    if cache.is_dir():
        shutil.rmtree(cache, ignore_errors=True)
    return destination


def ensure_llm(base: Path, *, on_progress=None) -> Path:
    """Local GGUF path for the summary/merge model, downloading if needed."""
    remove_noncanonical_llms(base)
    existing = installed_llm(base)
    if existing is not None:
        return existing

    free = shutil.disk_usage(llm_dir(base)).free
    candidate = choose_llm_candidate(free)
    if candidate is None:
        raise RuntimeError(
            "Not enough disk space for a local summary model "
            f"(have {free / 1_000_000_000:.1f} GB free; "
            "the 8B model needs about 5.4 GB)."
        )
    try:
        return _download_gguf(candidate, llm_path(base), on_progress=on_progress)
    except Exception as exc:
        raise RuntimeError(f"Local model download failed: {exc}") from exc


def _llama_zip_name() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        if machine in ("arm64", "aarch64"):
            return f"llama-{LLAMA_BUILD}-bin-win-cpu-arm64.zip"
        return f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip"
    if sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            return f"llama-{LLAMA_BUILD}-bin-macos-arm64.zip"
        return f"llama-{LLAMA_BUILD}-bin-macos-x64.zip"
    return f"llama-{LLAMA_BUILD}-bin-ubuntu-x64.zip"


def _flatten_llama(directory: Path) -> None:
    exe_names = ("llama-server.exe", "llama-server")
    if any((directory / name).is_file() for name in exe_names):
        return
    for name in exe_names:
        for candidate in directory.rglob(name):
            source = candidate.parent
            for item in source.iterdir():
                target = directory / item.name
                if target.exists():
                    continue
                shutil.move(str(item), str(target))
            return


def ensure_llama_server(base: Path, *, on_progress=None) -> Path:
    """llama-server next to its DLLs. Downloads a CPU build when missing."""
    existing = llama_server_path(base)
    if existing is not None:
        return existing

    if on_progress is not None:
        on_progress("Downloading llama-server")

    directory = llama_dir(base)
    zip_name = _llama_zip_name()
    archive = directory / zip_name
    url = f"{_LLAMA_BASE_URL}/{zip_name}"
    temporary = archive.with_suffix(archive.suffix + ".part")
    with urllib.request.urlopen(url) as response, open(temporary, "wb") as handle:
        shutil.copyfileobj(response, handle)
    temporary.replace(archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(directory)
    _flatten_llama(directory)
    try:
        archive.unlink()
    except OSError:
        pass

    resolved = llama_server_path(base)
    if resolved is None:
        raise RuntimeError("llama-server did not unpack correctly.")
    return resolved


def ensure_all(base: Path, *, on_progress=None) -> None:
    for model in (config.MIX_MODEL, config.USER_MODEL):
        ensure_whisper_model(base, model, on_progress=on_progress)
    ensure_llama_server(base, on_progress=on_progress)
    ensure_llm(base, on_progress=on_progress)


def llama_server_path(base: Path | None = None) -> Path | None:
    """Installed llama-server, preferring a GPU build when one was shipped."""
    override = os.environ.get("ELG_LLAMA_SERVER")
    if override and Path(override).is_file():
        return Path(override)

    names = ("llama-server.exe", "llama-server")
    if base is not None:
        directory = config.models_dir(base) / "llama"
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate

    root = Path(__file__).resolve().parent.parent
    for sub in ("llama-gpu", "llama"):
        for name in names:
            candidate = root / sub / name
            if candidate.is_file():
                return candidate
    return None


def has_gpu_llama(base: Path | None = None) -> bool:
    path = llama_server_path(base)
    return path is not None and path.parent.name == "llama-gpu"
