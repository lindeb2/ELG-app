# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ELG desktop app."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH)
src_dir = project_root / "src"
source_icon = src_dir / "ELG Studio 0.1_256_128_64_48_32_24_clean_rounded.ico"
caption_icon = src_dir / "ELG Studio 0.1_16_clean_big.ico"
shell_icon_path = project_root / "build" / "elg_app.ico"


def _build_shell_icon(source: Path, dest: Path) -> str:
    """Build a Windows-shell-friendly ICO (16px first, no spaces in path)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or source.stat().st_mtime > dest.stat().st_mtime:
        image = Image.open(source).convert("RGBA").resize(
            (256, 256),
            Image.Resampling.LANCZOS,
        )
        image.save(
            dest,
            format="ICO",
            sizes=[(16, 16), (20, 20), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    return str(dest)


app_icon = _build_shell_icon(source_icon, shell_icon_path)

block_cipher = None

datas = collect_data_files("customtkinter")
datas += collect_data_files("pystray")
datas += collect_data_files("tzdata")

for icon_path in (caption_icon, source_icon):
    datas.append((str(icon_path), "."))

hiddenimports = []
for package in (
    "CtkSmartScrollableFrame",
    "CTkStickyPlaceholderEntry",
    "CTkFlexToolTip",
    "CTkPieChart",
):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(src_dir / "main.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

is_macos = sys.platform == "darwin"

if is_macos:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="ELG-app",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="ELG-app",
    )
    app = BUNDLE(
        coll,
        name="ELG-app.app",
        icon=app_icon,
        bundle_identifier="com.lindeb2.elg-app",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="ELG-app",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=app_icon,
        onefile=True,
    )
