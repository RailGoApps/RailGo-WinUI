# RailGPT v3.0.0 / DeepSeek Flash local upgrade

The base is RailGo PR #30 at `97161e59a7c8407e47da07106ce7f3d211e8930e`.
The imported standalone version is RailGPT `ecda19473ecb9b2786855b6ba0ba8803832e8e92`.
These changes upgrade the RailGPT integration in PR #30 to v3.0.0 with DeepSeek Flash defaults.

## Application changes

- Import v3 provider/model settings, provider-specific request parameters and the model configuration Web UI.
- Default all three modes, including the Thinking client, to `deepseek-flash`. FAST-GO disables thinking; FAST-PLUS and DEEP retain high reasoning settings.
- Settings schema 3 translates legacy DeepSeek default model IDs on read. Existing ciphertext stays unchanged. A normal user save persists the upgraded format. Explicit custom provider models remain unchanged.
- Keep the RailGo `server_entry.py`, embedded body/CSS, native conversation notifications, request IDs, busy notifications, deep links, host status and shutdown endpoints.
- Keep the host's writable-directory support, atomic history-index writes and station/train Named Pipe preselection.
- Include v3 icon resources and initialize the writable directory before importing backend objects.
- Use release metadata for railway HTTP client version headers.
- Bundle Marked 18.1.3 and Highlight.js 11.9.0 with licenses. Chat initialization and syntax themes no longer wait for a CDN.
- Preserve the existing Python 3.12 release constraint set and packaging pipeline.

## Validation

The full Python test run executes 412 tests: 408 pass and 4 optional legacy Qt tests are skipped. The test launcher calls `prepare_runtime()` before discovery, keeps resource tests relative to the source checkout, and uses an absolute launcher filename for pywebview imports.

Six migration/integration tests cover old model IDs, ciphertext preservation, custom models, embedded status and the existing host-station adapter. The adapter test uses a mocked host response; it is not a real Named Pipe integration run.

Four real DeepSeek chat requests finish with SSE `done`. First answer times: 7.047 s, 8.321 s, 9.255 s and 4.963 s. Complete times: 7.361 s, 8.681 s, 9.407 s and 6.391 s. These are small local samples, not a controlled model benchmark.

The browser check blocks external requests, loads settings and embedded mode, confirms three `deepseek-flash` model fields, verifies hidden native-duplicate navigation, and reports no JavaScript page errors.

On 2026-11-04, .NET SDK 8.0.427 and a dedicated Python 3.12.13 environment were installed locally. The x64 unpackaged Debug build completed with 0 errors and 334 warnings. RailGo started with a responding native window, launched its Python backend, and reported RailGPT 3.0.0 ready. A real Named Pipe `station.search` request for Beijing returned 9 results. Closing the native window stopped its backend launcher and worker and released port 5033. Logs are under `artifacts/dev-setup/`; local setup instructions are in `docs/Local-Development.md`.

A release Runtime executable and MSIX have not been generated. The earlier model measurements used the application's text fallback because their temporary test environment lacked sentence-transformers. The new development environment includes sentence-transformers 5.3.0 and CPU PyTorch 2.13.0; imports and a matrix multiplication were verified. This does not establish that an embedding model cache is populated.

## Context and future DeepSeek Harness work

DeepSeek currently documents 1M-token context for Flash and Pro. RailGPT retains its existing per-mode message/character selection. Expanding this requires token accounting, output reservation and changes to tool-result selection; this upgrade does not silently increase all prompt sizes.

Potential future work is to host the model/tool loop in DeepSeek Harness, register railway tools as plugins, preserve local caches and query-frequency controls, and adapt session events to RailGo's native history and WebView2 interface. dsh has not been installed or integrated in this change.

Official references:

- https://api-docs.deepseek.com/quick_start/pricing/
- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md

## Running the backend

Use the declared Python requirements, then start `RailGPT/server_entry.py` with `RAILGPT_DATA_ROOT`, `RAILGPT_HOST=127.0.0.1` and an available `RAILGPT_PORT`. Release builds still use `packaging/build-railgpt-runtime.ps1` and `packaging/test-railgpt-runtime.ps1`.

The WinUI settings page retains its existing DeepSeek key controls; the Web settings UI supplies the imported v3 provider/model controls. OpenAI and custom provider calls are covered by protocol tests but were not live-tested with paid accounts.
