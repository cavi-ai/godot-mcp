import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_support import glb, missing_tools, textured_cubes

from godot_mcp import config, review
from godot_mcp.review import inspect, prepare, scene_selection


class ReviewTests(unittest.TestCase):
    def test_outside_path_rejected(self):
        with TemporaryDirectory() as d, self.assertRaises(ValueError):
            prepare(Path(d), "../outside.glb", "review")

    def test_external_texture_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({"images": [{"uri": "missing.png"}]}))
            with self.assertRaisesRegex(ValueError, "embedded"):
                prepare(root, "mesh.glb", "review")

    def test_texture_free_static_drafts_prepare_and_inspect(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            for name, attributes in (
                ("plain", {"POSITION": 0}),
                ("vertex-colour", {"POSITION": 0, "COLOR_0": 0}),
            ):
                source = root / f"{name}.glb"
                source.write_bytes(
                    glb(
                        {
                            "images": [],
                            "textures": [],
                            "materials": [{}],
                            "meshes": [
                                {
                                    "primitives": [
                                        {"attributes": attributes, "material": 0}
                                    ]
                                }
                            ],
                        }
                    )
                )
                prepared = prepare(root, source.name, name)
                self.assertEqual(prepared["source"], source.name)
                self.assertEqual(inspect(root, name)["state"], "prepared")

    def test_multiple_views_are_sealed_and_reject_pose_before_writing(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review", all_views=True)
            self.assertTrue(receipt["all_views"])
            self.assertTrue(inspect(root, "review")["all_views"])
            with self.assertRaisesRegex(ValueError, "static"):
                prepare(root, "mesh.glb", "pose", pose={}, all_views=True)
            self.assertFalse((root / "pose").exists())
            owner = root / "review" / "owner.json"
            import json

            owner_data = json.loads(owner.read_text())
            del owner_data["all_views"]
            owner.write_text(json.dumps(owner_data))
            with self.assertRaisesRegex(ValueError, "ownership"):
                inspect(root, "review")

    def test_view_evidence_requires_each_bound_file_and_correct_metadata(self):
        import json

        from godot_mcp.review import validate_view_captures

        with TemporaryDirectory() as d:
            root = Path(d)
            bundle = root / "bundle"
            bundle.mkdir()
            captures = {}
            artifacts = {}
            for index, view in enumerate(("front", "three-quarter", "side")):
                name = "capture.png" if view == "front" else f"capture-{view}.png"
                image = f"bundle/{name}"
                metadata = image + ".view.json"
                (root / image).write_bytes(view.encode())
                (root / metadata).write_text(
                    json.dumps({"view": view, "camera_position": [index, 2, 3]})
                )
                captures[view] = {"image": image, "metadata": metadata}
                artifacts[image] = artifacts[metadata] = "digest"
            receipt = {
                "bundle": "bundle",
                "view": "front",
                "view_captures": captures,
                "artifacts": artifacts,
            }
            validate_view_captures(root, receipt)
            del artifacts[captures["side"]["image"]]
            with self.assertRaisesRegex(ValueError, "Missing"):
                validate_view_captures(root, receipt)
            artifacts[captures["side"]["image"]] = "digest"
            (root / captures["side"]["metadata"]).write_text('{"view":"front"}')
            with self.assertRaisesRegex(ValueError, "metadata"):
                validate_view_captures(root, receipt)

    def test_lighting_is_stored_and_rejected_before_the_directory_exists(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            self.assertEqual(receipt["lighting"], "studio")
            owner = json.loads((root / "review" / "owner.json").read_text())
            self.assertEqual(owner["lighting"], "studio")
            self.assertEqual(inspect(root, "review")["lighting"], "studio")
            self.assertEqual(scene_selection(receipt, False)["lighting"], "studio")
            for lighting in ("clay", "sun"):
                prepared = prepare(root, "mesh.glb", lighting, lighting=lighting)
                self.assertEqual(prepared["lighting"], lighting)
                self.assertEqual(scene_selection(prepared, False)["lighting"], lighting)
                self.assertEqual(
                    json.loads((root / lighting / "owner.json").read_text())[
                        "lighting"
                    ],
                    lighting,
                )
            omitted = dict(receipt)
            del omitted["lighting"]
            self.assertEqual(scene_selection(omitted, False)["lighting"], "studio")
            stored = json.loads((root / "review" / "review.json").read_text())
            del stored["lighting"]
            del owner["lighting"]
            (root / "review" / "review.json").write_text(json.dumps(stored))
            (root / "review" / "owner.json").write_text(json.dumps(owner))
            self.assertNotIn("lighting", inspect(root, "review"))
            stored["lighting"] = "night"
            owner["lighting"] = "night"
            (root / "review" / "review.json").write_text(json.dumps(stored))
            (root / "review" / "owner.json").write_text(json.dumps(owner))
            with self.assertRaises(ValueError):
                inspect(root, "review")
            with self.assertRaises(ValueError):
                prepare(root, "mesh.glb", "night", lighting="night")
            self.assertFalse((root / "night").exists())

    def test_static_framing_is_validated_and_sealed(self):
        import json

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            frame = {"center": [1.0, 2.0, 3.0], "distance": 4.0}
            receipt = prepare(root, "mesh.glb", "review", framing=frame)
            self.assertEqual(receipt["framing"], frame)
            self.assertEqual(inspect(root, "review")["framing"], frame)
            for index, bad in enumerate(
                (
                    {"center": [0, 1], "distance": 1},
                    {"center": [0, float("nan"), 0], "distance": 1},
                    {"center": [0, 0, 0], "distance": 0},
                    {"center": [0, 0, 0], "distance": float("inf")},
                )
            ):
                with self.subTest(frame=bad), self.assertRaises(ValueError):
                    prepare(root, "mesh.glb", f"invalid-{index}", framing=bad)
                self.assertFalse((root / f"invalid-{index}").exists())
            with self.assertRaisesRegex(ValueError, "static"):
                prepare(root, "mesh.glb", "pose", pose={}, framing=frame)
            self.assertFalse((root / "pose").exists())
            owner = root / "review/owner.json"
            value = json.loads(owner.read_text())
            value["framing"]["distance"] = 5
            owner.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "ownership"):
                inspect(root, "review")

    def test_camera_evidence_binds_scene_and_requested_frame(self):
        import json

        from godot_mcp.review import validate_camera_evidence

        with TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "bundle").mkdir()
            metadata = root / "bundle/capture.png.json"
            scene = {"camera_frame": {"center": [1, 2, 3], "distance": 4}}
            metadata.write_text(json.dumps(scene))
            receipt = {
                "bundle": "bundle",
                "scene": scene,
                "artifacts": {"bundle/capture.png.json": "digest"},
                "framing": {"center": [1, 2, 3], "distance": 4},
            }
            validate_camera_evidence(root, receipt)
            receipt["framing"]["distance"] = 5
            with self.assertRaisesRegex(ValueError, "differs"):
                validate_camera_evidence(root, receipt)
            receipt["framing"]["distance"] = 4
            metadata.write_text(
                json.dumps({"camera_frame": {"center": [0, 0, 0], "distance": 4}})
            )
            with self.assertRaisesRegex(ValueError, "metadata changed"):
                validate_camera_evidence(root, receipt)

    def test_legacy_prepared_capture_checks_new_camera_metadata(self):
        import json
        import os
        from unittest.mock import patch

        from godot_mcp.review import digest

        with TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            owner_path = root / "review/owner.json"
            owner = json.loads(owner_path.read_text())
            del owner["camera_frame_evidence"]
            del receipt["camera_frame_evidence"]
            owner_path.write_text(json.dumps(owner))
            bundle = root / "review/bundle"
            bundle.mkdir()
            metadata = bundle / "capture.png.json"
            scene = {"camera_frame": {"center": [0, 0, 0], "distance": 1}}
            metadata.write_text(json.dumps(scene))
            tools = {
                name: str(root / "mesh.glb")
                for name in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }
            receipt.update(
                state="captured",
                bundle="bundle",
                scene=scene,
                inputs={"tools": {name: "digest" for name in tools}},
                artifacts={"bundle/capture.png.json": digest(metadata)},
            )
            (root / "review/review.json").write_text(json.dumps(receipt))
            with (
                patch.dict(os.environ, tools),
                patch(
                    "godot_mcp.review.input_snapshot", return_value=receipt["inputs"]
                ),
            ):
                self.assertEqual(inspect(root, "review")["scene"], scene)
                receipt["scene"]["camera_frame"]["distance"] = 2
                (root / "review/review.json").write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, "metadata changed"):
                    inspect(root, "review")

    def test_geometry_preview_only_for_unadorned_triangles(self):
        import copy

        from godot_mcp.review import geometry_only, read_glb, validate_geometry_preview

        with TemporaryDirectory() as d:
            source = Path(d) / "geometry.glb"
            source.write_bytes(
                glb(
                    {
                        "images": [],
                        "textures": [],
                        "materials": [],
                        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                    }
                )
            )
            original = read_glb(source)
            self.assertTrue(geometry_only(original))
            for change in (
                "material",
                "normal",
                "color",
                "mode",
                "invalid",
                "animation",
                "texture",
            ):
                value = copy.deepcopy(original)
                primitive = value["meshes"][0]["primitives"][0]
                if change == "material":
                    value["materials"] = [{}]
                if change == "normal":
                    primitive["attributes"]["NORMAL"] = 0
                if change == "color":
                    primitive["attributes"]["COLOR_0"] = 0
                if change == "mode":
                    primitive["mode"] = 1
                if change == "invalid":
                    primitive["attributes"]["POSITION"] = 999
                if change == "animation":
                    value["animations"] = [{}]
                if change == "texture":
                    value["textures"] = [{}]
                with self.subTest(change=change):
                    self.assertFalse(geometry_only(value))
            validate_geometry_preview(
                {"geometry_preview_surfaces": 1, "material_surfaces": 1}, True
            )
            validate_geometry_preview({"geometry_preview_surfaces": 0}, False)
            for count, expected in ((0, True), (1, False), (None, True)):
                with self.assertRaisesRegex(ValueError, "preview"):
                    validate_geometry_preview(
                        {"geometry_preview_surfaces": count}, expected
                    )

    def test_changed_source_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            source = root / "mesh.glb"
            source.write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            source.write_bytes(source.read_bytes() + b"x")
            with self.assertRaisesRegex(ValueError, "changed"):
                inspect(root, "review")


