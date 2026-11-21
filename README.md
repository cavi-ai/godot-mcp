# Cavi Godot MCP

## Take it for a spin.

![A frog king on a turntable, inspected by an otter curator](assets/godot-mcp-hero.png)

See your GLB as Godot imports it. Cavi Godot MCP helps your assistant inspect meshes, materials, skeletons and animation clips, then prepare a rendered view you can review together. Orbit a static asset or capture a selected animation pose without building a review scene by hand.

Try asking:

> Inspect my GLB in Godot. What meshes, materials and animation clips did it import? Prepare a three-quarter view so I can look it over.

## Set up

Native inspection and capture require macOS or Linux, Python 3.15 or newer, [uv](https://docs.astral.sh/uv/), a Godot editor and an existing workspace. Install the server:

```sh
uv tool install cavi-godot-mcp
```

Configure your MCP client with the executable and the absolute workspace path:

```json
{
  "mcpServers": {
    "godot": {
      "command": "cavi-godot-mcp",
      "env": {
        "CAVI_GAME_ART_WORKSPACE": "/absolute/path/to/workspace"
      }
    }
  }
}
```

Godot, Chromium and Node.js are found where they are installed: `/Applications/Godot.app`, `/Applications/Google Chrome.app` and `/opt/homebrew/bin/node`, then `godot`, `chromium` and `node` on `PATH`. Capture uses the single-threaded Web template (`web_nothreads_release.zip`) for the editor's version from Godot's export templates directory; install the export templates from the Godot editor. Capture needs Chromium with WebGL 2 support and Node.js 22 or newer.

To use other files, set `CAVI_GODOT_BIN`, `CAVI_GODOT_WEB_TEMPLATE`, `CAVI_CHROMIUM_BIN` or `CAVI_NODE_BIN` to an absolute path. A set variable always wins: when its file is missing, that tool is reported as not found. `godot_status` reports which tools are found.

## Review your asset

- Inspect a self-contained GLB for scene nodes, mesh surfaces, materials, UV presence, skeletons and clips.
- Sample an animation's node and skeleton transforms at selected times.
- Prepare and capture a static view, including texture-free and vertex-colored drafts before cleanup, or capture one selected pose from an animated GLB. Open the capture for visual review.
- Serve the exported review bundle locally to orbit and zoom; press R to reset the camera. A white bar on the asset's bottom front edge is the largest power of ten that fits it, such as 1 cm or 1 m, and the label names its length. Pose reviews open paused at the requested time. Play or pause the selected clip, scrub the timeline, or select Reset pose.

<details>
<summary>Operations, limits and recovery</summary>

`godot_asset_inspect` takes a workspace-relative `source` and a new workspace-relative `directory`. It accepts self-contained GLB 2.0 files up to 64 MiB. Its mesh bounds describe the imported rest pose; reports include totals and truncation flags for capped records. The report's `qa` section counts every drawn mesh instance, including capped ones: triangles, vertices, zero-area triangles (under 1e-12 m²), surfaces with UV0 outside 0–1, unique materials and textures, texture memory as uncompressed RGBA8 with mipmaps, the largest texture side, non-power-of-two textures, bone weights per vertex (4 or 8), vertices with all skin weights zero, vertices whose weights do not sum to 1, the most blend shapes on one mesh, mirrored and non-uniformly scaled meshes, animation keys after Godot's import resampling, and the scene's rest-pose bounds. Each BaseMaterial3D records albedo, metallic, roughness, emission, emission energy, normal scale, transparency, cull and shading; each clip records loop mode and track-type counts. The report lists KHR_materials_* extensions Godot 4.7 does not map onto the imported material (clearcoat, transmission, volume, iridescence, sheen, anisotropy, specular, variants, and the rest outside unlit, emissive strength, and specular-glossiness). Pass `budget` with upper limits on any of these counts, `draw_calls` (surfaces), `bones`, or `base_offset`, the distance in metres from the scene origin to the bottom centre of the bounds; the result and receipt record each limit, its measured value and whether every limit is met. Inspection uses Godot's headless runtime importer and does not require a browser or Web template.

For motion, use the inspection report's player path and clip name with `godot_motion_inspect`. Supply 2–9 strictly increasing `normalized_times` between 0 and 1. The result measures node and skeleton transforms. It also records each mesh's blend-shape weights and, for skinned meshes, the CPU-skinned bounds at the requested times. It does not dump vertices. The report's `qa` section samples the whole clip evenly at 30 frames per second or finer, up to 1,801 frames, whatever times you request: the largest position and rotation difference between the first and last frame (`loop_seam`), the fastest world-space movement and the fastest local rotation of any node or bone with its name, the world displacement of top-level nodes and root bones over the clip (`root_motion`), the largest change in skinned bounds size from the rest pose, and the largest blend-shape weight gap between the first and last frame.

For a static review, `godot_review_prepare` takes the source, a new directory and `front`, `three-quarter` or `side` view. The three-quarter view is 45° between front and side, and the camera distance fits the asset's bounding sphere to the 45° field of view at any size. Static inputs cannot contain skins or animations. Textures are optional; any images must be embedded PNG or JPEG. Geometry-only static triangles without normals or materials use neutral shading for review. To review an animated asset, inspect it first and provide a selected `pose` with the reported player path, animation name and time in seconds. A pose review frames the skinned meshes as deformed at that time. For matched comparisons, capture the larger asset first, then pass its returned `scene.camera_frame` as `framing` for the other asset at the same view. For static reviews, set `all_views` to capture all three angles in one session; the selected view remains `capture.png`, and the other angles use `capture-front.png`, `capture-three-quarter.png` or `capture-side.png`. `godot_review_capture` produces the frames, and `godot_review_inspect` checks source, tool and artifact hashes and reports the latest attempt. Captures light the asset with a neutral grey sky that metals reflect and use AgX tonemapping; `scene.renderer` records Godot's rendering method, driver and adapter.

To open the interactive view, serve the receipt's relative `bundle` within the review directory:

```sh
python3 -m http.server --bind 127.0.0.1 --directory <review-directory>/<bundle>
```

Stop the server after review. Godot's headless mode does not render; capture uses a Godot Web export with the Compatibility renderer and WebGL 2. The export is built once per review runtime, Godot editor and Web template and kept in the workspace's `.godot-review-runtime` directory. Each review bundle serves its `subject.glb` and `selection.json` beside the export, and the scene loads the GLB with Godot's runtime glTF importer, as inspection does. The review does not establish gameplay, collision, traversal, room architecture or appearance in another engine. Perform final acceptance in the consuming engine.

If inspection fails, preserve its output directory and inspect the receipt and process logs before retrying. If a capture has an unknown outcome, use `godot_review_inspect` to examine the latest attempt. A pending cleanup marker blocks another capture; resolve the owned processes and output before removing it.

</details>

## Security

The server runs locally over MCP stdio. Report security concerns privately as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
