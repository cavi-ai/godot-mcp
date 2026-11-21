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
                    "keys": 31,
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

    def test_inspection_reports_asset_qa_metrics(self):
        (self.root / "flawed.glb").write_bytes(
            textured_cubes(2, slide="Slide", flawed=True)
        )
        report = inspection.inspect_asset(self.root, "flawed.glb", "flawed")["report"]
        qa = report["qa"]
        self.assertEqual(
            {key: value for key, value in qa.items() if key != "bounds"},
            {
                "triangles": 24,
                "vertices": 48,
                "degenerate_triangles": 2,
                "uv0_outside_unit_surfaces": 2,
                "materials": 1,
                "textures": 1,
                "texture_rgba8_bytes": 20,
                "max_texture_size": 2,
                "npot_textures": 0,
                "max_bone_influences": 0,
                "max_blend_shapes": 0,
                "mirrored_meshes": 1,
                "non_uniform_scale_meshes": 1,
                "animation_keys": 31,
            },
        )
        box = qa["bounds"]["position"] + qa["bounds"]["size"]
        for actual, expected in zip(box, [-0.5, -1, -0.5, 2.4, 2, 1]):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(
            [
                (s["degenerate_triangles"], s["uv0_outside_unit"])
                for s in report["surfaces"]
            ],
            [(1, True), (1, True)],
        )
        self.assertEqual([clip["keys"] for clip in report["clips"]], [31])
        (self.root / "rig.glb").write_bytes(skinned_cube(lift=1.0))
        rig = inspection.inspect_asset(self.root, "rig.glb", "rig")["report"]
        self.assertEqual(rig["qa"]["max_bone_influences"], 4)

    def test_inspection_checks_and_records_a_budget(self):
        (self.root / "flawed.glb").write_bytes(textured_cubes(2, flawed=True))
        limits = {
            "triangles": 24,
            "draw_calls": 1,
            "mirrored_meshes": 0,
            "base_offset": 1.0,
        }
        result = inspection.inspect_asset(self.root, "flawed.glb", "budget", limits)
        checks = result["budget"]["checks"]
        self.assertEqual(
            [(c["rule"], c["limit"], c["within"]) for c in checks],
            [
                ("triangles", 24, True),
                ("draw_calls", 1, False),
                ("mirrored_meshes", 0, False),
                ("base_offset", 1.0, False),
            ],
        )
        self.assertEqual([c["value"] for c in checks[:3]], [24, 2, 1])
        self.assertAlmostEqual(checks[3]["value"], math.hypot(0.7, 1), places=5)
        self.assertFalse(result["budget"]["within"])
        receipt = json.loads((self.root / "budget" / "receipt.json").read_text())
        self.assertEqual(receipt["budget"], result["budget"])

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

    def test_motion_reports_speed_loop_seam_and_root_motion(self):
        def qa(source, directory, animation):
            return motion.inspect_motion(
                self.root,
                source,
                directory,
                "/subject/AnimationPlayer",
                animation,
                [0, 1],
            )["report"]["qa"]

        slide = qa("asset.glb", "slide", "Slide")
        self.assertEqual((slide["frame_rate"], slide["frames"]), (30, 31))
        self.assertAlmostEqual(slide["loop_seam"]["position"], 1.0, places=5)
        self.assertEqual(slide["loop_seam"]["rotation"], 0.0)
        speed = slide["max_linear_speed"]
        self.assertAlmostEqual(speed["value"], 1.0, places=4)
        self.assertEqual((speed["path"], speed["bone"]), ("/subject/Cube0", None))
        self.assertEqual(
            slide["max_angular_speed"], {"value": 0.0, "path": None, "bone": None}
        )
        moved = {
            (m["path"], m["bone"]): m["displacement"] for m in slide["root_motion"]
        }
        self.assertEqual(
            set(moved), {("/subject/Cube0", None), ("/subject/Cube1", None)}
        )
        for actual, expected in zip(moved[("/subject/Cube0", None)], [1, 0, 0]):
            self.assertAlmostEqual(actual, expected, places=5)
        (self.root / "rig.glb").write_bytes(skinned_cube(lift=2.0))
        lift = qa("rig.glb", "lift", "Lift")
        self.assertAlmostEqual(lift["max_linear_speed"]["value"], 2.0, places=4)
        self.assertEqual(lift["max_linear_speed"]["bone"], "Bone")
        bone = [m for m in lift["root_motion"] if m["bone"] == "Bone"]
        self.assertEqual(len(bone), 1)
        for actual, expected in zip(bone[0]["displacement"], [0, 2, 0]):
            self.assertAlmostEqual(actual, expected, places=5)

    def review_scene(self, source, view, pose=None):
        """The metadata the review scene reports when run headless on ``source``.

        The subject and selection are loose files the scene reads at run time;
        nothing is imported.
        """
        project = self.root / "review"
        project.mkdir()
        runtime = Path(review.__file__).parent / "runtime"
        for name in ("project.godot", "main.tscn", "main.gd", "pose.gd"):
            shutil.copyfile(runtime / name, project / name)
        (project / "subject.glb").write_bytes(source)
        receipt = {"view": view, **({} if pose is None else {"pose": pose})}
        (project / review.SELECTION_FILE).write_text(
            json.dumps(review.scene_selection(receipt, fallback=False))
        )
        godot = str(config.tools((config.GODOT,))[config.GODOT])
        base = [godot, "--headless", "--path", str(project)]
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
        fit = scene["camera_fit"]
        half_vertical = math.radians(fit["fov"]) / 2
        width, height = fit["viewport"]
        half_horizontal = math.atan(math.tan(half_vertical) * width / height)
        self.assertAlmostEqual(
            frame["distance"],
            radius / math.sin(min(half_vertical, half_horizontal)),
        )
        offset = [p - c for p, c in zip(scene["camera_position"], frame["center"])]
        self.assertAlmostEqual(math.atan2(offset[0], offset[2]), math.pi / 4, places=5)
        near, far = scene["camera_clip"]
        self.assertLess(near, frame["distance"] - radius)
        self.assertGreater(far, frame["distance"] + radius)

    def test_review_scene_reports_its_renderer(self):
        scene = self.review_scene(textured_cubes(), "front")
        self.assertEqual(scene["renderer"]["method"], "gl_compatibility")
        self.assertEqual(scene["renderer"]["driver"], "opengl3")

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
