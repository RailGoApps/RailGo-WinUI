import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from app_runtime import (
    APP_USER_MODEL_ID,
    DEFAULT_HOST,
    DEFAULT_PORT,
    WINDOW_TITLE,
    prepare_runtime,
)


def _format_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll until Flask is accepting connections, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def _extract_server_port(server) -> int:
    if hasattr(server, "server_port"):
        return int(server.server_port)
    if hasattr(server, "socket") and server.socket:
        return int(server.socket.getsockname()[1])
    raise RuntimeError("Unable to determine runtime port")


def _can_bind(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
        return True
    except OSError:
        return False


def _start_flask(flask_app, host: str = DEFAULT_HOST, preferred_port: int = DEFAULT_PORT):
    """Create a Werkzeug server and serve it on a daemon thread."""
    from werkzeug.serving import make_server as _make_server

    target_port = preferred_port if _can_bind(host, preferred_port) else 0
    try:
        srv = _make_server(host, target_port, flask_app)
        actual_port = preferred_port if target_port else _extract_server_port(srv)
    except (OSError, SystemExit):
        srv = _make_server(host, 0, flask_app)
        actual_port = _extract_server_port(srv)

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, actual_port


def _set_app_user_model_id():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _launch_browser_fallback(url: str):
    print(f"[launch] browser-fallback mode -> {url}")
    webbrowser.open(url, new=1, autoraise=True)


def _launch_desktop_window(url: str):
    import webview
    from window_api import WindowAPI, setup_window

    api = WindowAPI()
    window = webview.create_window(
        title=WINDOW_TITLE,
        url=url,
        width=1200,
        height=800,
        min_size=(800, 600),
        resizable=True,
        frameless=True,
        easy_drag=False,
        js_api=api,
    )
    api._window = window

    print(f"[launch] desktop-app mode -> {url}")
    webview.start(func=lambda *_: setup_window(api))


def main():
    prepare_runtime()
    _set_app_user_model_id()

    from app_init import build_backend

    flask_app = build_backend()
    _, port = _start_flask(flask_app)
    url = _format_url(DEFAULT_HOST, port)

    print("=" * 50)
    print(f"  {WINDOW_TITLE} - Desktop")
    print(f"  Starting on {url}")
    print("=" * 50)

    if not _wait_for_server(url):
        print("ERROR: Flask server did not start in time.")
        return

    try:
        _launch_desktop_window(url)
    except Exception as exc:
        print(f"[launch] pywebview unavailable, falling back to browser: {exc}")
        _launch_browser_fallback(url)
        while True:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
