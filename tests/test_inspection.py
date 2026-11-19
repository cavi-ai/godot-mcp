import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import glb

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
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
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
            "uv0": False,
            "uv1": False,
            "material": {
                "name": "",
                "type": "StandardMaterial3D",
                "textures": {"base_color": {"name": "", "width": 1, "height": 1}},
            },
        }
        report = {
            "status": "ok",
            "godot_version": {"major": 4, "minor": 5, "string": "4.5.stable"},
            "bounds_semantics": "imported rest pose",
            "totals": {
                "nodes": 3,
                "surfaces": 1,
                "bones": 0,
                "clips": 1,
                "unsupported_primitives": 0,
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
                }
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(glb({}))
            engine = root / "engine"
            engine.write_bytes(b"engine")

            def inspect(value, directory):
                def run(command, timeout, dest, label):
                    (dest / "godot.stdout").write_bytes(b"")
                    (dest / "godot.stderr").write_bytes(b"")
                    (Path(command[3]) / "report.json").write_text(json.dumps(value))

                with (
                    patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                    patch.object(review, "run_owned", side_effect=run),
                ):
                    return inspection.inspect_asset(root, "asset.glb", directory)

            self.assertEqual(inspect(report, "good")["report"], report)
            self.assertTrue((root / "good" / "receipt.json").is_file())
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

    def test_unknown_cleanup_preserves_project_and_input_receipt(self):
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
                patch.object(
                    inspection.shutil, "rmtree", side_effect=OSError("cleanup failed")
                ),
            ):
                with self.assertRaises(review.CleanupUnverified):
                    inspection.inspect_asset(root, "asset.glb", "result")
                result = root / "result"
                self.assertTrue((result / ".inspection.pending").exists())
                self.assertTrue((result / "inputs.json").is_file())
                self.assertEqual(len(list(result.glob(".inspection-*"))), 1)
