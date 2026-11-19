"""Evaluate the actual stdio server without launching native applications."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cavi_mcp_core import owned_exec
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from evaluator import validate_binding
from test_support import textured_cubes


def case_passed(case, is_error, content):
    if case["kind"] == "success":
        return not is_error and (
            "pending" not in case or content.get("cleanup_pending") == case["pending"]
        )
    error = (content or {}).get("error", {})
    return bool(
        is_error
        and error.get("code") == case["code"]
        and error.get("outcome") == case["outcome"]
        and error.get("operation") == case["tool"]
        and error.get("retry_safe") is False
    )


async def exercise(env):
    cases = json.loads((ROOT / "evals/cases.json").read_text())
    command = owned_exec.command([sys.executable, "-m", "godot_mcp.server"], env=env)
    params = StdioServerParameters(command=command[0], args=command[1:], env=env)
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        expected = {
            "godot_status",
            "godot_asset_inspect",
            "godot_motion_inspect",
            "godot_review_prepare",
            "godot_review_inspect",
            "godot_review_capture",
        }
        if set(tools) != expected or {case["tool"] for case in cases} != expected:
            raise ValueError("Tool catalog or corpus differs from the expected surface")
        for tool in tools.values():
            a = tool.annotations
            readonly = tool.name in {"godot_status", "godot_review_inspect"}
            if not a or (
                a.read_only_hint,
                a.destructive_hint,
                a.idempotent_hint,
                a.open_world_hint,
            ) != (readonly, False, readonly, False):
                raise ValueError("Incorrect tool annotations: " + tool.name)
            if tool.input_schema.get("additionalProperties") is not False:
                raise ValueError("Tool accepts unknown arguments: " + tool.name)
        results = []
        for case in cases:
            if case.get("setup") == "pending":
                marker = (
                    Path(env["CAVI_GAME_ART_WORKSPACE"]) / "review" / ".capture.pending"
                )
                marker.write_text(json.dumps({"kind": "capture_pending"}))
            reply = await session.call_tool(case["tool"], case["arguments"])
            passed = case_passed(
                case, bool(reply.is_error), reply.structured_content or {}
            )
            results.append(
                {
                    "id": case["id"],
                    "tool": case["tool"],
                    "kind": case["kind"],
                    "arguments": case["arguments"],
                    "passed": passed,
                    "result": reply.model_dump(mode="json"),
                }
            )
        return results


def main():
    evaluator = Path(os.environ["MCPEVAL_EXECUTABLE"]).resolve(strict=True)
    binding = validate_binding(evaluator)
    revision = binding["source_revision"]
    expected_revision = os.environ.get("MCPEVAL_SOURCE_REVISION")
    if expected_revision and expected_revision != revision:
        raise ValueError(
            "Evaluator source revision differs from the requested revision"
        )
    base = ROOT / ".tmp/evals"
    base.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="protocol-", dir=base))
    workspace = output / "workspace"
    workspace.mkdir()
    (workspace / "asset.glb").write_bytes(textured_cubes())
    env = dict(
        os.environ,
        CAVI_GAME_ART_WORKSPACE=str(workspace),
        MCPEVAL_HOME=str(output / "store"),
    )
    for key in (
        "CAVI_GODOT_BIN",
        "CAVI_GODOT_WEB_TEMPLATE",
        "CAVI_CHROMIUM_BIN",
        "CAVI_NODE_BIN",
    ):
        env[key] = "/nonexistent/tool"
    version = subprocess.run(
        owned_exec.command([str(evaluator), "--version"], env=env),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    if version.stdout.strip() != binding["version"]:
        raise ValueError("Evaluator binary version differs from its install metadata")
    (output / "evaluator.json").write_text(json.dumps(binding, indent=2) + "\n")
    results = asyncio.run(exercise(env))
    (output / "cases.json").write_text(json.dumps(results, indent=2) + "\n")
    command = [
        str(evaluator),
        "probe",
        "--server",
        "godot",
        "--manifest",
        str(ROOT / "evals/protocol.manifest.json"),
        "--format",
        "json",
        "--gate-only",
        "--",
        sys.executable,
        "-m",
        "godot_mcp.server",
    ]
    result = subprocess.run(
        owned_exec.command(command, cwd=ROOT, env=env),
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    (output / "mcpeval.json").write_text(result.stdout)
    (output / "mcpeval.stderr").write_text(result.stderr)
    summary = {
        "cases": len(results),
        "failed": [case["id"] for case in results if not case["passed"]],
        "mcpeval_exit": result.returncode,
        "artifacts": str(output),
    }
    print(json.dumps(summary, indent=2))
    if summary["failed"] or result.returncode:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
