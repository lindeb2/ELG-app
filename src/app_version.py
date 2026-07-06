"""Application release version (CI-baked in packaged builds)."""
from __future__ import annotations

import importlib.util
import sys

from runtime_paths import bundle_dir, is_packaged_build

_DEV_VERSION = "0.0.0-dev"
_version_cache: str | None = None


def _load_version_from_module() -> str | None:
    try:
        import _version
    except ImportError:
        _version = None

    if _version is not None and hasattr(_version, "__version__"):
        return str(_version.__version__)

    if is_packaged_build():
        path = bundle_dir() / "_version.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("_version", path)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "__version__"):
                    return str(module.__version__)

    return None


def current_version() -> str:
    global _version_cache
    if _version_cache is not None:
        return _version_cache

    loaded = _load_version_from_module()
    _version_cache = loaded if loaded else _DEV_VERSION
    return _version_cache


def is_dev_build() -> bool:
    version = current_version()
    return version == _DEV_VERSION or version.endswith("-dev")
