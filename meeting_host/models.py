"""First-run model downloads and device selection.

Nothing model-shaped ships in the installer; everything lands in
{ELG data}/meeting_mike/models/ on first use.
"""
from __future__ import annotations

import os
from pathlib import Path

from meeting_host import config

_WHISPER_REPOS = {
    # faster-whisper resolves bare names via HF; pin the CTranslate2 repos.
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "small.en": "Systran/faster-whisper-small.en",
}


def whisper_cache_dir(base: Path) -> Path:
    path = config.models_dir(base) / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_path(base: Path) -> Path:
    return config.models_dir(base) / "llm" / config.LLM_FILENAME


def whisper_model_dir(base: Path, model: str) -> Path:
    return whisper_cache_dir(base) / model.replace("/", "_")


def _dir_has_model(path: Path) -> bool:
    if not path.is_dir():
        return False
    weights = path / "model.bin"
    return weights.is_file() and weights.stat().st_size > 0


def missing_assets(base: Path) -> list[str]:
    missing: list[str] = []
    for model in (config.MIX_MODEL, config.USER_MODEL):
        if not _dir_has_model(whisper_model_dir(base, model)):
            missing.append(model)
    target = llm_path(base)
    if not (target.is_file() and target.stat().st_size > 0):
        missing.append(config.LLM_FILENAME)
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


def ensure_llm(base: Path, *, on_progress=None) -> Path:
    """Local GGUF path for the summary/merge model, downloading if needed."""
    destination = llm_path(base)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination

    if on_progress is not None:
        on_progress("Downloading language model (about 5 GB, one time)")

    from huggingface_hub import hf_hub_download

    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded = hf_hub_download(
        repo_id=config.LLM_REPO,
        filename=config.LLM_FILENAME,
        local_dir=str(destination.parent),
    )
    resolved = Path(downloaded)
    if resolved != destination:
        try:
            resolved.replace(destination)
        except OSError:
            return resolved
    return destination


def ensure_all(base: Path, *, on_progress=None) -> None:
    for model in (config.MIX_MODEL, config.USER_MODEL):
        ensure_whisper_model(base, model, on_progress=on_progress)
    ensure_llm(base, on_progress=on_progress)


def llama_server_path() -> Path | None:
    """Bundled llama-server, preferring a GPU build when one was shipped."""
    override = os.environ.get("ELG_LLAMA_SERVER")
    if override and Path(override).is_file():
        return Path(override)

    root = Path(__file__).resolve().parent.parent
    for sub in ("llama-gpu", "llama"):
        for name in ("llama-server.exe", "llama-server"):
            candidate = root / sub / name
            if candidate.is_file():
                return candidate
    return None


def has_gpu_llama() -> bool:
    path = llama_server_path()
    return path is not None and path.parent.name == "llama-gpu"