class DocumentTests(unittest.TestCase):
    def test_malformed_json_chunk_is_rejected_before_output(self):
        import json

        from test_support import raw_glb

        valid = {
            "asset": {"version": "2.0"},
            "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}
            ],
            "buffers": [{"byteLength": 36}],
            "bufferViews": [{"buffer": 0, "byteLength": 36}],
        }
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "valid.glb").write_bytes(raw_glb(json.dumps(valid).encode()))
            self.assertEqual(prepare(root, "valid.glb", "valid")["state"], "prepared")
            for name, document in (
                ("list", b"[]"),
                ("nan", json.dumps({**valid, "extras": float("nan")}).encode()),
                ("views", json.dumps({**valid, "bufferViews": [1]}).encode()),
                ("buffers", json.dumps({**valid, "buffers": [1]}).encode()),
            ):
                (root / f"{name}.glb").write_bytes(raw_glb(document))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    prepare(root, f"{name}.glb", name)
                self.assertFalse((root / name).exists())


class ConfigurationTests(unittest.TestCase):
    def test_installed_tools_are_found_without_configuration(self):
        import os
        from unittest.mock import patch

        from godot_mcp import config

        with TemporaryDirectory() as d:
            root = Path(d)
            commands = root / "bin"
            commands.mkdir()
            for name, body in (
                ("godot", "echo 4.7.2.stable.official.abc"),
                ("chromium", "exit 0"),
                ("node", "exit 0"),
            ):
                (commands / name).write_text("#!/bin/sh\n" + body + "\n")
                (commands / name).chmod(0o755)
            templates = root / "templates"
            for version in ("4.7.2.stable", "4.7.1.stable", "4.7.20.stable"):
                (templates / version).mkdir(parents=True)
                (templates / version / config.WEB_TEMPLATE).write_bytes(b"zip")
            missing = root / "missing"
            defaults = {
                name: (missing, command)
                for name, (_, command) in config.EXECUTABLES.items()
            }
            with (
                patch.dict(os.environ, {"PATH": str(commands)}, clear=True),
                patch.dict(config.EXECUTABLES, defaults),
                patch("godot_mcp.config.export_templates", return_value=templates),
            ):
                self.assertEqual(
                    config.tools(),
                    {
                        config.GODOT: (commands / "godot").resolve(),
                        config.TEMPLATE: (
                            templates / "4.7.2.stable" / config.WEB_TEMPLATE
                        ).resolve(),
                        config.CHROMIUM: (commands / "chromium").resolve(),
                        config.NODE: (commands / "node").resolve(),
                    },
                )
                with (
                    patch.dict(os.environ, {config.NODE: str(missing)}),
                    self.assertRaisesRegex(ValueError, "set CAVI_NODE_BIN$"),
                ):
                    config.tools()

    def test_captured_review_without_tool_configuration_is_rejected(self):
        import json
        import os
        from unittest.mock import patch

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            names = (
                "CAVI_GODOT_BIN",
                "CAVI_GODOT_WEB_TEMPLATE",
                "CAVI_CHROMIUM_BIN",
                "CAVI_NODE_BIN",
            )
            receipt.update(
                state="captured", inputs={"tools": dict.fromkeys(names, "recorded")}
            )
            (root / "review" / "review.json").write_text(json.dumps(receipt))
            with (
                patch.dict(os.environ, missing_tools(), clear=True),
                self.assertRaisesRegex(ValueError, "CAVI_NODE_BIN"),
            ):
                inspect(root, "review")


