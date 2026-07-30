"""RailGo-hosted RailGPT server entry point.

This entry point intentionally starts Flask only.  It never creates a
pywebview window or opens a browser; the WinUI host owns the user interface.
"""

from __future__ import annotations

import os
import sys


def _configure_streams() -> None:
    # A frozen console process inherits the legacy Windows code page even
    # when output is redirected by RailGo. Agent startup logs contain Unicode
    # symbols, so normalize streams before importing any RailGPT modules.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _prepare_import_path() -> None:
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _run_self_test() -> int:
    """Verify imports and resources that a shallow HTTP probe cannot cover."""

    import json
    from pathlib import Path
    from zoneinfo import ZoneInfo

    import numpy
    import torch
    from sentence_transformers import SentenceTransformer

    from app_runtime import APP_NAME, APP_VERSION, resource_path

    required_resources = (
        "templates/index.html",
        "static/js/app.js",
        "static/css/styles.css",
        "release_metadata.json",
    )
    missing_resources = [
        relative
        for relative in required_resources
        if not Path(resource_path(*relative.split("/"))).is_file()
    ]
    if missing_resources:
        raise RuntimeError(
            "Missing packaged resources: " + ", ".join(missing_resources)
        )

    if int(numpy.dot([1], [2])) != 2:
        raise RuntimeError("NumPy runtime self-test failed.")
    if int(torch.tensor([1]).sum().item()) != 1:
        raise RuntimeError("Torch runtime self-test failed.")
    if SentenceTransformer.__name__ != "SentenceTransformer":
        raise RuntimeError("sentence-transformers import self-test failed.")

    payload = {
        "service": APP_NAME,
        "version": APP_VERSION,
        "timezone": str(ZoneInfo("Asia/Shanghai")),
        "numpy": numpy.__version__,
        "torch": torch.__version__,
        "sentence_transformers": "imported",
        "resources": "ok",
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    _configure_streams()
    _prepare_import_path()

    if "--self-test" in sys.argv[1:]:
        return _run_self_test()

    # Importing app_init after sys.path is prepared is important for both the
    # source checkout and the PyInstaller bundle.
    from app_init import build_backend
    from werkzeug.serving import run_simple

    host = os.environ.get("RAILGPT_HOST", "127.0.0.1")
    port = int(os.environ.get("RAILGPT_PORT", "5033"))
    app = build_backend()

    run_simple(
        host,
        port,
        app,
        threaded=True,
        use_reloader=False,
        use_debugger=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
