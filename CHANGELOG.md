# Changelog

## 0.1.0 - 2026-11-16

### Added

- `godot_asset_inspect` reports scene nodes, mesh surfaces, materials, UV presence, skeletons and animation clips for a self-contained GLB 2.0 file up to 64 MiB.
- `godot_motion_inspect` samples node and skeleton transforms of one clip at 2–9 normalized times.
- `godot_review_prepare`, `godot_review_capture` and `godot_review_inspect` prepare a front, three-quarter or side view or a selected animation pose, capture it through a Godot Web export, and check source, tool and artifact hashes.
- `godot_status` reports whether each review dependency is configured without launching it.
- A release workflow that publishes `cavi-godot-mcp` to PyPI.
