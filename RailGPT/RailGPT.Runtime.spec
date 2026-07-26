# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)

def data_tree(name):
    directory = root / name
    return [(str(path), str(path.parent.relative_to(root)))
            for path in directory.rglob("*") if path.is_file()]

datas = []
for directory in ("templates", "static", "assets", "knowledge", "prompts", "tools"):
    if (root / directory).exists():
        datas.extend(data_tree(directory))

hiddenimports = []
for package in ("agent", "memory", "knowledge", "llm", "tools", "thinking", "utils"):
    if (root / package).exists():
        hiddenimports.extend(collect_submodules(package))

a = Analysis(
    [str(root / "server_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pywebview", "PyQt5", "PySide6"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RailGPT.Runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