class CaptureTests(unittest.TestCase):
    def test_missing_tools_do_not_replace_prepared_review(self):
        import os
        from unittest.mock import patch

        from godot_mcp.review import capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            with (
                patch.dict(os.environ, missing_tools()),
                self.assertRaises((KeyError, ValueError, FileNotFoundError)),
            ):
                capture(root, "review")
            self.assertEqual(inspect(root, "review")["state"], "prepared")


class CleanupTests(unittest.TestCase):
    def test_timeout_terminates_child_process_group(self):
        import os
        import subprocess
        import sys
        import time

        from godot_mcp.review import run_owned

        with TemporaryDirectory() as d:
            pidfile = Path(d) / "pid"
            script = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(60)'
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned([sys.executable, "-c", script, str(pidfile)], 1)
            pid = int(pidfile.read_text())
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("child process survived timeout")


class IntegrityTests(unittest.TestCase):
    def test_review_artifact_drift_is_rejected(self):
        from godot_mcp.review import atomic_json, digest

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            image = root / "review" / "capture.png"
            image.write_bytes(b"first")
            receipt["artifacts"] = {"capture.png": digest(image)}
            atomic_json(root / "review" / "review.json", receipt)
            image.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                inspect(root, "review")


class OwnershipTests(unittest.TestCase):
    def test_altered_owner_marker_is_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            (root / "review" / "owner.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "ownership"):
                inspect(root, "review")


class RuntimeCacheTests(unittest.TestCase):
    def test_reviews_reuse_export_without_sharing_writable_files(self):
        import json
        import os
        from unittest.mock import patch

        from godot_mcp.review import SELECTION_FILE, capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            tool = root / "tool"
            tool.write_text("tool")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }

            exports = []

            def fake_run(command, timeout, evidence, label):
                (evidence / (label + ".stderr")).write_text("")
                if label == "export":
                    exports.append(command[-1])
                    (Path(command[-1]).parent / "index.wasm").write_bytes(b"wasm")
                if label == "browser":
                    path = Path(command[-1])
                    path.write_bytes(b"png")
                    Path(str(path) + ".json").write_text(
                        '{"camera_frame":{"center":[0,0,0],"distance":1},"geometry_preview_surfaces":0}'
                    )
                return "fixture"

            receipts = {}
            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("godot_mcp.review.run_owned", side_effect=fake_run),
                patch(
                    "godot_mcp.review.run_with_browser",
                    side_effect=lambda command, profile, task, evidence: task(1),
                ),
            ):
                for name, view in (("first", "front"), ("second", "side")):
                    prepare(root, "mesh.glb", name, view=view)
                    receipts[name] = capture(root, name)
                bundles = {
                    name: root / name / receipt["bundle"]
                    for name, receipt in receipts.items()
                }
                (bundles["first"] / "index.wasm").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "Review artifact changed"):
                    inspect(root, "first")
                self.assertEqual(inspect(root, "second")["state"], "captured")
            self.assertEqual(len(exports), 1)
            key = receipts["first"]["project"]["runtime_export"]
            self.assertEqual(receipts["second"]["project"]["runtime_export"], key)
            cached = root / ".godot-review-runtime" / key / "index.wasm"
            self.assertEqual(cached.read_bytes(), b"wasm")
            self.assertEqual(
                json.loads((bundles["second"] / SELECTION_FILE).read_text())["view"],
                "side",
            )


