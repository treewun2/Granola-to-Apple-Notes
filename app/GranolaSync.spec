# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Granola Sync menu-bar app.
# Build with app/build.sh (must run on a Mac).
#
# Signing: if GS_SIGN_IDENTITY is set (a "Developer ID Application: …"
# identity), PyInstaller signs every binary in the bundle with the
# hardened runtime + entitlements.plist — ready for notarization
# (see docs/NOTARIZING.md). Unset, the build is ad-hoc signed as before.

import os
from pathlib import Path

here = Path(SPECPATH).resolve()      # app/
engine_dir = here.parent             # repo root (granola_sync.py lives here)

sign_identity = os.environ.get("GS_SIGN_IDENTITY")
entitlements = str(here / "entitlements.plist") if sign_identity else None

a = Analysis(
    [str(here / "granola_sync_app.py")],
    pathex=[str(here), str(engine_dir)],
    binaries=[],
    # Menu-bar template icon; the app resolves it via sys._MEIPASS.
    datas=[(str(here / "menubar_icon.png"), ".")],
    hiddenimports=["rumps"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Granola Sync",
    console=False,          # menu-bar app: no terminal window
    upx=False,
    codesign_identity=sign_identity,
    entitlements_file=entitlements,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Granola Sync",
    upx=False,
)

app = BUNDLE(
    coll,
    name="Granola Sync.app",
    icon=None,               # default icon for now; add .icns later if wanted
    bundle_identifier="com.stevepettit.granola-sync.app",
    info_plist={
        # Menu-bar only: no Dock icon, no app switcher entry.
        "LSUIElement": True,
        "CFBundleName": "Granola Sync",
        "CFBundleShortVersionString": "2.1.2",
        "CFBundleVersion": "2.1.2",
        # Required for the macOS Automation (TCC) prompt to appear when
        # the app first drives Notes / System Events via AppleScript.
        "NSAppleEventsUsageDescription":
            "Granola Sync creates your meeting notes in Apple Notes, and "
            "uses System Events if you turn on Launch at Login.",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
