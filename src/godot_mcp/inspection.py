"""Source-bound Godot runtime inspection of self-contained GLB files."""

import os
import shutil
import tempfile
from pathlib import Path

from cavi_mcp_core import files
from cavi_mcp_core.contracts import OperationUnknown

from . import review


def validate_glb(path):
    return review.read_glb(path, static_review=False)


def inspect_asset(root, source, directory):
    src = review.inside(root, source)
    dest = review.inside(root, directory)
    if not src.is_file() or src.suffix.lower() != ".glb":
        raise ValueError("Source must be a GLB file")
    validate_glb(src)
    configured = os.environ.get("CAVI_GODOT_BIN")
    if not configured:
        raise ValueError("CAVI_GODOT_BIN is required")
    engine = Path(configured).resolve()
    if not engine.is_file():
        raise ValueError("Godot executable must be a file")
    runtime = Path(__file__).parent / "runtime" / "inspect.gd"
    snapshot = {
        "source_sha256": review.digest(src, review.LIMIT),
        "engine_sha256": review.digest(engine),
        "provider": review.provider_identity(),
    }
    dest.mkdir(parents=False, exist_ok=False)
    pending = dest / ".inspection.pending"
    project = None
    try:
        review.atomic_json(
            dest / "owner.json", {"kind": "godot-asset-inspection", "source": source}
        )
        review.atomic_json(pending, {"kind": "inspection_pending"})
        review.atomic_json(dest / "inputs.json", {"source": source, "inputs": snapshot})
        project = Path(tempfile.mkdtemp(prefix=".inspection-", dir=dest))
        (project / "project.godot").write_text(
            '[application]\nconfig/name="Inspection"\n'
        )
        shutil.copyfile(src, project / "subject.glb")
        shutil.copyfile(runtime, project / "inspect.gd")
        validate_glb(project / "subject.glb")
        if (
            review.digest(project / "subject.glb", review.LIMIT)
            != snapshot["source_sha256"]
            or review.digest(project / "inspect.gd")
            != snapshot["provider"]["runtime"]["inspect.gd"]
        ):
            raise ValueError("Inspection inputs changed while copying")
        review.run_owned(
            [
                str(engine),
                "--headless",
                "--path",
                str(project),
                "--script",
                "res://inspect.gd",
            ],
            120,
            dest,
            "godot",
        )
        if (
            b"ERROR:" in (dest / "godot.stderr").read_bytes()
            or b"ERROR:" in (dest / "godot.stdout").read_bytes()
        ):
            raise ValueError("Godot reported an inspection error")
        report = files.load_json(project / "report.json")
        if not isinstance(report, dict) or report.get("status") != "ok":
            raise ValueError("Invalid Godot inspection report")
        if (
            review.digest(src, review.LIMIT) != snapshot["source_sha256"]
            or review.digest(engine) != snapshot["engine_sha256"]
            or review.provider_identity() != snapshot["provider"]
        ):
            raise ValueError("Inspection inputs changed")
        review.atomic_json(dest / "report.json", report)
        result = {
            "kind": "godot-asset-inspection",
            "source": source,
            "inputs": snapshot,
            "report_sha256": review.digest(dest / "report.json", 1024 * 1024),
            "report": report,
        }
        review.atomic_json(dest / "receipt.json", result)
        shutil.rmtree(project)
        project = None
        pending.unlink(missing_ok=True)
        return result
    except review.CleanupUnverified:
        # The process may still use the project. Preserve both project and marker.
        raise
    except Exception as exc:
        cleanup_error = None
        if project is not None:
            try:
                shutil.rmtree(project)
            except OSError as error:
                cleanup_error = error
        try:
            pending.unlink(missing_ok=True)
        except OSError as error:
            cleanup_error = cleanup_error or error
        message = str(exc)
        if cleanup_error is not None:
            message += f"; output cleanup failed: {cleanup_error}"
        raise OperationUnknown(message) from exc