class TextureTests(unittest.TestCase):
    def test_invalid_texture_reference_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({"textures": [{"source": 8}]}))
            with self.assertRaisesRegex(ValueError, "embedded image"):
                prepare(root, "mesh.glb", "review")

    def test_missing_material_texture_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({"textures": []}))
            with self.assertRaisesRegex(ValueError, "texture reference"):
                prepare(root, "mesh.glb", "review")


class EvidenceTests(unittest.TestCase):
    def test_failure_retains_stdout_stderr_and_returncode(self):
        import json
        import sys

        from godot_mcp.review import run_owned

        with TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(RuntimeError):
                run_owned(
                    [
                        sys.executable,
                        "-c",
                        'import sys; print("out"); print("err", file=sys.stderr); sys.exit(7)',
                    ],
                    5,
                    root,
                    "failed",
                )
            self.assertEqual((root / "failed.stdout").read_text(), "out\n")
            self.assertEqual((root / "failed.stderr").read_text(), "err\n")
            self.assertEqual(
                json.loads((root / "failed.json").read_text())["returncode"], 7
            )


class FaultCleanupTests(unittest.TestCase):
    def test_wait_failure_cleans_child_and_records_failure(self):
        import os
        import sys
        import time
        from unittest.mock import patch

        from godot_mcp.review import run_owned

        with TemporaryDirectory() as d:
            root = Path(d)
            pidfile = root / "child.pid"
            script = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(60)'

            def fail_wait(*args):
                time.sleep(0.3)
                raise OSError("injected wait failure")

            with (
                patch("os.waitid", side_effect=fail_wait),
                self.assertRaisesRegex(RuntimeError, "wait failure"),
            ):
                run_owned(
                    [sys.executable, "-c", script, str(pidfile)], 5, root, "fault"
                )
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertTrue((root / "fault.json").exists())


class RetryTests(unittest.TestCase):
    def test_failed_attempt_can_retry_without_losing_evidence(self):
        import os
        from unittest.mock import patch

        from godot_mcp.review import capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("tool")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }
            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch(
                    "godot_mcp.review.run_owned", side_effect=RuntimeError("injected")
                ),
            ):
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        capture(root, "review")
            self.assertEqual(len(list((root / "review" / "attempts").iterdir())), 2)


