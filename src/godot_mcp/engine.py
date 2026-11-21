"""A Godot runtime script run on a copy of a source GLB, bound to its inputs."""

import shutil
import tempfile
from pathlib import Path

from cavi_mcp_core import files
from cavi_mcp_core.contracts import OperationUnknown

from . import config, review


def run(
    src,
    dest,
    source,
    *,
    kind,
    name,
    script,
    settings,
    report_limit,
    validate,
    assess=None,
):
    """Run ``script`` headless on a copy of ``src`` and record it in the new ``dest``.

    ``dest`` receives owner.json, inputs.json, the engine logs, report.json and
    receipt.json. ``settings`` is written as the script's config.json. ``assess``
    maps the validated report to extra receipt fields. The pending marker and
    project stay when process cleanup is unverified.
    """
    godot = config.tools((config.GODOT,))[config.GODOT]
    runtime = Path(__file__).parent / "runtime" / script
    snapshot = {
        "source_sha256": review.digest(src, review.LIMIT),
        "engine_sha256": review.digest(godot),
        "provider": review.provider_identity(),
    }
    request = {"source": source, **({} if settings is None else {"config": settings})}
    dest.mkdir()
    pending = dest / f".{name}.pending"
    project = None
    try:
        review.atomic_json(dest / "owner.json", {"kind": kind, "source": source})
        review.atomic_json(pending, {"kind": f"{name}_pending"})
        review.atomic_json(dest / "inputs.json", {**request, "inputs": snapshot})
        project = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=dest))
        (project / "project.godot").write_text(f'[application]\nconfig/name="{name}"\n')
        shutil.copyfile(src, project / "subject.glb")
        shutil.copyfile(runtime, project / script)
        if script == "motion.gd":
            shutil.copyfile(runtime.with_name("pose.gd"), project / "pose.gd")
        if settings is not None:
            review.atomic_json(project / "config.json", settings)

        def copies_unchanged():
            return (
                review.digest(project / "subject.glb", review.LIMIT)
                == snapshot["source_sha256"]
                and review.digest(project / script)
                == snapshot["provider"]["runtime"][script]
                and (
                    script != "motion.gd"
                    or review.digest(project / "pose.gd")
                    == snapshot["provider"]["runtime"]["pose.gd"]
                )
                and (
                    settings is None
                    or files.load_json(project / "config.json") == settings
                )
            )

        if not copies_unchanged():
            raise ValueError("Engine inputs changed while copying")
        review.run_owned(
            [
                str(godot),
                "--headless",
                "--path",
                str(project),
                "--script",
                "res://" + script,
            ],
            120,
            dest,
            "godot",
        )
        if (
            b"ERROR:" in (dest / "godot.stderr").read_bytes()
            or b"ERROR:" in (dest / "godot.stdout").read_bytes()
        ):
            raise ValueError("Godot reported an error")
        report = validate(
            files.parse_json(files.read_bytes(project / "report.json", report_limit))
        )
        assessment = {} if assess is None else assess(report)
        if (
            review.digest(src, review.LIMIT) != snapshot["source_sha256"]
            or review.digest(godot) != snapshot["engine_sha256"]
            or review.provider_identity() != snapshot["provider"]
            or not copies_unchanged()
        ):
            raise ValueError("Engine inputs changed")
        review.atomic_json(dest / "report.json", report)
        receipt = {
            "kind": kind,
            **request,
            "inputs": snapshot,
            "report_sha256": review.digest(dest / "report.json", report_limit),
            **assessment,
        }
        review.atomic_json(dest / "receipt.json", receipt)
        shutil.rmtree(project)
        project = None
        pending.unlink(missing_ok=True)
        return {**receipt, "report": report}
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
