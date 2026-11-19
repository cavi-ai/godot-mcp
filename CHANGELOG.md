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
- The review camera distance fits the asset's bounding sphere to the field of view, with no minimum size, and the clip planes follow it; the three-quarter view is at 45°.
- Captured scene metadata records `camera_position` and `camera_clip`; the review scene prints its metadata when run outside a browser.
- The scale bar is the largest power of ten that fits the asset's bottom front edge instead of a fixed 1 m bar in front of it; `scale_bar` records its length and ends.
- Pose reviews frame skinned meshes by their vertices at the selected time instead of bone origins with padding.
- Inspection and motion reports give `units` as `metres`; motion reports replace `Godot scene units`.
- Inspection counts surface vertices, indices and UV sets without copying mesh arrays.

### Fixed

- Inspection reports full-precision numbers; clip durations were rounded to 15 digits and could exceed the clip's length.
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