class SnapshotTests(unittest.TestCase):
    def test_tool_drift_changes_snapshot(self):
        from unittest.mock import patch

        from godot_mcp.review import input_snapshot

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("v1")
            with patch("importlib.metadata.version", return_value="fixture"):
                before = input_snapshot(root, receipt, {"engine": tool})
                tool.write_text("v2")
                self.assertNotEqual(
                    before, input_snapshot(root, receipt, {"engine": tool})
                )

    def test_changed_copy_stops_before_process_launch(self):
        import os
        import shutil
        from unittest.mock import patch

        from godot_mcp.review import capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("tool")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }
            copy = shutil.copyfile

            def altered(src, dest):
                result = copy(src, dest)
                if Path(dest).name == "subject.glb":
                    Path(dest).write_bytes(b"altered")
                return result

            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("shutil.copyfile", side_effect=altered),
                patch("godot_mcp.review.run_owned") as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "Copied source changed"):
                    capture(root, "review")
                run.assert_not_called()


class CensusFaultTests(unittest.TestCase):
    def test_initial_census_failure_still_cleans_child(self):
        import os
        import subprocess
        import sys
        from unittest.mock import patch

        from godot_mcp.review import run_owned

        with TemporaryDirectory() as d:
            root = Path(d)
            pidfile = root / "pid"
            script = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(0.5)'
            real_run = subprocess.run
            calls = 0

            def fail_first(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return subprocess.CompletedProcess(args[0], 1, "", "injected")
                return real_run(*args, **kwargs)

            with (
                patch("subprocess.run", side_effect=fail_first),
                self.assertRaisesRegex(RuntimeError, "inspect process groups"),
            ):
                run_owned(
                    [sys.executable, "-c", script, str(pidfile)], 5, root, "census"
                )
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)
            self.assertIn("injected", (root / "census.groups.json").read_text())


class CaptureCheckpointTests(unittest.TestCase):
    def test_safe_interruption_retries_and_preserves_attempt(self):
        from unittest.mock import patch

        with TemporaryDirectory() as d:
            root = Path(d)
            source = root / "mesh.glb"
            source.write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            attempts = root / "review" / "attempts"
            attempts.mkdir()
            (attempts / "previous").mkdir()
            with patch("godot_mcp.review.config.tools", return_value={}):
                with (
                    patch(
                        "godot_mcp.review.capture_locked", side_effect=KeyboardInterrupt
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    review.capture(root, "review")
                marker = root / "review" / ".capture.pending"
                self.assertEqual(json.loads(marker.read_text())["phase"], "safe")
                self.assertFalse(inspect(root, "review")["cleanup_pending"])
                with patch(
                    "godot_mcp.review.capture_locked", return_value=receipt
                ) as call:
                    self.assertEqual(review.capture(root, "review"), receipt)
                    call.assert_called_once()
                self.assertFalse(marker.exists())
                self.assertTrue((attempts / "previous").is_dir())
                self.assertEqual(source.read_bytes(), glb({}))

    def test_active_legacy_and_tampered_markers_block(self):
        from unittest.mock import patch

        with TemporaryDirectory() as d:
            root = Path(d)
            source = root / "mesh.glb"
            source.write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            marker = root / "review" / ".capture.pending"
            for value in (
                {"kind": "capture_pending"},
                review.capture_marker("review", receipt, "browser"),
                review.capture_marker("elsewhere", receipt, "safe"),
                review.capture_marker("review", receipt, "safe") | {"extra": True},
                review.capture_marker("review", receipt, "safe") | {"version": True},
                review.capture_marker("review", receipt, "safe")
                | {
                    "owner": review.capture_marker("review", receipt, "safe")["owner"]
                    | {"camera_frame_evidence": 1}
                },
            ):
                marker.write_text(json.dumps(value))
                self.assertTrue(inspect(root, "review")["cleanup_pending"])
                with patch("godot_mcp.review.capture_locked") as call:
                    with self.assertRaises(review.CleanupUnverified):
                        review.capture(root, "review")
                    call.assert_not_called()
                self.assertEqual(json.loads(marker.read_text()), value)
                self.assertEqual(source.read_bytes(), glb({}))

    def test_safe_marker_after_publication_returns_verified_receipt(self):
        from unittest.mock import patch

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review") | {"state": "captured"}
            marker = root / "review" / ".capture.pending"
            marker.write_text(
                json.dumps(review.capture_marker("review", receipt, "safe"))
            )
            with (
                patch("godot_mcp.review.inspect", return_value=receipt) as inspect_call,
                patch("godot_mcp.review.capture_locked") as capture_call,
            ):
                self.assertEqual(review.capture(root, "review"), receipt)
                inspect_call.assert_called_once_with(
                    root, "review", include_status=False
                )
                capture_call.assert_not_called()
            self.assertFalse(marker.exists())

    def test_native_intervals_leave_active_checkpoint_on_interruption(self):
        from unittest.mock import patch

        for phase in ("runtime_export", "browser", "version"):
            with self.subTest(phase=phase), TemporaryDirectory() as d:
                root = Path(d)
                (root / "mesh.glb").write_bytes(glb({}))
                receipt = prepare(root, "mesh.glb", "review")
                tools = {name: str(root / "tool") for name in config.REVIEW_TOOLS}
                inputs = {"source_sha256": receipt["source_sha256"]}

                def interrupt(*args, **kwargs):
                    raise KeyboardInterrupt()

                with (
                    patch("godot_mcp.review.config.tools", return_value=tools),
                    patch("godot_mcp.review.input_snapshot", return_value=inputs),
                    patch(
                        "godot_mcp.review.runtime_export",
                        side_effect=interrupt if phase == "runtime_export" else None,
                        return_value=(root, "cache", {}),
                    ),
                    patch(
                        "godot_mcp.review.run_with_browser",
                        side_effect=interrupt if phase == "browser" else None,
                    ),
                    patch(
                        "godot_mcp.review.run_owned",
                        side_effect=interrupt if phase == "version" else None,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    review.capture(root, "review")
                marker = json.loads((root / "review" / ".capture.pending").read_text())
                self.assertEqual(marker["phase"], phase)
                with self.assertRaises(review.CleanupUnverified):
                    review.capture(root, "review")


class UnknownCleanupTests(unittest.TestCase):
    def test_unverified_cleanup_blocks_retry(self):
        import os
        from unittest.mock import patch

        from godot_mcp.review import CleanupUnverified, capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("tool")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }
            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch(
                    "godot_mcp.review.run_owned",
                    side_effect=CleanupUnverified("unknown"),
                ),
            ):
                with self.assertRaises(CleanupUnverified):
                    capture(root, "review")
                with self.assertRaisesRegex(CleanupUnverified, "Previous"):
                    capture(root, "review")


class MidCaptureDriftTests(unittest.TestCase):
    def test_tool_drift_withholds_bundle_and_retains_attempt(self):
        import os
        from unittest.mock import patch

        from godot_mcp.review import capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("v1")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }

            def fake_run(command, timeout, evidence, label):
                (evidence / (label + ".stderr")).write_text("")
                if label == "export":
                    out = Path(command[-1]).parent
                    (out / "index.wasm").write_bytes(b"wasm")
                if label == "browser":
                    out = Path(command[-1])
                    out.write_bytes(b"png")
                    Path(str(out) + ".json").write_text("{}")
                    tool.write_text("v2")
                return "fixture"

            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("godot_mcp.review.run_owned", side_effect=fake_run),
                patch(
                    "godot_mcp.review.run_with_browser",
                    side_effect=lambda command, profile, task, evidence: task(1234),
                ),
                self.assertRaisesRegex(RuntimeError, "input changed during capture"),
            ):
                capture(root, "review")
            self.assertEqual(inspect(root, "review")["state"], "prepared")
            attempts = list((root / "review" / "attempts").iterdir())
            self.assertEqual(len(attempts), 1)
            self.assertFalse((attempts[0] / "bundle").exists())
            self.assertTrue((attempts[0] / "request.json").exists())


class PersistentCensusTests(unittest.TestCase):
    def test_census_failure_terminates_child_and_blocks_retry(self):
        import json
        import os
        import subprocess
        import sys
        import time
        from unittest.mock import patch

        from godot_mcp.review import CleanupUnverified, capture, run_owned

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("tool")
            pidfile = root / "pid"
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }
            script = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(0.3)'

            def run_child(command, timeout, evidence, label):
                return run_owned(
                    [sys.executable, "-c", script, str(pidfile)], 5, evidence, label
                )

            def fail_ps(command, **kwargs):
                return subprocess.CompletedProcess(
                    command, 1, "", "injected census failure"
                )

            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("godot_mcp.review.run_owned", side_effect=run_child),
                patch("subprocess.run", side_effect=fail_ps),
            ):
                with self.assertRaises(CleanupUnverified):
                    capture(root, "review")
                with self.assertRaises(CleanupUnverified):
                    capture(root, "review")
            pid = int(pidfile.read_text())
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("child remained alive")
            attempt = next((root / "review" / "attempts").iterdir())
            metadata = json.loads((attempt / "import.json").read_text())
            self.assertIn("inspect process groups", metadata["cleanup_error"])
            self.assertIn(
                "injected census failure", (attempt / "import.groups.json").read_text()
            )
            self.assertIsNotNone(metadata["execution_error"])
            self.assertTrue((root / "review" / ".capture.pending").exists())


