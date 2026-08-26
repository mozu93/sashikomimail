# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("msal_extensions")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
        ("docs/ユーザーマニュアル.md", "docs"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # openpyxlはNumPyを任意機能として読み込むが、本アプリでは使用しない。
    # NumPy 2.3系をPyInstallerで同梱すると起動時に属性の解決に失敗するため除外する。
    excludes=["numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SashikomiMail",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="SashikomiMail",
)
