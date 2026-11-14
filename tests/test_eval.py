import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
from run import case_passed


class EvalTests(unittest.TestCase):
    def test_unknown_error_cannot_pass_rejected_case(self):
        case = {
            "kind": "invalid",
            "tool": "godot_status",
            "code": "INVALID_ARGUMENT",
            "outcome": "rejected",
        }
        error = {
            "code": "OUTCOME_UNKNOWN",
            "outcome": "unknown",
            "operation": "godot_status",
            "retry_safe": False,
        }
        self.assertFalse(case_passed(case, True, {"error": error}))
        error.update(code="INVALID_ARGUMENT", outcome="rejected")
        self.assertTrue(case_passed(case, True, {"error": error}))


class EvaluatorBindingTests(unittest.TestCase):
    def test_changed_evaluator_is_rejected(self):
        import hashlib
        import json
        import tempfile

        from evaluator import validate_binding

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / "bin" / "mcpeval"
            binary.parent.mkdir()
            binary.write_bytes(b"first")
            revision = "a" * 40
            source = f"mcpeval 0.8.0 (git+https://github.com/cavi-ai/mcp-eval?branch=main#{revision})"
            metadata = root / ".crates2.json"
            metadata.write_text(json.dumps({"installs": {source: {}}}))
            record = {
                "returncode": 0,
                "version_returncode": 0,
                "command": ["cargo", "install"],
                "started": "start",
                "finished": "finish",
                "source_revision": revision,
                "install_source": source,
                "metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
                "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "version": "mcpeval 0.8.0",
            }
            for name in ("stdout", "stderr"):
                (root / ("build." + name)).write_bytes(b"log")
                record[name + "_sha256"] = hashlib.sha256(b"log").hexdigest()
            (root / "build-record.json").write_text(json.dumps(record))
            self.assertEqual(validate_binding(binary)["source_revision"], revision)
            binary.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "executable"):
                validate_binding(binary)
