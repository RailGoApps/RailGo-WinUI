# Railway AI Agent Changelog

## v3.0.0 - 2026-08-17

### Added
- Multi-provider LLM settings for DeepSeek, OpenAI, and custom OpenAI-compatible services.
- Independent model selection for FAST-GO, FAST-PLUS, and DEEP, with editable model IDs.
- Windows DPAPI-protected primary and optional thinker API-key slots, including schema-v1 migration.
- Professional Windows portable and Inno Setup build entry points.
- GitHub Actions CI and tag-driven Windows release workflow.

### Changed
- Unified application, desktop, installer, and release metadata under the `3.0.0` version.
- DeepSeek thinking, OpenAI reasoning, and generic-compatible request parameters now follow provider-specific compatibility rules.
- Settings changes lazily refresh LLM clients for subsequent requests without requiring an application restart.

### Compatibility
- Existing local settings, conversations, Railway SQLite databases, RailGo cache, and DPAPI data remain compatible.
- API keys and per-user runtime data remain outside Git and installers.

## v2.6 (Public Preview - Single User)

### Added
- New tool: station_to_station (s2s) query
- SQLite persistent cache for s2s_route table
- S2S → TrainPath injection for locality optimization
- Router prompt upgraded to select tools correctly

### Fixed
- Station telecode normalization bug (3-char Chinese mis-detection)
- Duplicate query suppression in Executor

### Notes
- v2.6 is limited to **1-person closed beta**
- No API rate limiting yet (safe due to controlled usage)

### Planned (v2.7)
- Add API miss rate-limit + per-session query budget
- Prevent user-driven OD crawling behavior
