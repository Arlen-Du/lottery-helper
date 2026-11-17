# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas',
        'numpy',
        'lxml',
        'PIL',
        'Pillow',
        'scipy',
        'matplotlib',
        'plotly',
        'tkinter',
        'unittest',
        'pydoc',
        'PyQt6.QtPdf',
        'PyQt6.QtNetwork',
        'PyQt6.QtSvg',
        'PyQt6.QtQuick',
        'PyQt6.QtQml',
        'PyQt6.QtOpenGL',
    ],
    noarchive=False,
    optimize=0,
)

# Filter out unused Qt6 frameworks/plugins that hooks may pull in transitively
_DROP_BIN_TOKENS = (
    'QtPdf',
    'libqpdf',
    'QtSvg',
    'libqsvg',
    'QtNetwork',
    'libqtiff',
    'libqwebp',
    'libqjp2',
    'libqtga',
)
a.binaries = [b for b in a.binaries if not any(tok in b[0] for tok in _DROP_BIN_TOKENS)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LotteryHelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name='LotteryHelper',
)
app = BUNDLE(
    coll,
    name='LotteryHelper.app',
    icon=None,
    bundle_identifier='com.arlen.lotteryhelper',
)
