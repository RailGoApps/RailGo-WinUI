# Railway AI Agent Changelog

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