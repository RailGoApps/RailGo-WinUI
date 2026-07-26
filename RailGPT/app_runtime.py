from __future__ import annotations

import json
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load_release_metadata() -> dict:
    metadata_path = app_root().joinpath("release_metadata.json")
    with metadata_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    return data


def metadata_value(key: str, default=None):
    return load_release_metadata().get(key, default)


APP_NAME = metadata_value("app_name", "RailGPT")
APP_VERSION = metadata_value("app_version", "2.6.6")
RELEASE_NAME = metadata_value("release_name", f"{APP_NAME}_v{APP_VERSION}")
WINDOW_TITLE = metadata_value("window_title", f"{APP_NAME} v{APP_VERSION}")
INSTALLER_DISPLAY_NAME = metadata_value("installer_display_name", APP_NAME)
DEFAULT_HOST = metadata_value("default_host", "127.0.0.1")
DEFAULT_PORT = int(metadata_value("default_port", 5033))
APP_USER_MODEL_ID = metadata_value("app_user_model_id", f"{APP_NAME}.Desktop")
RELEASE_ICON_FILE = metadata_value("release_icon", "releaseico.ico")
SETUP_ICON_FILE = metadata_value("setup_icon", "setup_icon.ico")
INSTALLER_OUTPUT = metadata_value("installer_output", f"{APP_NAME}_v{APP_VERSION}_Setup_x64")
PORTABLE_OUTPUT = metadata_value("portable_output", f"{APP_NAME}_v{APP_VERSION}_win64")


def resource_path(*parts: str) -> str:
    return str(app_root().joinpath(*parts))


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def writable_path(*parts: str) -> str:
    return str(runtime_root().joinpath(*parts))


def user_data_path(*parts: str) -> str:
    """Return a per-user writable asset path outside the installation folder."""

    appdata_root = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("APPDATA")
        or str(Path.home().joinpath("AppData", "Local"))
    )
    target = Path(appdata_root).joinpath(APP_NAME, *parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


def _seed_runtime_file(relative_path: str):
    source = app_root().joinpath(relative_path)
    target = runtime_root().joinpath(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or not source.exists():
        return
    shutil.copy2(source, target)


def prepare_runtime() -> Path:
    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)

    for relative_path in (
        "rail12306.db",
        "rail_store.db",
        "tools/rail/store/station_dict.json",
        "tools/rail/store/station_dict_meta.json",
    ):
        _seed_runtime_file(relative_path)

    os.chdir(root)
    os.environ.setdefault("RAILGPT_APP_ROOT", str(root))
    os.environ.setdefault("RAILGPT_BUNDLE_ROOT", str(app_root()))
    return root
