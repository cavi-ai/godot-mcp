import unittest

from godot_mcp import inspection

REST = {"origin": [0, 0, 0], "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}


def surface(index, triangles):
    return {
        "node": "/A",
        "surface": index,
        "primitive": 3,
        "vertices": 3,
        "triangles": triangles,
        "degenerate_triangles": 0,
        "uv0": False,
        "uv0_outside_unit": False,
        "uv1": False,
        "material": None,
    }


def report(bounds):
    surfaces = [surface(0, 1), surface(1, 2)]
    bones = [
        {
            "skeleton": "/A",
            "index": 0,
            "name": "root",
            "parent": -1,
            "rest_transform": REST,
        }
    ]
    return {
        "status": "ok",
        "godot_version": {},
        "units": "metres",
        "bounds_semantics": "imported rest pose",
        "totals": {
            "nodes": 1,
            "surfaces": len(surfaces),
            "bones": len(bones),
            "clips": 0,
            "unsupported_primitives": 0,
        },
        "qa": {
            "triangles": 3,
            "vertices": 6,
            "degenerate_triangles": 0,
            "uv0_outside_unit_surfaces": 0,
            "materials": 0,
            "textures": 0,
            "texture_rgba8_bytes": 0,
            "max_texture_size": 0,
            "npot_textures": 0,
            "max_bone_influences": 0,
            "zero_weight_vertices": 0,
            "unnormalized_weight_vertices": 0,
            "max_blend_shapes": 0,
            "mirrored_meshes": 0,
            "non_uniform_scale_meshes": 0,
            "animation_keys": 0,
            "bounds": bounds,
        },
        "truncated": {key: False for key in ("nodes", "surfaces", "bones", "clips")},
        "nodes": [{"path": "/A", "parent": "", "type": "Node3D"}],
        "surfaces": surfaces,
        "bones": bones,
        "clips": [],
    }


class BudgetTests(unittest.TestCase):
    def test_met_limits_read_surface_and_bone_totals(self):
        checked = inspection.validate_report(
            report({"position": [0, 0, 0], "size": [1, 1, 1]})
        )
        result = inspection.evaluate(
            {"triangles": 3, "draw_calls": 2, "bones": 1},
            checked,
        )
        checks = {check["rule"]: check for check in result["checks"]}
        self.assertEqual(checks["draw_calls"]["value"], checked["totals"]["surfaces"])
        self.assertEqual(checks["bones"]["value"], checked["totals"]["bones"])
        self.assertEqual(checks["draw_calls"]["value"], 2)
        self.assertEqual(checks["bones"]["value"], 1)
        self.assertTrue(checks["triangles"]["within"])
        self.assertTrue(checks["draw_calls"]["within"])
        self.assertTrue(checks["bones"]["within"])
        self.assertTrue(result["within"])

    def test_missing_bounds_fails_base_offset(self):
        checked = inspection.validate_report(report(None))
        self.assertIsNone(checked["qa"]["bounds"])
        result = inspection.evaluate({"triangles": 3, "base_offset": 1.0}, checked)
        checks = {check["rule"]: check for check in result["checks"]}
        self.assertIsNone(checks["base_offset"]["value"])
        self.assertFalse(checks["base_offset"]["within"])
        self.assertTrue(checks["triangles"]["within"])
        self.assertFalse(result["within"])
