# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ELG desktop app."""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH)
src_dir = project_root / "src"

block_cipher = None

datas = collect_data_files("customtkinter")
datas += collect_data_files("pystray")

for icon_name in (
    "ELG Studio 0.1_16_clean_big.ico",
    "ELG Studio 0.1_256_128_64_48_32_24_clean_rounded.ico",
):
    datas.append((str(src_dir / icon_name), "."))

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

exe = EXE(
    pyz,
    a.scripts,
    [] if is_macos else a.binaries,
    [] if is_macos else a.datas,
    [] if is_macos else [],
    name="ELG-app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=is_macos,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=not is_macos,
)

if is_macos:
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
        icon=None,
        bundle_identifier="com.lindeb2.elg-app",
    )
