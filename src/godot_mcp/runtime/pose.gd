extends RefCounted

static func apply_pose(player: AnimationPlayer, animation_name: String, seconds: float, nodes: Array, bones: Array) -> void:
    player.stop()
    for item in nodes:
        item.node.transform = item.baseline
    for item in bones:
        item.node.set_bone_pose(item.index, item.baseline)
    player.play(animation_name)
    player.seek(seconds, true, true)

static func posed_aabb(mesh: MeshInstance3D, include_morphs: bool = false) -> AABB:
    # The engine's skinning on the CPU; bake_mesh_from_current_skeleton_pose reads a rendering
    # server skeleton, which headless runs do not have.
    var skeleton := mesh.get_node(mesh.skeleton) as Skeleton3D
    var binds: Array[Transform3D] = []
    for i in mesh.skin.get_bind_count():
        var bone = mesh.skin.get_bind_bone(i)
        if mesh.skin.get_bind_name(i) != &"":
            bone = skeleton.find_bone(mesh.skin.get_bind_name(i))
        binds.append(skeleton.get_bone_global_pose(bone) * mesh.skin.get_bind_pose(i))
    var active_shapes = []
    if include_morphs and mesh.mesh is ArrayMesh:
        for i in mesh.mesh.get_blend_shape_count():
            var weight = mesh.get_blend_shape_value(i)
            if weight != 0.0:
                active_shapes.append({"index": i, "weight": weight})
    var box: AABB
    var first = true
    for surface in mesh.mesh.get_surface_count():
        var arrays = mesh.mesh.surface_get_arrays(surface)
        var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
        var shape_vertices = []
        if not active_shapes.is_empty():
            var shapes = mesh.mesh.surface_get_blend_shape_arrays(surface)
            for shape in active_shapes:
                shape_vertices.append(shapes[shape.index][Mesh.ARRAY_VERTEX])
        var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
        var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
        var influences = bones.size() / vertices.size() if not vertices.is_empty() else 0
        for v in vertices.size():
            var vertex = vertices[v]
            for i in active_shapes.size():
                var target: Vector3 = shape_vertices[i][v]
                if mesh.mesh.blend_shape_mode == Mesh.BLEND_SHAPE_MODE_NORMALIZED:
                    target -= vertices[v]
                vertex += target * active_shapes[i].weight
            var point = vertex
            if influences > 0:
                point = Vector3.ZERO
                for k in influences:
                    var weight = weights[v * influences + k]
                    if weight > 0.0:
                        point += binds[bones[v * influences + k]] * vertex * weight
            box = AABB(point, Vector3.ZERO) if first else box.expand(point)
            first = false
    return box