class BrowserOwnershipTests(unittest.TestCase):
    def test_missing_initial_identity_still_reaps_owned_browser(self):
        import json
        import os
        import sys
        from unittest.mock import patch

        from godot_mcp.review import CleanupUnverified, run_with_browser

        with TemporaryDirectory() as d:
            root = Path(d)
            with (
                patch("godot_mcp.review.processes.inspect", return_value=None),
                self.assertRaises(CleanupUnverified),
            ):
                run_with_browser(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    root,
                    lambda port: None,
                    root,
                )
            info = json.loads((root / "browser-process.json").read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(info["pid"], 0)
            self.assertIsNotNone(info["cleanup_error"])


class RegisteredBrowserTests(unittest.TestCase):
    def test_browser_family_is_registered_sampled_and_reaped(self):
        import json
        import os
        import sys
        import time
        from unittest.mock import patch

        from godot_mcp.review import owned_exec, run_with_browser

        with TemporaryDirectory() as d:
            root = Path(d)
            profile = root / "profile"
            profile.mkdir()
            (profile / "DevToolsActivePort").write_text("1234\n")

            def task(port):
                self.assertEqual(port, 1234)
                time.sleep(0.3)

            with patch(
                "godot_mcp.review.owned_exec.command", wraps=owned_exec.command
            ) as gate:
                run_with_browser(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    profile,
                    task,
                    root,
                )
            self.assertTrue(gate.called)
            info = json.loads((root / "browser-process.json").read_text())
            self.assertIsNone(info["cleanup_error"])
            self.assertTrue(info["identity"]["birth"])
            self.assertTrue(info["known"])
            with self.assertRaises(ProcessLookupError):
                os.kill(info["pid"], 0)


class CoreOwnerTests(unittest.TestCase):
    def test_browser_registers_with_core_owner_and_is_retired(self):
        import os
        import sys

        from cavi_mcp_core import processes, supervision

        with TemporaryDirectory() as d:
            root = Path(d).resolve()
            profile = root / "profile"
            profile.mkdir()
            (profile / "DevToolsActivePort").write_text("1234\n")
            evidence = root / "evidence"
            evidence.mkdir()
            code = (
                "import sys,time; from pathlib import Path; from godot_mcp.review import run_with_browser; "
                'run_with_browser([sys.executable,"-c","import time;time.sleep(60)"], Path(sys.argv[1]), lambda port: time.sleep(.3), Path(sys.argv[2]))'
            )
            env = dict(os.environ)
            if env.get("CAVI_MCP_PROCESS_OWNER"):
                self.skipTest("An existing process owner cannot be replaced")
            result = supervision.run(
                [sys.executable, "-c", code, str(profile), str(evidence)],
                cwd=str(Path.cwd()),
                env=env,
                directory=root / "owner",
                max_seconds=20,
                max_rss_kib=1048576,
                minimum_available_kib=0,
            )
            self.assertEqual(result["exit_code"], 0, result)
            self.assertIsNone(result["error"], result)
            self.assertIsNone(result["cleanup_error"], result)
            records = list((root / "owner" / "records").glob("*.json"))
            self.assertGreaterEqual(len(records), 2)
            for record in records:
                import json

                row = json.loads(record.read_text())
                self.assertIsNone(processes.inspect(row["request"]["pid"]))


class CoreOwnedCommandTests(unittest.TestCase):
    def test_subprocesses_respect_existing_core_owner(self):
        import os
        import sys

        from cavi_mcp_core import supervision

        with TemporaryDirectory() as d:
            root = Path(d).resolve()
            evidence = root / "evidence"
            evidence.mkdir()
            code = (
                "import sys; from pathlib import Path; from godot_mcp.review import run_owned; "
                'run_owned([sys.executable,"-c","print(123)"], 5, Path(sys.argv[1]), "child")'
            )
            env = dict(os.environ)
            if env.get("CAVI_MCP_PROCESS_OWNER"):
                self.skipTest("An existing process owner cannot be replaced")
            result = supervision.run(
                [sys.executable, "-c", code, str(evidence)],
                cwd=str(Path.cwd()),
                env=env,
                directory=root / "owner",
                max_seconds=20,
                max_rss_kib=1048576,
                minimum_available_kib=0,
            )
            self.assertEqual(result["exit_code"], 0, result)
            self.assertIsNone(result["error"], result)
            self.assertIsNone(result["cleanup_error"], result)
            self.assertEqual((evidence / "child.stdout").read_text(), "123\n")
            self.assertGreaterEqual(
                len(list((root / "owner" / "records").glob("*.json"))), 2
            )


class EvidenceWriteFaultTests(unittest.TestCase):
    def test_uncertain_cleanup_remains_primary_when_evidence_write_fails(self):
        import os
        import subprocess
        import sys
        from unittest.mock import patch

        from godot_mcp.review import CleanupUnverified, atomic_json, capture, run_owned
        from godot_mcp.server import mcp

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("tool")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }

            def fail_ps(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, "", "census failed")

            def fail_evidence(path, value):
                if str(path).endswith(".groups.json"):
                    raise OSError("evidence unavailable")
                return atomic_json(path, value)

            def child(command, timeout, evidence, label):
                return run_owned(
                    [sys.executable, "-c", "import time;time.sleep(0.2)"],
                    5,
                    evidence,
                    label,
                )

            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("godot_mcp.review.run_owned", side_effect=child),
                patch("godot_mcp.review.atomic_json", side_effect=fail_evidence),
                patch("subprocess.run", side_effect=fail_ps),
            ):
                with self.assertRaises(CleanupUnverified) as failure:
                    capture(root, "review")
                self.assertEqual(
                    mcp.failure(
                        "godot_review_capture", failure.exception
                    ).structured_content["error"]["code"],
                    "CLEANUP_UNVERIFIED",
                )
                self.assertTrue((root / "review" / ".capture.pending").exists())
                with self.assertRaises(CleanupUnverified):
                    capture(root, "review")


