# Changelog

## Unreleased

### Added

- Every tool declares an output schema for its result.

### Changed

- Requires `cavi-mcp-core>=0.1.4`.
- Source and output paths follow MCP Core's workspace rules.
- Godot, Chromium, Node.js and the Web template are found where they are installed; `CAVI_GODOT_BIN`, `CAVI_GODOT_WEB_TEMPLATE`, `CAVI_CHROMIUM_BIN` and `CAVI_NODE_BIN` override them.
- The inspection receipt records `report_sha256` instead of the report, as the motion receipt does; inspection and motion refuse a run whose copied GLB or script changed.
- A capture's `request.json` records `camera_frame_evidence` for static views.

### Fixed

- Malformed GLB JSON (a non-object document, NaN or non-object entries) is refused before any output is written.
- Pose and motion requests for a missing clip, a time past the clip end or a zero-length clip are refused before Godot runs.
- The Godot inspection report's shape, counts, truncation flags and references are checked before a receipt is written.
- Inspecting a captured review without tool configuration is refused instead of raising `KeyError`.

## 0.1.0 - 2026-11-16

### Added

- `godot_asset_inspect` reports scene nodes, mesh surfaces, materials, UV presence, skeletons and animation clips for a self-contained GLB 2.0 file up to 64 MiB.
- `godot_motion_inspect` samples node and skeleton transforms of one clip at 2–9 normalized times.
- `godot_review_prepare`, `godot_review_capture` and `godot_review_inspect` prepare a front, three-quarter or side view or a selected animation pose, capture it through a Godot Web export, and check source, tool and artifact hashes.
- `godot_status` reports whether each review dependency is configured without launching it.
- A release workflow that publishes `cavi-godot-mcp` to PyPI.
