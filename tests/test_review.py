import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_support import glb

from godot_mcp.review import inspect, prepare


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

    def test_changed_source_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            source = root / "mesh.glb"
            source.write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            source.write_bytes(source.read_bytes() + b"x")
            with self.assertRaisesRegex(ValueError, "changed"):
                inspect(root, "review")


class CaptureTests(unittest.TestCase):
    def test_missing_tools_do_not_replace_prepared_review(self):
        from godot_mcp.review import capture

        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "mesh.glb").write_bytes(glb({}))
            prepare(root, "mesh.glb", "review")
            with self.assertRaises((KeyError, ValueError, FileNotFoundError)):
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
    def test_same_runtime_is_shared_by_hardlink(self):
        from godot_mcp.review import share_runtime

        with TemporaryDirectory() as d:
            root = Path(d)
            a = root / "a"
            b = root / "b"
            a.write_bytes(b"runtime")
            b.write_bytes(b"runtime")
            share_runtime(root, a)
            share_runtime(root, b)
            self.assertEqual(a.stat().st_ino, b.stat().st_ino)


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
                with self.assertRaisesRegex(ValueError, "Copied source changed"):
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
                    out = Path(command[-2])
                    out.write_bytes(b"png")
                    Path(str(out) + ".json").write_text("{}")
                    tool.write_text("v2")
                return "fixture"

            with (
                patch.dict(os.environ, env),
                patch("importlib.metadata.version", return_value="fixture"),
                patch("godot_mcp.review.run_owned", side_effect=fake_run),
                self.assertRaisesRegex(ValueError, "input changed during capture"),
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
