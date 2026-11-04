# ui/window_api.py
"""
Windows-specific webview window API.

Provides the JS-bridge class (WindowAPI) that handles frameless-window
controls (minimize / maximize / restore / close) and native drag/resize
via WM_NCLBUTTONDOWN, as well as the _setup_window callback used by
webview.start().
"""
import sys
import time
import threading
from pathlib import Path

from app_runtime import RELEASE_ICON_FILE, resource_path


class WindowAPI:
    """
    JS API for custom title bar controls.
    All _private attributes are skipped by pywebview's JS-bridge introspection.
    """

    def __init__(self):
        self._window      = None
        self._hwnd        = None   # cached after _setup_window runs
        self._maximized   = False
        self._normal_rect = None   # (x, y, w, h) saved before maximizing
        self._animating   = False  # guard against overlapping animations

    # ── Title-bar controls ──────────────────────────────────────

    def minimize(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._maximized:
            self._do_animate_restore()
            self._maximized = False
        else:
            self._do_animate_maximize()
            self._maximized = True

    def close(self):
        self._window.destroy()

    def exportConversation(self, cid):
        if not self._window:
            return {"ok": False, "error": "Desktop window is not ready yet."}

        try:
            from web_app import export_conversation_markdown, suggest_export_filename
            import webview
        except Exception as exc:
            return {"ok": False, "error": f"Export bridge unavailable: {exc}"}

        try:
            selected = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory=self._default_export_directory(),
                save_filename=suggest_export_filename(int(cid)),
                file_types=("Markdown files (*.md)",),
            )
        except Exception as exc:
            return {"ok": False, "error": f"Unable to open save dialog: {exc}"}

        if not selected:
            return {"ok": False, "cancelled": True}

        save_path = selected[0] if isinstance(selected, (list, tuple)) else str(selected)
        written_path = export_conversation_markdown(int(cid), save_path=save_path)
        if not written_path:
            return {"ok": False, "error": "Conversation export failed."}

        return {"ok": True, "path": written_path}

    def _default_export_directory(self) -> str:
        documents_dir = Path.home() / "Documents"
        if documents_dir.exists():
            return str(documents_dir)
        return str(Path.home())

    # ── Animated maximize / restore (Windows only) ───────────────

    def _work_area(self):
        """Return (x, y, w, h) of the monitor work area (excl. taskbar)."""
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ('cbSize',    ctypes.c_ulong),
                ('rcMonitor', wintypes.RECT),
                ('rcWork',    wintypes.RECT),
                ('dwFlags',   ctypes.c_ulong),
            ]

        MONITOR_DEFAULTTONEAREST = 2
        hmon = ctypes.windll.user32.MonitorFromWindow(
            self._hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcWork
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def _current_rect(self):
        """Return current (x, y, w, h) of the window."""
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def _animate_to(self, start, end, duration=0.22):
        """Interpolate window rect from *start* to *end* over *duration* seconds.

        Time-based animation: easing is driven by wall-clock elapsed time so
        the animation always completes in exactly *duration* seconds regardless
        of frame-rate fluctuations.  DwmFlush() paces each frame to the next
        DWM composition event instead of using an imprecise time.sleep()."""
        import ctypes
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        SWP_BASE = 0x0004 | 0x0010   # SWP_NOZORDER | SWP_NOACTIVATE

        t_start = time.perf_counter()
        while True:
            elapsed = time.perf_counter() - t_start
            if elapsed >= duration:
                break
            progress = elapsed / duration
            t = 1.0 - (1.0 - progress) ** 3   # ease-out cubic
            x = int(start[0] + (end[0] - start[0]) * t)
            y = int(start[1] + (end[1] - start[1]) * t)
            w = int(start[2] + (end[2] - start[2]) * t)
            h = int(start[3] + (end[3] - start[3]) * t)
            user32.SetWindowPos(self._hwnd, 0, x, y, w, h, SWP_BASE)
            dwmapi.DwmFlush()

        # Final frame: land exactly on the target rect
        user32.SetWindowPos(
            self._hwnd, 0, end[0], end[1], end[2], end[3], SWP_BASE)
        self._animating = False

    def _do_animate_maximize(self):
        if sys.platform != 'win32' or not self._hwnd:
            self._window.maximize()
            return
        if self._animating:
            return
        self._animating   = True
        self._normal_rect = self._current_rect()
        threading.Thread(
            target=self._animate_to,
            args=(self._normal_rect, self._work_area()),
            daemon=True,
        ).start()

    def _do_animate_restore(self):
        if sys.platform != 'win32' or not self._hwnd:
            self._window.restore()
            return
        if self._animating or self._normal_rect is None:
            return
        self._animating = True
        threading.Thread(
            target=self._animate_to,
            args=(self._current_rect(), self._normal_rect),
            daemon=True,
        ).start()

    # ── Native drag / resize  (Windows — WM_NCLBUTTONDOWN) ──────
    # These are called from JS edge/titlebar handlers.
    # We post WM_NCLBUTTONDOWN directly to the Form so Windows
    # handles the resize/drag loop natively, bypassing the fact
    # that WebView2 covers the entire client area.

    def _post_nc(self, ht):
        """Post WM_NCLBUTTONDOWN with the given hit-test code."""
        if sys.platform != 'win32' or not self._hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes
            class _PT(ctypes.Structure):
                _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
            pt = _PT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            ctypes.windll.user32.ReleaseCapture()
            lp = ctypes.c_long((pt.y << 16) | (pt.x & 0xFFFF)).value
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x00A1, ht, lp)
        except Exception:
            pass

    def start_drag(self):
        """Move the window by polling cursor delta in a background thread.
        Bypasses DefWindowProc / WM_SYSCOMMAND entirely — those paths are
        blocked on frameless windows without WS_CAPTION."""
        if sys.platform != 'win32' or not self._hwnd:
            return
        import ctypes
        user32 = ctypes.windll.user32
        hwnd   = self._hwnd

        class _PT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

        def _loop():
            pt0   = _PT()
            rect0 = ctypes.wintypes.RECT()
            user32.GetCursorPos(ctypes.byref(pt0))
            user32.GetWindowRect(hwnd, ctypes.byref(rect0))
            wx0, wy0 = rect0.left, rect0.top

            SWP = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
            while user32.GetAsyncKeyState(0x01) & 0x8000:   # VK_LBUTTON held
                pt = _PT()
                user32.GetCursorPos(ctypes.byref(pt))
                dx, dy = pt.x - pt0.x, pt.y - pt0.y
                if dx or dy:
                    user32.SetWindowPos(hwnd, 0, wx0 + dx, wy0 + dy, 0, 0, SWP)
                time.sleep(0.008)   # ~120 fps

        threading.Thread(target=_loop, daemon=True).start()

    def start_resize(self, direction):
        _HT = {'w':10,'e':11,'n':12,'nw':13,'ne':14,'s':15,'sw':16,'se':17}
        ht  = _HT.get(str(direction).lower())
        if ht:
            self._post_nc(ht)


