import math
import tempfile
import unittest
from pathlib import Path

from test_support import glb

from godot_mcp import review


class PoseReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "animated.glb").write_bytes(
            glb({"animations": [{}], "skins": [{}]})
        )
        self.pose = {
            "player": "/subject/AnimationPlayer",
            "animation": "Walk",
            "time_seconds": 1.25,
        }

    def test_animated_source_needs_explicit_valid_pose_and_is_sealed(self):
        with self.assertRaises(ValueError):
            review.prepare(self.root, "animated.glb", "static")
        result = review.prepare(self.root, "animated.glb", "review", pose=self.pose)
        self.assertEqual(result["pose"], self.pose)
        self.assertEqual(review.inspect(self.root, "review")["pose"], self.pose)
        owner = self.root / "review/owner.json"
        data = owner.read_text().replace("Walk", "Other")
        owner.write_text(data)
        with self.assertRaisesRegex(ValueError, "ownership"):
            review.inspect(self.root, "review")

    def test_bad_pose_rejected_before_creating_directory(self):
        for pose in (
            {},
            {**self.pose, "time_seconds": True},
            {**self.pose, "time_seconds": math.nan},
            {**self.pose, "unexpected": 1},
            {**self.pose, "time_seconds": -1},
        ):
            with self.subTest(pose=pose), self.assertRaises(ValueError):
                review.prepare(self.root, "animated.glb", "bad", pose=pose)
            self.assertFalse((self.root / "bad").exists())

    def test_pose_outside_named_clips_is_rejected_before_output(self):
        from test_support import animated

        (self.root / "clips.glb").write_bytes(animated([("Walk", 2.0), ("Run", 0.5)]))
        for pose in (
            {**self.pose, "animation": "Jump"},
            {**self.pose, "animation": "Walk", "time_seconds": 2.5},
            {**self.pose, "animation": "Run", "time_seconds": 1.0},
        ):
            with self.subTest(pose=pose), self.assertRaises(ValueError):
                review.prepare(self.root, "clips.glb", "bad", pose=pose)
            self.assertFalse((self.root / "bad").exists())
        for directory, pose in (
            ("end", {**self.pose, "animation": "Walk", "time_seconds": 2.0}),
            ("run", {**self.pose, "animation": "Run", "time_seconds": 0.5}),
        ):
            self.assertEqual(
                review.prepare(self.root, "clips.glb", directory, pose=pose)["pose"],
                pose,
            )

    def test_renamed_clips_only_bound_pose_time_by_longest_clip(self):
        from test_support import animated

        (self.root / "renamed.glb").write_bytes(
            animated([("Walk.001", 2.0), ("Idle_loop", 1.0), (None, 0.5)])
        )
        pose = {**self.pose, "animation": "Walk001", "time_seconds": 1.5}
        self.assertEqual(
            review.prepare(self.root, "renamed.glb", "renamed", pose=pose)["pose"], pose
        )
        with self.assertRaises(ValueError):
            review.prepare(
                self.root, "renamed.glb", "late", pose={**pose, "time_seconds": 2.5}
            )
        self.assertFalse((self.root / "late").exists())

    def test_pose_metadata_must_match_actual_scene(self):
        metadata = {
            "player": self.pose["player"],
            "animation": "Walk",
            "time_seconds": 1.25,
            "duration": 2.0,
            "bone_count": 1,
            "bones": [
                {
                    "name": "root",
                    "skeleton": "/subject/Skeleton",
                    "index": 0,
                    "origin": [0, 0, 0],
                    "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                }
            ],
        }
        review.validate_scene(self.pose, metadata)
        for bad in (
            {**metadata, "animation": "Other"},
            {**metadata, "time_seconds": 1.5},
            {**metadata, "time_seconds": -0.000001},
            {**metadata, "bones": [{**metadata["bones"][0], "index": -1}]},
            {**metadata, "bones": [{**metadata["bones"][0], "skeleton": ""}]},
            {**metadata, "bone_count": 2, "bones": metadata["bones"] * 2},
            {**metadata, "bones": []},
            {**metadata, "bones": [{"origin": [0, math.inf, 0]}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                review.validate_scene(self.pose, bad)

    def test_capture_seals_configuration_and_rejects_drift_or_wrong_pose(self):
        import json
        import os
        from unittest.mock import patch

        for problem in ("config", "scene", "source"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as d:
                root = Path(d).resolve()
                (root / "animated.glb").write_bytes(glb({"animations": [{}]}))
                review.prepare(root, "animated.glb", "review", pose=self.pose)
                tool = root / "tool"
                tool.write_text("tool")
                env = {
                    k: str(tool)
                    for k in (
                        "CAVI_GODOT_BIN",
                        "CAVI_GODOT_WEB_TEMPLATE",
                        "CAVI_CHROMIUM_BIN",
                        "CAVI_NODE_BIN",
                    )
                }
                checked = []

                def fake_run(
                    command, timeout, evidence, label, problem=problem, checked=checked
                ):
                    (evidence / (label + ".stderr")).write_text("")
                    if label == "export":
                        Path(command[-1]).parent.joinpath("index.wasm").write_bytes(
                            b"wasm"
                        )
                    elif label == "browser":
                        output = Path(command[-1])
                        chosen = json.loads(
                            (output.parent / review.SELECTION_FILE).read_text()
                        )
                        self.assertEqual(chosen["pose"], self.pose)
                        checked.append(output.parent)
                        output.write_bytes(b"png")
                        metadata = {
                            "player": self.pose["player"],
                            "animation": "Other",
                            "time_seconds": 1.25,
                            "duration": 2,
                            "bone_count": 0,
                            "bones": [],
                        }
                        Path(str(output) + ".json").write_text(
                            json.dumps({"pose": metadata})
                        )
                        if problem == "config":
                            (checked[0] / review.SELECTION_FILE).write_text("{}")
                        if problem == "source":
                            (checked[0] / "subject.glb").write_bytes(b"changed")
                    return "fixture"

                with (
                    patch.dict(os.environ, env),
                    patch("importlib.metadata.version", return_value="fixture"),
                    patch.object(review, "run_owned", side_effect=fake_run),
                    patch.object(
                        review,
                        "run_with_browser",
                        side_effect=lambda cmd, profile, task, evidence: task(1),
                    ),
                    self.assertRaisesRegex(
                        Exception,
                        "Copied subject or selection changed|Rendered pose does not match",
                    ),
                ):
                    review.capture(root, "review")
                self.assertEqual(review.inspect(root, "review")["state"], "prepared")
                self.assertTrue(checked)
