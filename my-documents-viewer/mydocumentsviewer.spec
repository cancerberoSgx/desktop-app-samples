# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

# sqlite-vec ships its loadable extension (vec0.so/.dylib/.dll) as package
# data rather than importable Python, so it needs collecting explicitly -
# PyInstaller's default module scan won't find it on its own.
datas = [('app/db/migrations', 'app/db/migrations')]
datas += collect_data_files('sqlite_vec')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['sqlite_vec'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='mydocumentsviewer',
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
    name='mydocumentsviewer',
)

app = BUNDLE(
    coll,
    name='mydocumentsviewer.app',
    icon=None,
    bundle_identifier=None,
)
