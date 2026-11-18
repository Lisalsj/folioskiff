# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    get_package_paths,
)


PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
WINDOWS_ROOT = PROJECT_ROOT / "packaging" / "windows"
DIAGNOSTIC_CONSOLE = os.environ.get("PAPER_READER_DIAGNOSTIC_CONSOLE") == "1"


def resource_tree(relative_source, destination, suffixes):
    source_root = SOURCE_ROOT / relative_source
    result = []
    for source in sorted(source_root.rglob("*")):
        if (
            source.is_file()
            and "__pycache__" not in source.parts
            and source.suffix.casefold() in suffixes
        ):
            relative_parent = source.parent.relative_to(source_root).as_posix()
            target = destination if relative_parent == "." else f"{destination}/{relative_parent}"
            result.append((str(source), target))
    return result


datas = []
datas += resource_tree(
    "paper_reader/static",
    "paper_reader/static",
    {".css", ".html", ".js", ".json", ".mjs", ".svg", ".woff", ".woff2"},
)
datas += resource_tree(
    "paper_reader/migrations",
    "paper_reader/migrations",
    {".mako", ".py"},
)
datas += resource_tree(
    "paper_reader/data",
    "paper_reader/data",
    {".json", ".md", ".txt"},
)
datas += resource_tree(
    "paper_reader/publishing/assets",
    "paper_reader/publishing/assets",
    {".yml", ".yaml"},
)
datas += collect_data_files("alembic")
datas += collect_data_files("rfc3987_syntax")
datas += collect_data_files("tzdata")
datas += collect_data_files("fastembed", excludes=["**/*.onnx", "**/*.pt", "**/*.pth"])
_, rapidocr_root = get_package_paths("rapidocr")
datas += [
    (str(Path(rapidocr_root) / "config.yaml"), "rapidocr"),
    (str(Path(rapidocr_root) / "default_models.yaml"), "rapidocr"),
]

binaries = []
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("sqlite_vec")

hiddenimports = []
hiddenimports += collect_submodules("keyring.backends")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastembed")
hiddenimports += collect_submodules("sqlite_vec")
hiddenimports += [
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    "rapidocr",
    "rapidocr.inference_engine.onnxruntime",
    "rapidocr.inference_engine.onnxruntime.main",
    "rapidocr.inference_engine.onnxruntime.provider_config",
]

excluded_modules = [
    "IPython",
    "jupyter",
    "matplotlib",
    "pip",
    "pytest",
    "setuptools",
    "tensorflow",
    "tests",
    "torch",
]

analysis = Analysis(
    [str(SOURCE_ROOT / "paper_reader/cli.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="paper-reader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=DIAGNOSTIC_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=str(WINDOWS_ROOT / "paper-reader.ico"),
    version=str(WINDOWS_ROOT / "version-info.txt"),
    contents_directory="_internal",
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="paper-reader",
)
