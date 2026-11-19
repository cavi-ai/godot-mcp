"""Inspection, motion and the review scene run in the installed Godot; CI requires one."""

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_support import skinned_cube, textured_cubes

from godot_mcp import config, inspection, motion, review

READY = "godot-review-ready "


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

    def test_inspection_reports_the_exact_clip_length_in_metres(self):
        (self.root / "rig.glb").write_bytes(skinned_cube(lift=1.0, duration=11 / 24))
        report = inspection.inspect_asset(self.root, "rig.glb", "rig")["report"]
        length = struct.unpack("<f", struct.pack("<f", 11 / 24))[0]
        self.assertEqual([clip["duration"] for clip in report["clips"]], [length])
        self.assertEqual(report["units"], "metres")

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
        self.assertEqual(report["units"], "metres")
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

    def review_scene(self, source, view, pose=None):
        """The metadata the review scene reports when run headless on ``source``."""
        project = self.root / "review"
        project.mkdir()
        runtime = Path(review.__file__).parent / "runtime"
        for name in ("project.godot", "main.tscn"):
            shutil.copyfile(runtime / name, project / name)
        (project / "subject.glb").write_bytes(source)
        selection = {"view": view}
        if pose is not None:
            (project / "pose.json").write_text(json.dumps(pose))
            selection["pose"] = pose
        template = (runtime / "main.gd").read_text()
        (project / "main.gd").write_text(
            review.render_script(template, selection, fallback=False)
        )
        godot = str(config.tools((config.GODOT,))[config.GODOT])
        base = [godot, "--headless", "--path", str(project)]
        subprocess.run(
            [*base, "--editor", "--import"],
            check=True,
            capture_output=True,
            timeout=120,
        )
        run = subprocess.run(
            [*base, "--quit-after", "30"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        lines = [line for line in run.stdout.splitlines() if line.startswith(READY)]
        self.assertEqual(len(lines), 1, run.stdout + run.stderr)
        return json.loads(lines[0].removeprefix(READY))

    def test_review_camera_fits_a_small_prop_at_three_quarter(self):
        scene = self.review_scene(textured_cubes(size=0.02), "three-quarter")
        radius = math.hypot(*scene["bounds_size"]) / 2
        frame = scene["camera_frame"]
        self.assertAlmostEqual(
            math.degrees(math.asin(radius / frame["distance"])), 22.5, places=3
        )
        offset = [p - c for p, c in zip(scene["camera_position"], frame["center"])]
        self.assertAlmostEqual(math.atan2(offset[0], offset[2]), math.pi / 4, places=5)
        near, far = scene["camera_clip"]
        self.assertLess(near, frame["distance"] - radius)
        self.assertGreater(far, frame["distance"] + radius)

    def test_review_scale_bar_is_a_power_of_ten_inside_the_bounds(self):
        scene = self.review_scene(textured_cubes(size=0.02), "front")
        bar = scene["scale_bar"]
        self.assertAlmostEqual(bar["length"], 0.01, places=6)
        self.assertAlmostEqual(math.dist(bar["start"], bar["end"]), 0.01, places=6)
        low = scene["bounds_origin"]
        high = [o + s for o, s in zip(low, scene["bounds_size"])]
        for point in (bar["start"], bar["end"]):
            for value, lower, upper in zip(point, low, high):
                self.assertLessEqual(lower - 1e-6, value)
                self.assertLessEqual(value, upper + 1e-6)

    def test_posed_bounds_follow_the_skinned_mesh(self):
        pose = {
            "player": "/subject/AnimationPlayer",
            "animation": "Lift",
            "time_seconds": 1.0,
        }
        scene = self.review_scene(skinned_cube(lift=2.0), "three-quarter", pose)
        self.assertEqual(scene["pose"]["time_seconds"], 1.0)
        for actual, expected in zip(
            scene["bounds_origin"] + scene["bounds_size"], [-0.5, 1.5, -0.5, 1, 1, 1]
        ):
            self.assertAlmostEqual(actual, expected, places=4)


if __name__ == "__main__":
    unittest.main()
