import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import glb, missing_tools

from godot_mcp import inspection


class InspectionTests(unittest.TestCase):
    def test_animated_untextured_input_is_accepted_for_inspection(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "asset.glb"
            p.write_bytes(
                glb(
                    {
                        "skins": [{}],
                        "animations": [{}],
                        "images": [],
                        "textures": [],
                        "materials": [],
                    }
                )
            )
            inspection.validate_glb(p)

    def test_external_buffer_or_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "asset.glb"
            for extra in (
                {"buffers": [{"byteLength": 1, "uri": "outside.bin"}]},
                {"images": [{"uri": "outside.png"}]},
            ):
                p.write_bytes(glb(extra))
                with self.assertRaises(ValueError):
                    inspection.validate_glb(p)

    def test_missing_engine_and_escape_are_rejected_before_output(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(os.environ, missing_tools(), clear=True),
        ):
            root = Path(d)
            (root / "asset.glb").write_bytes(glb({}))
            for source in ("asset.glb", "../outside.glb"):
                with self.assertRaises(ValueError):
                    inspection.inspect_asset(root, source, "result")
                self.assertFalse((root / "result").exists())

    def test_unknown_cleanup_keeps_pending_and_blocks_directory_reuse(self):
        from godot_mcp import review

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(glb({}))
            engine = root / "engine"
            engine.write_bytes(b"engine")
            with (
                patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                patch.object(
                    review, "run_owned", side_effect=review.CleanupUnverified("unknown")
                ),
            ):
                with self.assertRaises(review.CleanupUnverified):
                    inspection.inspect_asset(root, "asset.glb", "result")
                self.assertTrue((root / "result" / ".inspection.pending").exists())
                with self.assertRaises(FileExistsError):
                    inspection.inspect_asset(root, "asset.glb", "result")

    def test_marker_failure_after_directory_creation_is_unknown(self):
        from cavi_mcp_core.contracts import OperationUnknown

        from godot_mcp import review

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(glb({}))
            engine = root / "engine"
            engine.write_bytes(b"engine")
            with (
                patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                patch.object(
                    review, "atomic_json", side_effect=OSError("write failed")
                ),
            ):
                with self.assertRaises(OperationUnknown):
                    inspection.inspect_asset(root, "asset.glb", "result")
                self.assertTrue((root / "result").is_dir())

    def test_engine_report_is_validated_before_receipt(self):
        import json

        from cavi_mcp_core.contracts import OperationUnknown

        from godot_mcp import review

        t = {"origin": [0, 0, 0], "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        b = {"position": [0, 0, 0], "size": [1, 1, 0]}
        surface = {
            "node": "/Scene/Mesh",
            "surface": 0,
            "primitive": 3,
            "vertices": 3,
            "triangles": 1,
            "degenerate_triangles": 0,
            "uv0": False,
            "uv0_outside_unit": False,
            "uv1": False,
            "material": {
                "name": "",
                "type": "StandardMaterial3D",
                "textures": {"base_color": {"name": "", "width": 1, "height": 1}},
                "albedo": [1, 1, 1, 1],
                "metallic": 0,
                "roughness": 1,
                "emission": [0, 0, 0, 1],
                "emission_energy": 1,
                "normal_scale": 1,
                "transparency": 0,
                "cull": 0,
                "shading": 0,
            },
        }
        report = {
            "status": "ok",
            "godot_version": {"major": 4, "minor": 5, "string": "4.5.stable"},
            "units": "metres",
            "bounds_semantics": "imported rest pose",
            "totals": {
                "nodes": 3,
                "surfaces": 1,
                "bones": 0,
                "clips": 1,
                "unsupported_primitives": 0,
            },
            "qa": {
                "triangles": 1,
                "vertices": 3,
                "degenerate_triangles": 0,
                "uv0_outside_unit_surfaces": 0,
                "materials": 1,
                "textures": 1,
                "texture_rgba8_bytes": 4,
                "max_texture_size": 1,
                "npot_textures": 0,
                "max_bone_influences": 0,
                "zero_weight_vertices": 0,
                "unnormalized_weight_vertices": 0,
                "max_blend_shapes": 0,
                "mirrored_meshes": 0,
                "non_uniform_scale_meshes": 0,
                "animation_keys": 2,
                "bounds": b,
            },
            "truncated": {
                "nodes": False,
                "surfaces": False,
                "bones": False,
                "clips": False,
            },
            "nodes": [
                {
                    "path": "/Scene",
                    "parent": "",
                    "type": "Node3D",
                    "local_transform": t,
                    "world_transform": t,
                },
                {
                    "path": "/Scene/Mesh",
                    "parent": "/Scene",
                    "type": "MeshInstance3D",
                    "local_transform": t,
                    "world_transform": t,
                    "mesh_rest_local_bounds": b,
                    "mesh_rest_world_bounds": b,
                },
                {
                    "path": "/Scene/AnimationPlayer",
                    "parent": "/Scene",
                    "type": "AnimationPlayer",
                },
            ],
            "surfaces": [surface],
            "bones": [],
            "clips": [
                {
                    "player": "/Scene/AnimationPlayer",
                    "name": "Walk",
                    "duration": 1.0,
                    "tracks": 1,
                    "keys": 2,
                    "loop_mode": 0,
                    "track_types": {
                        "value": 0,
                        "position": 1,
                        "rotation": 0,
                        "scale": 0,
                        "blend_shape": 0,
                        "method": 0,
                        "bezier": 0,
                        "audio": 0,
                        "animation": 0,
                    },
                }
            ],
            "dropped_material_extensions": [],
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(glb({}))
            engine = root / "engine"
            engine.write_bytes(b"engine")

            def inspect(value, directory, change=None):
                def run(command, timeout, dest, label):
                    (dest / "godot.stdout").write_bytes(b"")
                    (dest / "godot.stderr").write_bytes(b"")
                    (Path(command[3]) / "report.json").write_text(json.dumps(value))
                    if change:
                        (Path(command[3]) / change).write_bytes(b"changed")

                with (
                    patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                    patch.object(review, "run_owned", side_effect=run),
                ):
                    return inspection.inspect_asset(root, "asset.glb", directory)

            self.assertEqual(inspect(report, "good")["report"], report)
            receipt = json.loads((root / "good" / "receipt.json").read_text())
            self.assertNotIn("report", receipt)
            self.assertEqual(
                receipt["report_sha256"],
                hashlib.sha256(
                    (root / "good" / "report.json").read_bytes()
                ).hexdigest(),
            )
            for change in ("subject.glb", "inspect.gd"):
                with self.subTest(change=change), self.assertRaises(OperationUnknown):
                    inspect(report, "changed-" + change, change)
                self.assertFalse(
                    (root / ("changed-" + change) / "receipt.json").exists()
                )
            for index, bad in enumerate(
                (
                    {**report, "nodes": {}},
                    {**report, "bounds_semantics": "posed"},
                    {**report, "surfaces": [{**surface, "vertices": "3"}]},
                    {**report, "surfaces": [{**surface, "triangles": None}]},
                    {**report, "surfaces": [{**surface, "node": "/Elsewhere"}]},
                    {**report, "nodes": report["nodes"][:1] * 3},
                    {**report, "truncated": {**report["truncated"], "nodes": True}},
                    {**report, "totals": {**report["totals"], "surfaces": 0}},
                    {**report, "clips": [{**report["clips"][0], "duration": -1}]},
                    {**report, "qa": {**report["qa"], "triangles": 2}},
                    {**report, "qa": {**report["qa"], "max_bone_influences": 3}},
                    {**report, "qa": {**report["qa"], "animation_keys": 3}},
                    {**report, "surfaces": [{**surface, "uv0_outside_unit": True}]},
                    {
                        **report,
                        "nodes": [
                            {**report["nodes"][0], "local_transform": {"origin": []}},
                            *report["nodes"][1:],
                        ],
                    },
                )
            ):
                directory = f"bad-{index}"
                with self.subTest(index=index), self.assertRaises(OperationUnknown):
                    inspect(bad, directory)
                self.assertFalse((root / directory / "receipt.json").exists())

    def test_unmapped_material_extensions_are_listed(self):
        import json

        from godot_mcp import review

        report = {
            "status": "ok",
            "godot_version": {"major": 4},
            "units": "metres",
            "bounds_semantics": "imported rest pose",
            "totals": {
                "nodes": 0,
                "surfaces": 0,
                "bones": 0,
                "clips": 0,
                "unsupported_primitives": 0,
            },
            "qa": {
                "triangles": 0,
                "vertices": 0,
                "degenerate_triangles": 0,
                "uv0_outside_unit_surfaces": 0,
                "materials": 0,
                "textures": 0,
                "texture_rgba8_bytes": 0,
                "max_texture_size": 0,
                "npot_textures": 0,
                "max_bone_influences": 0,
                "zero_weight_vertices": 0,
                "unnormalized_weight_vertices": 0,
                "max_blend_shapes": 0,
                "mirrored_meshes": 0,
                "non_uniform_scale_meshes": 0,
                "animation_keys": 0,
                "bounds": None,
            },
            "truncated": {
                "nodes": False,
                "surfaces": False,
                "bones": False,
                "clips": False,
            },
            "nodes": [],
            "surfaces": [],
            "bones": [],
            "clips": [],
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(
                glb(
                    {
                        "extensionsUsed": [
                            "KHR_materials_clearcoat",
                            "KHR_materials_unlit",
                        ],
                        "materials": [
                            {
                                "extensions": {
                                    "KHR_materials_clearcoat": {},
                                    "KHR_materials_unlit": {},
                                }
                            }
                        ],
                    }
                )
            )
            engine = root / "engine"
            engine.write_bytes(b"engine")

            def run(command, timeout, dest, label):
                (dest / "godot.stdout").write_bytes(b"")
                (dest / "godot.stderr").write_bytes(b"")
                (Path(command[3]) / "report.json").write_text(json.dumps(report))

            with (
                patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                patch.object(review, "run_owned", side_effect=run),
            ):
                result = inspection.inspect_asset(root, "asset.glb", "extensions")
            self.assertEqual(
                result["report"]["dropped_material_extensions"],
                ["KHR_materials_clearcoat"],
            )
            saved = json.loads((root / "extensions" / "report.json").read_text())
            self.assertEqual(
                saved["dropped_material_extensions"], ["KHR_materials_clearcoat"]
            )

    def test_unknown_cleanup_preserves_project_and_input_receipt(self):
        import shutil

        from godot_mcp import review

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(glb({}))
            engine = root / "engine"
            engine.write_bytes(b"engine")
            with (
                patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                patch.object(
                    review,
                    "run_owned",
                    side_effect=review.CleanupUnverified("uncertain"),
                ),
                patch.object(shutil, "rmtree", side_effect=OSError("cleanup failed")),
            ):
                with self.assertRaises(review.CleanupUnverified):
                    inspection.inspect_asset(root, "asset.glb", "result")
                result = root / "result"
                self.assertTrue((result / ".inspection.pending").exists())
                self.assertTrue((result / "inputs.json").is_file())
                self.assertEqual(len(list(result.glob(".inspection-*"))), 1)