def setup_window(api: WindowAPI) -> None:
    """
    Runs in a background thread via webview.start(func=...).

    What this does:
      1. Find the HWND by process-id (reliable even if title changes).
      2. Add WS_THICKFRAME so PostMessage(WM_NCLBUTTONDOWN, HTLEFT/…)
         is honoured by Windows for resize (without WS_THICKFRAME the
         Form ignores resize non-client messages).
      3. Subclass WM_NCHITTEST — handles the few cases where the Form
         (not WebView2) receives the hit-test (e.g. the thick-frame
         non-client border); returns HTLEFT/… for border areas and
         HTCLIENT everywhere else so WebView2 gets normal events.
      4. Apply Windows 11 DWM rounded corners.

    Drag  → CSS -webkit-app-region:drag on .titlebar-drag  (WebView2
            returns HTCAPTION → OS drags) + JS mousedown fallback.
    Resize → JS detects edges, calls api.start_resize(dir) which
            posts WM_NCLBUTTONDOWN to the Form.
    """
    if sys.platform != 'win32':
        return
    time.sleep(0.5)
    try:
        import ctypes
        import os
        from ctypes import wintypes

        user32  = ctypes.windll.user32
        dwmapi  = ctypes.windll.dwmapi

        # ── Find HWND by process id ────────────────────────────
        my_pid = os.getpid()
        _found = [0]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == my_pid:
                _found[0] = hwnd
                return False        # stop after first visible window
            return True

        user32.EnumWindows(_enum, 0)
        hwnd = _found[0] or user32.FindWindowW(None, 'RailGPT')
        if not hwnd:
            return

        api._hwnd = hwnd           # share with WindowAPI
        _set_window_icons(hwnd)

        # ── Windows 11 DWM rounded corners ────────────────────
        try:
            v = ctypes.c_int(2)    # DWMWCP_ROUND
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

        # ── Add WS_THICKFRAME ──────────────────────────────────
        # Required so WM_NCLBUTTONDOWN resize messages are honoured.
        WS_THICKFRAME   = 0x00040000
        GWL_STYLE       = -16
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
        # Notify Windows the frame changed
        SWP_FLAGS = 0x0037         # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)

        # ── WM_NCHITTEST subclass on the Form ──────────────────
        # Covers non-client areas that WebView2 does NOT intercept
        # (e.g. the WS_THICKFRAME border).  For client-area pixels
        # WebView2 handles WM_NCHITTEST itself; the CSS drag region
        # on .titlebar-drag makes WebView2 return HTCAPTION there.
        WM_NCHITTEST  = 0x0084
        GWL_WNDPROC   = -4
        BORDER        = 6
        TITLE_H       = 38
        BTN_W         = 120
        HTCLIENT=1; HTCAPTION=2; HTLEFT=10; HTRIGHT=11; HTTOP=12
        HTTOPLEFT=13; HTTOPRIGHT=14; HTBOTTOM=15; HTBOTTOMLEFT=16; HTBOTTOMRIGHT=17

        if sys.maxsize > 2 ** 32:
            _LP  = ctypes.c_longlong
            _SWL = user32.SetWindowLongPtrW
        else:
            _LP  = ctypes.c_long
            _SWL = user32.SetWindowLongW
        _SWL.restype    = _LP
        _SWL.argtypes   = [wintypes.HWND, ctypes.c_int, _LP]
        user32.CallWindowProcW.restype  = _LP
        user32.CallWindowProcW.argtypes = [_LP, wintypes.HWND,
                                           wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        WNDPROC = ctypes.WINFUNCTYPE(
            _LP, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class _RECT(ctypes.Structure):
            _fields_ = [('left',ctypes.c_long),('top',ctypes.c_long),
                         ('right',ctypes.c_long),('bottom',ctypes.c_long)]
        _prev = [0]

        def _wndproc(h, msg, wp, lp):
            if msg == 0x00A1:  # WM_NCLBUTTONDOWN — used by resize
                return user32.DefWindowProcW(h, msg, wp, lp)
            if msg == WM_NCHITTEST:
                x = ctypes.c_short( lp        & 0xFFFF).value
                y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                rc = _RECT()
                user32.GetWindowRect(h, ctypes.byref(rc))
                rx = x - rc.left;  ry = y - rc.top
                rw = rc.right - rc.left;  rh = rc.bottom - rc.top
                L = rx < BORDER;  R = rx > rw - BORDER
                T = ry < BORDER;  B = ry > rh - BORDER
                if T and L: return HTTOPLEFT
                if T and R: return HTTOPRIGHT
                if B and L: return HTBOTTOMLEFT
                if B and R: return HTBOTTOMRIGHT
                if L: return HTLEFT
                if R: return HTRIGHT
                if T: return HTTOP
                if B: return HTBOTTOM
                if ry < TITLE_H:
                    return HTCLIENT if rx > rw - BTN_W else HTCAPTION
                return HTCLIENT
            # All other messages → pywebview chain
            return user32.CallWindowProcW(_prev[0], h, msg, wp, lp)

        _ref     = WNDPROC(_wndproc)
        _ref_ptr = ctypes.cast(_ref, ctypes.c_void_p).value
        _prev[0] = _SWL(hwnd, GWL_WNDPROC, _ref_ptr)
        api._wndproc_ref  = _ref   # prevent GC
        api._wndproc_prev = _prev

    except Exception as exc:
        print(f'[window setup] {exc}')


def _set_window_icons(hwnd) -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        ICON_SMALL = 0
        ICON_BIG = 1
        WM_SETICON = 0x0080

        icon_path = resource_path(RELEASE_ICON_FILE)
        big_icon = ctypes.windll.user32.LoadImageW(
            None,
            icon_path,
            IMAGE_ICON,
            256,
            256,
            LR_LOADFROMFILE,
        )
        small_icon = ctypes.windll.user32.LoadImageW(
            None,
            icon_path,
            IMAGE_ICON,
            32,
            32,
            LR_LOADFROMFILE,
        )

        if big_icon:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
        if small_icon:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
    except Exception:
        pass
