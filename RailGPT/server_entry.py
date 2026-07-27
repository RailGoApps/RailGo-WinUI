"""RailGo-hosted RailGPT server entry point.

This entry point intentionally starts Flask only.  It never creates a
pywebview window or opens a browser; the WinUI host owns the user interface.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # A frozen console process inherits the legacy Windows code page even
    # when output is redirected by RailGo. Agent startup logs contain Unicode
    # symbols, so normalize streams before importing any RailGPT modules.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")

    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

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
