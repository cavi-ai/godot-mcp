extends RefCounted

static func apply_pose(player: AnimationPlayer, animation_name: String, seconds: float, nodes: Array, bones: Array) -> void:
    player.stop()
    for item in nodes:
        item.node.transform = item.baseline
    for item in bones:
        item.node.set_bone_pose(item.index, item.baseline)
    player.play(animation_name)
    player.seek(seconds, true, true)

static func posed_aabb(mesh: MeshInstance3D) -> AABB:
    # The engine's skinning on the CPU; bake_mesh_from_current_skeleton_pose reads a rendering
    # server skeleton, which headless runs do not have.
    var skeleton := mesh.get_node(mesh.skeleton) as Skeleton3D
    var binds: Array[Transform3D] = []
    for i in mesh.skin.get_bind_count():
        var bone = mesh.skin.get_bind_bone(i)
        if mesh.skin.get_bind_name(i) != &"":
            bone = skeleton.find_bone(mesh.skin.get_bind_name(i))
        binds.append(skeleton.get_bone_global_pose(bone) * mesh.skin.get_bind_pose(i))
    var box: AABB
    var first = true
    for surface in mesh.mesh.get_surface_count():
        var arrays = mesh.mesh.surface_get_arrays(surface)
        var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
        var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
        var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
        var influences = bones.size() / vertices.size() if not vertices.is_empty() else 0
        for v in vertices.size():
            var point = vertices[v]
            if influences > 0:
                point = Vector3.ZERO
                for k in influences:
                    var weight = weights[v * influences + k]
                    if weight > 0.0:
                        point += binds[bones[v * influences + k]] * vertices[v] * weight
            box = AABB(point, Vector3.ZERO) if first else box.expand(point)
            first = false
    return box
