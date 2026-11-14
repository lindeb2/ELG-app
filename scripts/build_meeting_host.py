"""Assemble the ELG Meeting Host payload for the Windows installer.

Produces build/meeting-host/<arch>/ containing an embedded CPython with the
sidecar dependencies, the llama.cpp server binaries and the meeting_host
package. No models are included; those download on first use.

The Host is not compiled with Nuitka: it needs torch-adjacent native wheels
(ctranslate2, onnxruntime, PyNaCl) that are far more reliable when left as
ordinary site-packages.

Usage:
  python scripts/build_meeting_host.py --arch x64 [--gpu-backend none|cuda|vulkan]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PYTHON_VERSION = "3.12.10"
LLAMA_BUILD = "b11026"

_PYTHON_EMBED_ARCH = {"x64": "amd64", "arm64": "arm64"}
_LLAMA_CPU_ASSET = {
    "x64": f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip",
    "arm64": f"llama-{LLAMA_BUILD}-bin-win-cpu-arm64.zip",
}
_LLAMA_GPU_ASSET = {
    ("cuda", "x64"): f"llama-{LLAMA_BUILD}-bin-win-cuda-13.4-x64.zip",
    ("cuda", "arm64"): f"llama-{LLAMA_BUILD}-bin-win-cuda-13.4-arm64.zip",
    ("vulkan", "x64"): f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip",
}
_LLAMA_CUDART_ASSET = {
    "x64": "cudart-llama-bin-win-cuda-13.4-x64.zip",
    "arm64": "cudart-llama-bin-win-cuda-13.4-arm64.zip",
}
_LLAMA_BASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"

def log(message: str) -> None:
    print(f"[meeting-host] {message}", flush=True)


def download(url: str, destination: Path) -> Path:
    if destination.is_file() and destination.stat().st_size > 0:
        log(f"cached {destination.name}")
        return destination
    log(f"downloading {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(url) as response, open(temporary, "wb") as handle:
        shutil.copyfileobj(response, handle)
    temporary.replace(destination)
    return destination


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)


def build_runtime(cache: Path, stage: Path, arch: str) -> Path:
    """Embedded CPython with site-packages enabled and deps installed."""
    embed_arch = _PYTHON_EMBED_ARCH[arch]
    url = (
        f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
        f"python-{PYTHON_VERSION}-embed-{embed_arch}.zip"
    )
    archive = download(url, cache / f"python-{PYTHON_VERSION}-embed-{embed_arch}.zip")

    runtime = stage / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    extract(archive, runtime)

    # The embeddable build ships with site disabled and no site-packages.
    for pth in runtime.glob("python*._pth"):
        lines = pth.read_text(encoding="utf-8").splitlines()
        wanted = ["Lib\\site-packages", "..", "import site"]
        for entry in wanted:
            if entry not in lines:
                lines.append(entry)
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"patched {pth.name}")

    (runtime / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)

    python_exe = runtime / "python.exe"
    if not python_exe.is_file():
        raise SystemExit("Embedded runtime is missing python.exe")

    get_pip = download("https://bootstrap.pypa.io/get-pip.py", cache / "get-pip.py")
    run([str(python_exe), str(get_pip), "--no-warn-script-location"])
    run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "-r",
            str(REPO_ROOT / "meeting_host" / "requirements.txt"),
        ]
    )
    # pip itself is not needed at runtime and is the bulk of the leftovers.
    run([str(python_exe), "-m", "pip", "uninstall", "-y", "pip", "setuptools", "wheel"], check=False)
    return runtime


def run(command: list[str], *, check: bool = True) -> None:
    log(" ".join(command[:3]) + (" ..." if len(command) > 3 else ""))
    result = subprocess.run(command, cwd=str(REPO_ROOT))
    if check and result.returncode != 0:
        raise SystemExit(f"Command failed ({result.returncode}): {command[0]}")


def build_llama(cache: Path, stage: Path, arch: str, gpu_backend: str) -> None:
    cpu_archive = download(
        f"{_LLAMA_BASE_URL}/{_LLAMA_CPU_ASSET[arch]}", cache / _LLAMA_CPU_ASSET[arch]
    )
    llama_dir = stage / "llama"
    if llama_dir.exists():
        shutil.rmtree(llama_dir)
    extract(cpu_archive, llama_dir)
    _flatten_llama(llama_dir)

    if gpu_backend == "none":
        return

    asset = _LLAMA_GPU_ASSET.get((gpu_backend, arch))
    if asset is None:
        raise SystemExit(f"No {gpu_backend} llama.cpp build for {arch}")

    gpu_dir = stage / "llama-gpu"
    if gpu_dir.exists():
        shutil.rmtree(gpu_dir)
    extract(download(f"{_LLAMA_BASE_URL}/{asset}", cache / asset), gpu_dir)

    if gpu_backend == "cuda":
        cudart = _LLAMA_CUDART_ASSET[arch]
        extract(download(f"{_LLAMA_BASE_URL}/{cudart}", cache / cudart), gpu_dir)
    _flatten_llama(gpu_dir)


def _flatten_llama(directory: Path) -> None:
    """Some archives nest binaries one level down; llama_server expects them flat."""
    if (directory / "llama-server.exe").is_file():
        return
    for candidate in directory.rglob("llama-server.exe"):
        source = candidate.parent
        for item in source.iterdir():
            target = directory / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))
        break


def copy_package(stage: Path, version: str) -> None:
    package = stage / "meeting_host"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    source = REPO_ROOT / "meeting_host"
    for item in sorted(source.iterdir()):
        if item.name in {"runtime_secrets.py", "__pycache__"} or item.name.startswith("."):
            continue
        if item.suffix == ".py":
            shutil.copy2(item, package / item.name)
        elif item.is_dir() and (item / "__init__.py").is_file():
            shutil.copytree(
                item,
                package / item.name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

    secrets = source / "runtime_secrets.py"
    if not secrets.is_file():
        raise SystemExit(
            "meeting_host/runtime_secrets.py is missing. CI writes it; for a local "
            "build create it with DISCORD_BOT_TOKEN, DISCORD_GUILD_ID and MONGODB_URI. "
            "Add GROQ_API_KEY and TRANSCRIPTION_SOURCE = 'groq' to transcribe via Groq."
        )
    _stage_secrets(secrets, package / "runtime_secrets.py")

    (package / "_version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    log(f"staged meeting_host ({version})")


def _stage_secrets(source: Path, destination: Path) -> None:
    """Copy host secrets. A raw Groq key is sealed so the installer has no plaintext key."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from meeting_host.secret_codec import seal_secret, unseal_secret

    namespace: dict = {}
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    lines = []
    for name in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "MONGODB_URI"):
        value = namespace.get(name)
        if not value:
            raise SystemExit(f"meeting_host/runtime_secrets.py is missing {name}.")
        lines.append(f"{name} = {value!r}")

    key = str(namespace.get("GROQ_API_KEY") or "")
    source_name = str(namespace.get("TRANSCRIPTION_SOURCE") or "").strip().lower()
    if source_name == "groq" and not key:
        raise SystemExit(
            "TRANSCRIPTION_SOURCE is 'groq' but GROQ_API_KEY is empty in runtime_secrets.py."
        )
    if key:
        lines.append(f"GROQ_API_KEY = {seal_secret(unseal_secret(key))!r}")
    if source_name:
        lines.append(f"TRANSCRIPTION_SOURCE = {source_name!r}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("staged runtime secrets")


def write_manifest(stage: Path, *, version: str, arch: str, gpu_backend: str) -> None:
    manifest = {
        "version": version,
        "arch": arch,
        "python": PYTHON_VERSION,
        "llama_build": LLAMA_BUILD,
        "gpu_backend": gpu_backend,
        "entry": {"exe": "runtime\\python.exe", "args": ["-m", "meeting_host"]},
    }
    (stage / "meeting-host.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ELG Meeting Host payload")
    parser.add_argument("--arch", choices=sorted(_PYTHON_EMBED_ARCH), default="x64")
    parser.add_argument("--gpu-backend", choices=("none", "cuda", "vulkan"), default="none")
    parser.add_argument("--version", default=os.environ.get("APP_VERSION", "0.0.0-dev"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if sys.platform != "win32":
        log("WARNING: not running on Windows; the payload will not be usable.")

    stage = Path(args.output) if args.output else REPO_ROOT / "build" / "meeting-host" / args.arch
    cache = REPO_ROOT / "build" / "meeting-host" / "cache"
    stage.mkdir(parents=True, exist_ok=True)

    build_runtime(cache, stage, args.arch)
    build_llama(cache, stage, args.arch, args.gpu_backend)
    copy_package(stage, args.version)
    write_manifest(stage, version=args.version, arch=args.arch, gpu_backend=args.gpu_backend)

    log(f"payload ready at {stage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
