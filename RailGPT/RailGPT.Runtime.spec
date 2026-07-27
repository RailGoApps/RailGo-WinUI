# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)

def data_tree(name):
    directory = root / name
    return [
        (str(path), str(path.parent.relative_to(root)))
        for path in directory.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".py", ".pyc", ".pyo"}
        and not path.name.startswith("test_")
    ]

datas = []
for directory in ("templates", "static", "assets", "knowledge", "prompts", "tools"):
    if (root / directory).exists():
        datas.extend(data_tree(directory))
for filename in ("release_metadata.json", "README.md", "README_EN.md", "RailGPT.ico"):
    path = root / filename
    if path.exists():
        datas.append((str(path), "."))

hiddenimports = []
def is_runtime_module(module_name):
    return not any(
        part == "test" or part.startswith("test_")
        for part in module_name.split(".")
    )

for package in ("agent", "memory", "knowledge", "llm", "tools", "thinking", "utils"):
    if (root / package).exists():
        hiddenimports.extend(collect_submodules(package, filter=is_runtime_module))

a = Analysis(
    [str(root / "server_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # These are optional notebook/plotting/desktop integrations pulled in by
    # ML package hooks. The server runtime does not use them, and excluding
    # them materially reduces extraction time and memory pressure.
    excludes=[
        "pywebview",
        "webview",
        "PyQt5",
        "PySide6",
        "IPython",
        "flax",
        "jax",
        "jedi",
        "jupyter",
        "keras",
        "matplotlib",
        "nbformat",
        "notebook",
        "onnxruntime",
        "pandas",
        "pytest",
        "tensorflow",
        "tkinter",
    ],
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
