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
