# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the standalone Linux x86_64 fnOS native server binary."""

a = Analysis(
    ["web_main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("src/web/static", "src/web/static"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "multipart",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6",
        "PyQt5",
        "PySide6",
        "tkinter",
        "matplotlib",
        "pandas",
        "numpy",
        "scipy",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="lottery-helper-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    console=True,
)