class BoundedEvidenceTests(unittest.TestCase):
    def test_fifo_artifact_is_rejected_without_blocking(self):
        import os

        from godot_mcp.review import digest

        with TemporaryDirectory() as d:
            fifo = Path(d) / "pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "regular file"):
                digest(fifo)

    def test_oversize_receipt_is_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            (root / "review" / "review.json").write_bytes(b" " * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "bounded regular file"):
                inspect(root, "review")

    def test_oversize_artifact_is_rejected(self):
        import json

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            receipt = prepare(root, "mesh.glb", "review")
            big = root / "review" / "oversize"
            with big.open("wb") as f:
                f.truncate(256 * 1024 * 1024 + 1)
            receipt["artifacts"] = {"oversize": "0" * 64}
            (root / "review" / "review.json").write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "bounded regular file"):
                inspect(root, "review")


class OversizeSealTests(unittest.TestCase):
    def test_oversize_export_is_not_sealed(self):
        import os
        from unittest.mock import patch

        from godot_mcp.review import capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            tool = root / "tool"
            tool.write_text("tool")
            env = {
                key: str(tool)
                for key in (
                    "CAVI_GODOT_BIN",
                    "CAVI_GODOT_WEB_TEMPLATE",
                    "CAVI_CHROMIUM_BIN",
                    "CAVI_NODE_BIN",
                )
            }

            def fake_run(command, timeout, evidence, label):
                (evidence / (label + ".stderr")).write_text("")
                if label == "export":
                    with (Path(command[-1]).parent / "index.wasm").open("wb") as stream:
                        stream.truncate(256 * 1024 * 1024 + 1)
                if label == "browser":
                    path = Path(command[-1])
                    path.write_bytes(b"png")
                    Path(str(path) + ".json").write_text(
                        '{"camera_frame":{"center":[0,0,0],"distance":1},"geometry_preview_surfaces":0}'
                    )
                return "fixture"

            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("godot_mcp.review.run_owned", side_effect=fake_run),
                patch(
                    "godot_mcp.review.run_with_browser",
                    side_effect=lambda command, profile, task, evidence: task(1),
                ),
                self.assertRaisesRegex(RuntimeError, "bounded regular file"),
            ):
                capture(root, "review")
            self.assertEqual(inspect(root, "review")["state"], "prepared")
            attempt = next((root / "review" / "attempts").iterdir())
            self.assertFalse((attempt / "bundle").exists())


READY = "godot-review-ready "


@unittest.skipUnless(
    config.locate(config.GODOT) or os.environ.get("CI"), "Godot is not installed"
)
class ReviewLightingSceneTests(unittest.TestCase):
    def test_headless_lighting_keeps_one_camera_frame(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = textured_cubes()
        runtime = Path(review.__file__).parent / "runtime"
        godot = str(config.tools((config.GODOT,))[config.GODOT])
        distances = {}
        for lighting in ("studio", "clay", "sun"):
            project = root / lighting
            project.mkdir()
            for name in ("project.godot", "main.tscn", "main.gd", "pose.gd"):
                runtime_file = runtime / name
                if runtime_file.is_file():
                    shutil.copyfile(runtime_file, project / name)
            (project / "subject.glb").write_bytes(source)
            receipt = {"view": "three-quarter", "lighting": lighting}
            (project / review.SELECTION_FILE).write_text(
                json.dumps(scene_selection(receipt, fallback=False))
            )
            run = subprocess.run(
                [godot, "--headless", "--path", str(project), "--quit-after", "30"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            lines = [line for line in run.stdout.splitlines() if line.startswith(READY)]
            self.assertEqual(len(lines), 1, run.stdout + run.stderr)
            scene = json.loads(lines[0].removeprefix(READY))
            self.assertEqual(scene["lighting"], lighting)
            distances[lighting] = scene["camera_frame"]["distance"]
        self.assertEqual(distances["clay"], distances["studio"])
        self.assertEqual(distances["sun"], distances["studio"])
