import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cavi_mcp_core.contracts import OperationUnknown
from test_support import animated, missing_tools, textured_cubes

from godot_mcp import motion, review

STILL = {"value": 0, "path": None, "bone": None}
QA = {
    "frame_rate": 30,
    "frames": 31,
    "seconds_per_frame": 1 / 30,
    "loop_seam": {"position": 0, "rotation": 0},
    "max_linear_speed": STILL,
    "max_angular_speed": STILL,
    "root_motion": [],
}


class MotionRequestTests(unittest.TestCase):
    def test_times_are_bounded_finite_and_strict(self):
        motion.validate_times([0, 0.5, 1])
        for value in (
            [],
            [0],
            [0, 0],
            [1, 0],
            [-0.1, 1],
            [0, math.nan],
            [0, True],
            list(range(10)),
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                motion.validate_times(value)

    def test_report_rejects_bad_time_shape_and_identity(self):
        t = {"origin": [0, 0, 0], "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        sample = {
            "normalized_time": 0,
            "seconds": 0,
            "nodes": [{"local_transform": t, "world_transform": t}],
            "bones": [],
        }
        report = {
            "status": "ok",
            "player": "/p",
            "animation": "walk",
            "duration": 1.0,
            "loop_mode": 0,
            "godot_version": {},
            "units": "metres",
            "bone_space": "skeleton relative and Godot world",
            "nodes": [{"path": "/n", "type": "Node3D"}],
            "bones": [],
            "samples": [sample, {**sample, "normalized_time": 1, "seconds": 1}],
            "qa": {
                **QA,
                "max_linear_speed": {"value": 1.0, "path": "/n", "bone": None},
                "root_motion": [
                    {"path": "/n", "bone": None, "displacement": [1, 0, 0]}
                ],
            },
        }
        motion.validate_report(report, "/p", "walk", [0, 1])
        moving = report["qa"]["max_linear_speed"]
        for bad in (
            {**report, "qa": {**QA, "frames": 1}},
            {**report, "qa": {**QA, "seconds_per_frame": 0.5}},
            {**report, "qa": {**QA, "max_linear_speed": {**moving, "path": "/x"}}},
            {**report, "qa": {**QA, "max_linear_speed": {**moving, "path": None}}},
            {
                **report,
                "qa": {
                    **QA,
                    "root_motion": [{"path": "/n", "bone": None, "displacement": []}],
                },
            },
            {**report, "player": "/other"},
            {**report, "nodes": []},
            {**report, "samples": [sample]},
            {**report, "duration": math.nan},
            {
                **report,
                "samples": [sample, {**sample, "normalized_time": True, "seconds": 1}],
            },
            {
                **report,
                "samples": [
                    {
                        **sample,
                        "nodes": [{"local_transform": "bad", "world_transform": t}],
                    },
                    report["samples"][1],
                ],
            },
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                motion.validate_report(bad, "/p", "walk", [0, 1])

    def test_fractional_sample_identity_remains_exact(self):
        transform = {"origin": [0, 0, 0], "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        sample = {
            "normalized_time": 1 / 3,
            "seconds": 1 / 3,
            "nodes": [{"local_transform": transform, "world_transform": transform}],
            "bones": [],
        }
        report = {
            "status": "ok",
            "player": "/p",
            "animation": "clip",
            "duration": 1,
            "loop_mode": 0,
            "godot_version": {},
            "units": "metres",
            "bone_space": "skeleton relative and Godot world",
            "nodes": [{"path": "/n", "type": "Node3D"}],
            "bones": [],
            "samples": [sample, {**sample, "normalized_time": 1, "seconds": 1}],
            "qa": QA,
        }
        motion.validate_report(report, "/p", "clip", [1 / 3, 1])
        report["samples"][0] = {**sample, "normalized_time": 0.333333333333333}
        with self.assertRaises(ValueError):
            motion.validate_report(report, "/p", "clip", [1 / 3, 1])

    def test_bone_inventory_must_be_complete_and_unique(self):
        t = {"origin": [0, 0, 0], "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        pose = {"skeleton_transform": t, "world_transform": t}
        node = {"local_transform": t, "world_transform": t}
        b = {"skeleton": "/s", "index": 0, "name": "root", "parent": -1}
        sample = {"normalized_time": 0, "seconds": 0, "nodes": [node], "bones": [pose]}
        report = {
            "status": "ok",
            "player": "/p",
            "animation": "walk",
            "duration": 1,
            "loop_mode": 0,
            "godot_version": {},
            "units": "metres",
            "bone_space": "skeleton relative and Godot world",
            "nodes": [{"path": "/s", "type": "Skeleton3D"}],
            "bones": [b],
            "samples": [sample, {**sample, "normalized_time": 1, "seconds": 1}],
            "qa": {
                **QA,
                "max_angular_speed": {"value": 2.0, "path": "/s", "bone": "root"},
            },
        }
        motion.validate_report(report, "/p", "walk", [0, 1])
        for bad in (
            [{**b, "index": 1}],
            [{**b, "skeleton": "/unknown"}],
            [b, {**b, "index": 1, "parent": 0}],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                motion.validate_report({**report, "bones": bad}, "/p", "walk", [0, 1])

    def test_missing_or_empty_clip_is_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            engine = root / "engine"
            engine.write_bytes(b"engine")
            (root / "static.glb").write_bytes(textured_cubes())
            (root / "clips.glb").write_bytes(animated([("clip", 1.0), ("pose", 0.0)]))
            with (
                patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                patch("godot_mcp.engine.launch", side_effect=AssertionError("ran")),
            ):
                for directory, source, animation in (
                    ("static", "static.glb", "clip"),
                    ("unknown", "clips.glb", "walk"),
                    ("empty", "clips.glb", "pose"),
                ):
                    with self.subTest(directory=directory):
                        with self.assertRaises(ValueError):
                            motion.inspect_motion(
                                root, source, directory, "/p", animation, [0, 1]
                            )
                        self.assertFalse((root / directory).exists())

    def test_path_missing_engine_and_pending_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(animated([("clip", 1.0)]))
            engine = root / "engine"
            engine.write_bytes(b"engine")
            with (
                patch.dict(os.environ, missing_tools(), clear=True),
                self.assertRaises(ValueError),
            ):
                motion.inspect_motion(root, "asset.glb", "out", "/p", "clip", [0, 1])
            with patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}):
                with self.assertRaises(ValueError):
                    motion.inspect_motion(
                        root, "../outside.glb", "out", "/p", "clip", [0, 1]
                    )
                with (
                    patch(
                        "godot_mcp.engine.launch",
                        side_effect=review.CleanupUnverified("unknown"),
                    ),
                    self.assertRaises(review.CleanupUnverified),
                ):
                    motion.inspect_motion(
                        root, "asset.glb", "out", "/p", "clip", [0, 1]
                    )
                self.assertTrue((root / "out/.motion.pending").exists())
                with self.assertRaises(FileExistsError):
                    motion.inspect_motion(
                        root, "asset.glb", "out", "/p", "clip", [0, 1]
                    )

    def test_source_drift_during_engine_call_cannot_publish_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            asset, engine = root / "asset.glb", root / "engine"
            asset.write_bytes(animated([("clip", 1.0)]))
            engine.write_bytes(b"engine")
            runtime = Path(motion.__file__).parent / "runtime/motion.gd"
            identity = {"runtime": {"motion.gd": review.digest(runtime)}}
            t = {"origin": [0, 0, 0], "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
            sample = {
                "normalized_time": 0,
                "seconds": 0,
                "nodes": [{"local_transform": t, "world_transform": t}],
                "bones": [],
            }
            report = {
                "status": "ok",
                "player": "/p",
                "animation": "clip",
                "duration": 1,
                "loop_mode": 0,
                "godot_version": {},
                "units": "metres",
                "bone_space": "skeleton relative and Godot world",
                "nodes": [{"path": "/n", "type": "Node3D"}],
                "bones": [],
                "samples": [sample, {**sample, "normalized_time": 1, "seconds": 1}],
                "qa": QA,
            }

            def run(godot, project, script, timeout, evidence):
                (evidence / "godot.stdout").write_bytes(b"")
                (evidence / "godot.stderr").write_bytes(b"")
                (Path(project) / "report.json").write_text(json.dumps(report))
                asset.write_bytes(b"changed")

            with (
                patch.dict(os.environ, {"CAVI_GODOT_BIN": str(engine)}),
                patch.object(review, "provider_identity", return_value=identity),
                patch("godot_mcp.engine.launch", side_effect=run),
                self.assertRaises(OperationUnknown),
            ):
                motion.inspect_motion(root, "asset.glb", "out", "/p", "clip", [0, 1])
            self.assertFalse((root / "out/receipt.json").exists())
