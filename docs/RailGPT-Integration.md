# RailGPT integration

RailGo-WinUI is the single desktop host. The RailGPT Flask frontend is kept
inside `RailGPT/` and is shown in WebView2 only after the user opens the
RailGPT navigation item.

## Development

The host first looks for `RailGPT.Runtime.exe`. If it is not available, it
falls back to `RailGPT/server_entry.py` and a local Python installation. The
development fallback uses port 5033 by default and exposes `/api/status`.

To build the self-contained runtime on a machine with Python and PyInstaller:

```powershell
python -m pip install -r RailGPT/requirements.txt
python -m pip install pyinstaller
.\packaging\build-railgpt-runtime.ps1 -Python python
```

The resulting `RailGPT/RailGPT.Runtime.exe` is ignored by Git and copied into
the RailGo output/publish directory by the RailGo project.

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

RailGo opens the frontend with `/?embedded=1&conversation=<id>`. Embedded mode
hides RailGPT's own sidebar and title bar; conversation navigation is owned by
the WinUI shell. The frontend can request a native page with:

```js
window.openRailGo("railgo://train/G3089");
```

The host currently maps train deep links to RailGo's native train lookup page.

## Release checks

- Build `RailGPT.Runtime.exe` before creating a release package.
- Verify the output contains the executable and `RailGPT/templates` and
  `RailGPT/static` resources.
- Test first-entry startup on a machine without Python.
- Do not commit model caches, databases, conversations, `__pycache__`, or
  PyInstaller build artifacts.
