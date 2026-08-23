# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
DEBUG_BUILD = os.environ.get("MARKETPLACE_DEBUG_BUILD", "0") == "1"

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('playwright_browsers', 'playwright_browsers'),
        ('.venv/Lib/site-packages/playwright/driver', 'playwright/driver'),
    ],
    hiddenimports=[],
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MarketplaceManager-Debug' if DEBUG_BUILD else 'MarketplaceManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=DEBUG_BUILD,  # Muestra consola si es build de diagnóstico (Debug)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MarketplaceManager',
)
