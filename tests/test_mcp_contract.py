import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cavi_mcp_core import owned_exec
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_support import missing_tools, textured_cubes


class UnknownOutcomeTests(unittest.TestCase):
    def test_unknown_outcomes_point_at_the_owned_output(self):
        from cavi_mcp_core.contracts import OperationUnknown

        from godot_mcp.server import mcp

        for name, action in (
            ("godot_review_capture", "godot_review_inspect"),
            ("godot_asset_inspect", "inspect_application_and_outputs"),
            ("godot_motion_inspect", "inspect_application_and_outputs"),
            ("godot_review_prepare", "inspect_application_and_outputs"),
        ):
            error = mcp.failure(name, OperationUnknown("uncertain")).structured_content
            self.assertEqual(error["error"]["outcome"], "unknown", name)
            self.assertEqual(error["error"]["next_action"], action, name)


class McpContractTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, workspace, cwd, name, args):
        env = dict(os.environ, CAVI_GAME_ART_WORKSPACE=workspace)
        command = owned_exec.command(
            [sys.executable, "-m", "godot_mcp.server"], env=env
        )
        params = StdioServerParameters(
            command=command[0], args=command[1:], env=env, cwd=cwd
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            return await session.call_tool(name, args)

    async def test_workspace_root_and_output_parent_are_refused_plainly(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "workspace"
            root.mkdir()
            (root / "asset.glb").write_bytes(textured_cubes())
            relative = await self.call(
                "workspace",
                d,
                "godot_review_prepare",
                {"source": "asset.glb", "directory": "review"},
            )
            self.assertTrue(relative.is_error)
            self.assertIn("absolute", relative.content[0].text)
            self.assertFalse((root / "review").exists())
            orphan = await self.call(
                str(root),
                d,
                "godot_review_prepare",
                {"source": "asset.glb", "directory": "missing/review"},
            )
            self.assertTrue(orphan.is_error)
            self.assertEqual(orphan.structured_content["error"]["outcome"], "rejected")
            self.assertNotIn(d, orphan.content[0].text)

    async def test_catalog_validation_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "asset.glb").write_bytes(textured_cubes())
            env = dict(os.environ, CAVI_GAME_ART_WORKSPACE=str(root), **missing_tools())
            command = owned_exec.command(
                [sys.executable, "-m", "godot_mcp.server"], env=env
            )
            params = StdioServerParameters(
                command=command[0], args=command[1:], env=env
            )
            async with (
                stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                self.assertEqual(
                    set(tools),
                    {
                        "godot_status",
                        "godot_asset_inspect",
                        "godot_motion_inspect",
                        "godot_review_prepare",
                        "godot_review_inspect",
                        "godot_review_capture",
                    },
                )
                for name, fields in (
                    ("godot_status", {"CAVI_GODOT_BIN", "CAVI_NODE_BIN"}),
                    ("godot_review_prepare", {"source_sha256", "state", "view"}),
                    ("godot_review_inspect", {"state", "cleanup_pending"}),
                    ("godot_review_capture", {"artifacts", "bundle", "state"}),
                    ("godot_asset_inspect", {"inputs", "report", "report_sha256"}),
                    ("godot_motion_inspect", {"config", "report", "report_sha256"}),
                ):
                    schema = tools[name].output_schema or {}
                    self.assertLessEqual(
                        fields, set(schema.get("properties", {})), name
                    )
                for name, tool in tools.items():
                    self.assertFalse(
                        tool.input_schema.get("additionalProperties", True), name
                    )
                    a = tool.annotations
                    self.assertIsNotNone(a)
                    self.assertEqual(
                        a.read_only_hint,
                        name in ("godot_status", "godot_review_inspect"),
                    )
                    self.assertFalse(a.destructive_hint)
                    self.assertFalse(a.open_world_hint)
                    self.assertEqual(
                        bool(a.idempotent_hint),
                        name in ("godot_status", "godot_review_inspect"),
                    )
                status = await session.call_tool("godot_status", {})
                self.assertFalse(status.is_error)
                for name, args in [
                    ("godot_status", {"surprise": True}),
                    (
                        "godot_asset_inspect",
                        {"source": "asset.glb", "directory": "out", "surprise": True},
                    ),
                    ("godot_asset_inspect", {}),
                    ("godot_motion_inspect", {}),
                    (
                        "godot_motion_inspect",
                        {
                            "source": "asset.glb",
                            "directory": "bad",
                            "player": "/p",
                            "animation": "walk",
                            "normalized_times": [0, True],
                        },
                    ),
                    (
                        "godot_motion_inspect",
                        {
                            "source": "asset.glb",
                            "directory": "bad",
                            "player": "/p",
                            "animation": "walk",
                            "normalized_times": [0, 0],
                        },
                    ),
                    (
                        "godot_asset_inspect",
                        {"source": "../outside.glb", "directory": "out"},
                    ),
                    (
                        "godot_asset_inspect",
                        {"source": "asset.glb", "directory": "out"},
                    ),
                    (
                        "godot_review_prepare",
                        {
                            "source": "asset.glb",
                            "directory": "extra",
                            "view": "front",
                            "surprise": True,
                        },
                    ),
                    ("godot_review_prepare", {}),
                    (
                        "godot_review_prepare",
                        {
                            "source": "asset.glb",
                            "directory": "bad-pose",
                            "pose": {
                                "player": "/p",
                                "animation": "walk",
                                "time_seconds": True,
                            },
                        },
                    ),
                    (
                        "godot_review_prepare",
                        {
                            "source": "asset.glb",
                            "directory": "bad-pose",
                            "pose": {
                                "player": "/p",
                                "animation": "walk",
                                "time_seconds": 1,
                                "extra": 1,
                            },
                        },
                    ),
                    (
                        "godot_review_prepare",
                        {"source": "asset.glb", "directory": "bad", "view": "wrong"},
                    ),
                    (
                        "godot_review_prepare",
                        {
                            "source": "../outside.glb",
                            "directory": "bad",
                            "view": "front",
                        },
                    ),
                ]:
                    result = await session.call_tool(name, args)
                    self.assertTrue(result.is_error, name)
                    self.assertFalse(result.structured_content["error"]["retry_safe"])
                prepared = await session.call_tool(
                    "godot_review_prepare",
                    {"source": "asset.glb", "directory": "review", "view": "front"},
                )
                self.assertFalse(prepared.is_error)
                inspected = await session.call_tool(
                    "godot_review_inspect", {"directory": "review"}
                )
                self.assertFalse(inspected.is_error)
                self.assertFalse(inspected.structured_content["cleanup_pending"])
                for name in ("godot_review_inspect", "godot_review_capture"):
                    extra = await session.call_tool(
                        name, {"directory": "review", "surprise": True}
                    )
                    self.assertTrue(extra.is_error)
                    self.assertEqual(
                        extra.structured_content["error"]["code"], "INVALID_ARGUMENT"
                    )
                missing = await session.call_tool(
                    "godot_review_capture", {"directory": "review"}
                )
                self.assertTrue(missing.is_error)
                (root / "review" / ".capture.pending").write_text(
                    json.dumps({"kind": "capture_pending"})
                )
                blocked = await session.call_tool(
                    "godot_review_capture", {"directory": "review"}
                )
                self.assertTrue(blocked.is_error)
                self.assertEqual(
                    blocked.structured_content["error"]["code"], "CLEANUP_UNVERIFIED"
                )
                self.assertEqual(
                    blocked.structured_content["error"]["outcome"], "unknown"
                )
                self.assertFalse(blocked.structured_content["error"]["retry_safe"])
                self.assertEqual(
                    blocked.structured_content["error"]["next_action"],
                    "godot_review_inspect",
                )
                inspection = await session.call_tool(
                    "godot_review_inspect", {"directory": "review"}
                )
                self.assertTrue(inspection.structured_content["cleanup_pending"])
