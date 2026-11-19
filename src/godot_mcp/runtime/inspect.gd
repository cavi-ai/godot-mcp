extends SceneTree

const MAX_NODES = 256
const MAX_SURFACES = 256
const MAX_BONES = 256
const MAX_CLIPS = 128
var nodes: Array = []
var surfaces: Array = []
var bones: Array = []
var clips: Array = []
var totals = {"nodes": 0, "surfaces": 0, "bones": 0, "clips": 0, "unsupported_primitives": 0}

func vec(v: Vector3) -> Array:
    return [v.x, v.y, v.z]

func transform(t: Transform3D) -> Dictionary:
    return {"origin": vec(t.origin), "basis": [vec(t.basis.x), vec(t.basis.y), vec(t.basis.z)]}

func bounds(a: AABB) -> Dictionary:
    return {"position": vec(a.position), "size": vec(a.size)}

func material_info(material: Material) -> Variant:
    if material == null:
        return null
    var result = {"name": material.resource_name, "type": material.get_class(), "textures": {}}
    if material is BaseMaterial3D:
        for slot in [["base_color", BaseMaterial3D.TEXTURE_ALBEDO], ["normal", BaseMaterial3D.TEXTURE_NORMAL], ["orm", BaseMaterial3D.TEXTURE_ORM], ["metallic", BaseMaterial3D.TEXTURE_METALLIC], ["roughness", BaseMaterial3D.TEXTURE_ROUGHNESS], ["occlusion", BaseMaterial3D.TEXTURE_AMBIENT_OCCLUSION], ["emissive", BaseMaterial3D.TEXTURE_EMISSION]]:
            var texture = material.get_texture(slot[1])
            if texture != null:
                result.textures[slot[0]] = {"name": texture.resource_name, "width": texture.get_width(), "height": texture.get_height()}
    return result

func visit(node: Node, parent_path: String, parent_world: Transform3D) -> void:
    var path = parent_path + "/" + str(node.name)
    var world = parent_world
    var record = {"path": path, "parent": parent_path, "type": node.get_class()}
    if node is Node3D:
        world = parent_world * node.transform
        record["local_transform"] = transform(node.transform)
        record["world_transform"] = transform(world)
    if node is MeshInstance3D and node.mesh != null:
        var mesh_bounds = node.mesh.get_aabb()
        record["mesh_rest_local_bounds"] = bounds(mesh_bounds)
        record["mesh_rest_world_bounds"] = bounds(world * mesh_bounds)
    totals.nodes += 1
    if nodes.size() < MAX_NODES:
        nodes.append(record)
    if node is MeshInstance3D and node.mesh != null:
        var mesh = node.mesh
        for index in mesh.get_surface_count():
            totals.surfaces += 1
            var primitive = mesh.surface_get_primitive_type(index)
            var format = mesh.surface_get_format(index)
            var vertices = mesh.surface_get_array_len(index)
            var indices = mesh.surface_get_array_index_len(index) if format & Mesh.ARRAY_FORMAT_INDEX else 0
            if primitive != Mesh.PRIMITIVE_TRIANGLES:
                totals.unsupported_primitives += 1
            if surfaces.size() < MAX_SURFACES:
                surfaces.append({"node": path, "surface": index, "primitive": primitive, "vertices": vertices, "triangles": int((indices if indices > 0 else vertices) / 3) if primitive == Mesh.PRIMITIVE_TRIANGLES else null, "uv0": format & Mesh.ARRAY_FORMAT_TEX_UV != 0, "uv1": format & Mesh.ARRAY_FORMAT_TEX_UV2 != 0, "material": material_info(node.get_active_material(index))})
    if node is Skeleton3D:
        for index in node.get_bone_count():
            totals.bones += 1
            if bones.size() < MAX_BONES:
                bones.append({"skeleton": path, "index": index, "name": node.get_bone_name(index), "parent": node.get_bone_parent(index), "rest_transform": transform(node.get_bone_rest(index))})
    if node is AnimationPlayer:
        for name in node.get_animation_list():
            totals.clips += 1
            if clips.size() < MAX_CLIPS:
                var animation = node.get_animation(name)
                clips.append({"player": path, "name": str(name), "duration": animation.length, "tracks": animation.get_track_count()})
    for child in node.get_children():
        visit(child, path, world)

func _initialize() -> void:
    var document = GLTFDocument.new()
    var state = GLTFState.new()
    var error = document.append_from_file("res://subject.glb", state)
    if error != OK:
        push_error("GLB import failed: " + str(error))
        quit(1)
        return
    var scene = document.generate_scene(state)
    if scene == null:
        push_error("GLB scene generation failed")
        quit(1)
        return
    visit(scene, "", Transform3D.IDENTITY)
    var report = {"status": "ok", "godot_version": Engine.get_version_info(), "units": "metres", "bounds_semantics": "imported rest pose", "totals": totals, "truncated": {"nodes": totals.nodes > nodes.size(), "surfaces": totals.surfaces > surfaces.size(), "bones": totals.bones > bones.size(), "clips": totals.clips > clips.size()}, "nodes": nodes, "surfaces": surfaces, "bones": bones, "clips": clips}
    var file = FileAccess.open("res://report.json", FileAccess.WRITE)
    if file == null:
        push_error("Could not write inspection report")
        quit(1)
        return
    file.store_string(JSON.stringify(report, "", true, true))
    file.close()
    scene.free()
    quit(0)
