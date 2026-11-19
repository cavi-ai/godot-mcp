import json
import struct
import zlib

from godot_mcp.config import REVIEW_TOOLS


def missing_tools():
    """Tool variables naming no file, so installed tools are not found instead."""
    return dict.fromkeys(REVIEW_TOOLS, "/nonexistent/tool")


def glb(extra):
    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0"))
        + chunk(b"IEND", b"")
    )
    binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0) + png
    data = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"source": 0}],
        "images": [{"bufferView": 1, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}
        ],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": len(png)},
        ],
        **extra,
    }
    raw = json.dumps(data).encode()
    raw += b" " * (-len(raw) % 4)
    binary += b"\0" * (-len(binary) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(raw) + len(binary))
        + struct.pack("<I4s", len(raw), b"JSON")
        + raw
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def animated(clips):
    """A triangle with one clip per (name, length); length is the sampler input max."""
    accessors = [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}]
    animations = []
    for name, length in clips:
        accessors.append(
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 1,
                "type": "SCALAR",
                "min": [0],
                "max": [length],
            }
        )
        animation = {
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}],
            "samplers": [{"input": len(accessors) - 1, "output": 0}],
        }
        if name is not None:
            animation["name"] = name
        animations.append(animation)
    return glb({"accessors": accessors, "animations": animations})


def raw_glb(document):
    """A GLB whose JSON chunk holds ``document`` bytes as given."""
    document += b" " * (-len(document) % 4)
    binary = b"\0" * 36
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(document) + len(binary))
        + struct.pack("<I4s", len(document), b"JSON")
        + document
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def textured_cubes(count=1, slide=None, size=1.0):
    """A small embedded-texture fixture; multiple nodes share one cube mesh.

    ``slide`` names a one-second clip moving the first cube from x = 0 to x = 1.
    ``size`` is the cube edge length in metres.
    """

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    rows = b"\0\xff\x20\x20\x20\xff\x20\0\x20\x20\xff\xff\xff\x20"
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    faces = [
        ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
        ((1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)),
        ((1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),
        ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),
        ((-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)),
        ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)),
    ]
    positions = [c * size / 2 for face in faces for point in face for c in point]
    uvs = [v for _ in faces for v in (0, 1, 1, 1, 1, 0, 0, 0)]
    indices = [
        index
        for i in range(6)
        for index in (4 * i, 4 * i + 1, 4 * i + 2, 4 * i, 4 * i + 2, 4 * i + 3)
    ]
    pos = struct.pack("<72f", *positions)
    uv = struct.pack("<48f", *uvs)
    ind = struct.pack("<36H", *indices)
    binary = pos + uv + ind + png
    keys = len(binary) + -len(binary) % 4
    if slide is not None:
        binary += b"\0" * (keys - len(binary)) + struct.pack(
            "<8f", 0, 1, 0, 0, 0, 1, 0, 0
        )
    data = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(count))}],
        "nodes": [
            {"name": f"Cube{i}", "mesh": 0, "translation": [i * 1.4 * size, 0, 0]}
            for i in range(count)
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                        "indices": 2,
                        "material": 0,
                    }
                ]
            }
        ],
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0,
                }
            }
        ],
        "textures": [{"source": 0}],
        "images": [{"bufferView": 3, "mimeType": "image/png"}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(uv)},
            {"buffer": 0, "byteOffset": len(pos) + len(uv), "byteLength": len(ind)},
            {
                "buffer": 0,
                "byteOffset": len(pos) + len(uv) + len(ind),
                "byteLength": len(png),
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 24,
                "type": "VEC3",
                "min": [-size / 2] * 3,
                "max": [size / 2] * 3,
            },
            {"bufferView": 1, "componentType": 5126, "count": 24, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
    }
    if slide is not None:
        data["bufferViews"] += [
            {"buffer": 0, "byteOffset": keys, "byteLength": 8},
            {"buffer": 0, "byteOffset": keys + 8, "byteLength": 24},
        ]
        data["accessors"] += [
            {
                "bufferView": 4,
                "componentType": 5126,
                "count": 2,
                "type": "SCALAR",
                "min": [0],
                "max": [1],
            },
            {"bufferView": 5, "componentType": 5126, "count": 2, "type": "VEC3"},
        ]
        data["animations"] = [
            {
                "name": slide,
                "channels": [
                    {"sampler": 0, "target": {"node": 0, "path": "translation"}}
                ],
                "samplers": [{"input": 3, "output": 4}],
            }
        ]
    raw = json.dumps(data).encode()
    raw += b" " * (-len(raw) % 4)
    binary += b"\0" * (-len(binary) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(raw) + len(binary))
        + struct.pack("<I4s", len(raw), b"JSON")
        + raw
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def skinned_cube(lift):
    """A 1 m cube skinned to one bone; the one-second clip "Lift" raises the bone by ``lift``."""
    corners = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4)]
    quads.append((1, 5, 7, 3))
    parts = [
        struct.pack("<24f", *(c for corner in corners for c in corner)),
        struct.pack("<36H", *(i for a, b, c, d in quads for i in (a, b, c, a, c, d))),
        bytes(32),
        struct.pack("<32f", *[1, 0, 0, 0] * 8),
        struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1),
        struct.pack("<2f", 0, 1),
        struct.pack("<6f", 0, 0, 0, 0, lift, 0),
    ]
    views = []
    for part in parts:
        offset = sum(view["byteLength"] for view in views)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(part)})
    kinds = [
        (5126, 8, "VEC3"),
        (5123, 36, "SCALAR"),
        (5121, 8, "VEC4"),
        (5126, 8, "VEC4"),
        (5126, 1, "MAT4"),
        (5126, 2, "SCALAR"),
        (5126, 2, "VEC3"),
    ]
    accessors = [
        {"bufferView": i, "componentType": component, "count": count, "type": kind}
        for i, (component, count, kind) in enumerate(kinds)
    ]
    accessors[0] |= {"min": [-0.5] * 3, "max": [0.5] * 3}
    accessors[5] |= {"min": [0], "max": [1]}
    binary = b"".join(parts)
    raw = json.dumps(
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0, 1]}],
            "nodes": [{"name": "Body", "mesh": 0, "skin": 0}, {"name": "Bone"}],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {
                                "POSITION": 0,
                                "JOINTS_0": 2,
                                "WEIGHTS_0": 3,
                            },
                            "indices": 1,
                        }
                    ]
                }
            ],
            "skins": [{"joints": [1], "inverseBindMatrices": 4}],
            "animations": [
                {
                    "name": "Lift",
                    "channels": [
                        {"sampler": 0, "target": {"node": 1, "path": "translation"}}
                    ],
                    "samplers": [{"input": 5, "output": 6}],
                }
            ],
            "accessors": accessors,
            "buffers": [{"byteLength": len(binary)}],
            "bufferViews": views,
        }
    ).encode()
    raw += b" " * (-len(raw) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(raw) + len(binary))
        + struct.pack("<I4s", len(raw), b"JSON")
        + raw
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )
