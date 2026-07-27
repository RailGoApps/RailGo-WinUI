# RailGPT integration

RailGo-WinUI is the single desktop host. RailGPT starts asynchronously after
the main RailGo window is activated; the Flask frontend is shown in WebView2
only after the user opens a RailGPT conversation. RailGo's first frame and
native query pages never wait for the AI runtime.

## Development

Release x64 builds use exactly
`AppContext.BaseDirectory\RailGPT\RailGPT.Runtime.exe`. Missing runtime,
unsupported architecture, startup failure, and missing WebView2 are presented
by a native WinUI status page.

Debug builds may run `RailGPT/server_entry.py` only when
`RAILGPT_DEV_PYTHON` explicitly points to a Python executable. RailGo never
scans Anaconda or other machine-specific locations.

To build the self-contained runtime on a machine with Python and PyInstaller:

```powershell
python -m pip install -r RailGPT/requirements-dev.txt
.\packaging\build-railgpt-runtime.ps1 -Python python
```

The build script creates `RailGPT/RailGPT.Runtime.exe` and immediately runs a
random-port smoke test using a temporary data directory. Release x64 builds
fail when the executable is absent. Release output contains only the runtime
and the RailGPT license; PyInstaller embeds the required frontend resources.

## Host bridge

When RailGPT is launched by RailGo, `RAILGO_BRIDGE_PIPE` points to a private
Windows named pipe. Requests are one-line JSON-RPC messages. The bridge uses
the already configured RailGo `QueryService`, so offline, online, and custom
data-source selection remains owned by RailGo.

The current methods are `station.search`, `station.get`, `train.search`,
`train.get`, `route.search`, `train.delay`, `station.board`, `emu.query`, and
`emu.assignment`. Railway tools use the host bridge for station and train
preselection when the environment variable is present, and retain their
standalone HTTP fallback when RailGPT is run independently.

## Embedded frontend contract

RailGo opens the frontend with `/?embedded=1`. Embedded mode hides RailGPT's
own sidebar and title bar; conversation navigation is owned by the WinUI
shell. Conversation changes use request IDs over WebView2 messages, and only
the latest response is accepted. The frontend can request a native page with:

```js
window.openRailGo("railgo://train/G3089");
```

The host maps `train`, `station`, and `route` deep links to RailGo's native
lookup pages.

## Conversations and diagnostics

The shell reads
`%LOCALAPPDATA%\RailGPT\conversations\index.json` without waiting for Flask.
Python writes the index by atomic replacement; RailGo watches it with debounce
and retains the last valid list if a write is interrupted.

Runtime and WebView diagnostics are written to
`%LOCALAPPDATA%\RailGo\Logs\railgpt.log`. Logs rotate at 5 MB and retain five
files. RailGo stops only the Runtime process it created.

## Release checks

- Build `RailGPT.Runtime.exe` before creating a release package.
- Run `.\packaging\test-railgpt-runtime.ps1` after clearing Python from PATH.
- Verify the x64 publish/MSIX contains
  `RailGPT\RailGPT.Runtime.exe` and `RailGPT\LICENSE`.
- Test first-entry startup on a machine without Python.
- Test missing Runtime and missing WebView2 native states.
- Confirm closing RailGo leaves no owned RailGPT process.
- Do not commit model caches, databases, conversations, `__pycache__`, or
  PyInstaller build artifacts.
