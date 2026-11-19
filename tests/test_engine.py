"""Inspection and motion run in the installed Godot; CI requires one."""

import os
import tempfile
import unittest
from pathlib import Path

from test_support import textured_cubes

from godot_mcp import config, inspection, motion


@unittest.skipUnless(
    config.locate(config.GODOT) or os.environ.get("CI"), "Godot is not installed"
)
class EngineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "asset.glb").write_bytes(textured_cubes(2, slide="Slide"))

    def test_inspection_reports_the_imported_scene(self):
        result = inspection.inspect_asset(self.root, "asset.glb", "inspected")
        report = result["report"]
        self.assertEqual(report["godot_version"]["major"], 4)
        self.assertEqual(
            report["clips"],
            [
                {
                    "duration": 1.0,
                    "name": "Slide",
                    "player": "/subject/AnimationPlayer",
                    "tracks": 1,
                }
            ],
        )
        surfaces = {s["node"]: s for s in report["surfaces"]}
        self.assertEqual(set(surfaces), {"/subject/Cube0", "/subject/Cube1"})
        for surface in surfaces.values():
            self.assertEqual(
                (surface["vertices"], surface["triangles"], surface["uv0"]),
                (24, 12, True),
            )
        nodes = {n["path"]: n for n in report["nodes"]}
        self.assertAlmostEqual(
            nodes["/subject/Cube1"]["world_transform"]["origin"][0], 1.4, places=5
        )
        output = self.root / "inspected"
        self.assertTrue((output / "receipt.json").is_file())
        self.assertFalse((output / ".inspection.pending").exists())

    def test_motion_samples_the_clip(self):
        result = motion.inspect_motion(
            self.root,
            "asset.glb",
            "motion",
            "/subject/AnimationPlayer",
            "Slide",
            [0, 0.5, 1],
        )
        report = result["report"]
        self.assertEqual(report["duration"], 1.0)
        index = [n["path"] for n in report["nodes"]].index("/subject/Cube0")
        self.assertEqual(
            [
                round(s["nodes"][index]["local_transform"]["origin"][0], 5)
                for s in report["samples"]
            ],
            [0.0, 0.5, 1.0],
        )
        output = self.root / "motion"
        self.assertTrue((output / "receipt.json").is_file())
        self.assertFalse((output / ".motion.pending").exists())


if __name__ == "__main__":
    unittest.main()
