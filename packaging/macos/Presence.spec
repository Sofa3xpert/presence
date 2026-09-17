# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Presence.app (macOS, arm64, onedir + windowed bundle).

    uv run pyinstaller --noconfirm --clean packaging/macos/Presence.spec

Never onefile, never universal2 (cryptography/pymupdf wheels are per-arch),
never UPX. Signing happens here only when CODESIGN_IDENTITY is set.
"""

import os

import presence
from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.abspath(SPECPATH)  # noqa: F821 — provided by PyInstaller
PKG = os.path.dirname(os.path.abspath(presence.__file__))
IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(PKG, "app", "templates"), "presence/app/templates"),
        (os.path.join(PKG, "app", "static"), "presence/app/static"),
        (os.path.join(HERE, "menubar.png"), "."),
    ],
    hiddenimports=collect_submodules("presence")
    + [
        "fitz",
        "google.oauth2.service_account",
        "google.oauth2.credentials",
        "google.auth.transport.requests",
        "google_auth_oauthlib.flow",
        "keyring.backends.macOS",
        "segno",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Presence",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,  # native arch of the build machine (arm64 on Apple silicon)
    codesign_identity=IDENTITY,
    entitlements_file=os.path.join(HERE, "entitlements.plist") if IDENTITY else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Presence")
app = BUNDLE(
    coll,
    name="Presence.app",
    icon=os.path.join(HERE, "Presence.icns"),
    bundle_identifier="org.presence.app",
    version=presence.__version__,
    info_plist={
        "LSUIElement": True,  # menu-bar app: no Dock icon
        "NSHighResolutionCapable": True,
        "CFBundleDisplayName": "Presence",
        "CFBundleName": "Presence",
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Presence contributors. AGPL-3.0.",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
