extends SceneTree

const MAX_NODES = 256
const MAX_SURFACES = 256
const MAX_BONES = 256
const MAX_CLIPS = 128
# Twice the area of a triangle under 1e-12 m², squared.
const DEGENERATE = 4e-24
const TEXTURE_SLOTS = [["base_color", BaseMaterial3D.TEXTURE_ALBEDO], ["normal", BaseMaterial3D.TEXTURE_NORMAL], ["orm", BaseMaterial3D.TEXTURE_ORM], ["metallic", BaseMaterial3D.TEXTURE_METALLIC], ["roughness", BaseMaterial3D.TEXTURE_ROUGHNESS], ["occlusion", BaseMaterial3D.TEXTURE_AMBIENT_OCCLUSION], ["emissive", BaseMaterial3D.TEXTURE_EMISSION]]
var nodes: Array = []
var surfaces: Array = []
var bones: Array = []
var clips: Array = []
var totals = {"nodes": 0, "surfaces": 0, "bones": 0, "clips": 0, "unsupported_primitives": 0}
var qa = {"triangles": 0, "vertices": 0, "degenerate_triangles": 0, "uv0_outside_unit_surfaces": 0, "materials": 0, "textures": 0, "texture_rgba8_bytes": 0, "max_texture_size": 0, "npot_textures": 0, "max_bone_influences": 0, "max_blend_shapes": 0, "mirrored_meshes": 0, "non_uniform_scale_meshes": 0, "animation_keys": 0, "bounds": null}
var seen = {}
var scene_bounds = null

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
        for slot in TEXTURE_SLOTS:
            var texture = material.get_texture(slot[1])
            if texture != null:
                result.textures[slot[0]] = {"name": texture.resource_name, "width": texture.get_width(), "height": texture.get_height()}
    return result

func first_use(resource: Resource) -> bool:
    if resource == null or seen.has(resource.get_instance_id()):
        return false
    seen[resource.get_instance_id()] = true
    return true

func count_texture(texture: Texture2D) -> void:
    var width = texture.get_width()
    var height = texture.get_height()
    qa.textures += 1
    qa.max_texture_size = max(qa.max_texture_size, width, height)
    if nearest_po2(width) != width or nearest_po2(height) != height:
        qa.npot_textures += 1
    while true:
        qa.texture_rgba8_bytes += width * height * 4
        if width == 1 and height == 1:
            break
        width = max(width >> 1, 1)
        height = max(height >> 1, 1)

func count_material(material: Material) -> void:
    if not first_use(material):
        return
    qa.materials += 1
    if material is BaseMaterial3D:
        for slot in TEXTURE_SLOTS:
            var texture = material.get_texture(slot[1])
            if first_use(texture):
                count_texture(texture)

func surface_quality(mesh: Mesh, index: int, primitive: int, format: int) -> Dictionary:
    var arrays = mesh.surface_get_arrays(index)
    var result = {"degenerate_triangles": null, "uv0_outside_unit": false}
    if format & Mesh.ARRAY_FORMAT_TEX_UV:
        for uv in arrays[Mesh.ARRAY_TEX_UV]:
            if uv.x < 0.0 or uv.x > 1.0 or uv.y < 0.0 or uv.y > 1.0:
                result.uv0_outside_unit = true
                break
    if primitive == Mesh.PRIMITIVE_TRIANGLES:
        var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
        var order = arrays[Mesh.ARRAY_INDEX] if format & Mesh.ARRAY_FORMAT_INDEX else PackedInt32Array(range(points.size()))
        var degenerate = 0
        for i in range(0, order.size() - 2, 3):
            var a = points[order[i]]
            if (points[order[i + 1]] - a).cross(points[order[i + 2]] - a).length_squared() < DEGENERATE:
                degenerate += 1
        result.degenerate_triangles = degenerate
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
        scene_bounds = world * mesh_bounds if scene_bounds == null else scene_bounds.merge(world * mesh_bounds)
        if world.basis.determinant() < 0.0:
            qa.mirrored_meshes += 1
        var scale = [world.basis.x.length(), world.basis.y.length(), world.basis.z.length()]
        if scale.max() - scale.min() > 1e-4 * scale.max():
            qa.non_uniform_scale_meshes += 1
        if node.mesh is ArrayMesh:
            qa.max_blend_shapes = max(qa.max_blend_shapes, node.mesh.get_blend_shape_count())
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
            var triangles = int((indices if indices > 0 else vertices) / 3) if primitive == Mesh.PRIMITIVE_TRIANGLES else null
            var quality = surface_quality(mesh, index, primitive, format)
            if primitive != Mesh.PRIMITIVE_TRIANGLES:
                totals.unsupported_primitives += 1
            else:
                qa.triangles += triangles
                qa.degenerate_triangles += quality.degenerate_triangles
            qa.vertices += vertices
            if quality.uv0_outside_unit:
                qa.uv0_outside_unit_surfaces += 1
            if format & Mesh.ARRAY_FORMAT_BONES:
                qa.max_bone_influences = max(qa.max_bone_influences, 8 if format & Mesh.ARRAY_FLAG_USE_8_BONE_WEIGHTS else 4)
            count_material(node.get_active_material(index))
            if surfaces.size() < MAX_SURFACES:
                surfaces.append({"node": path, "surface": index, "primitive": primitive, "vertices": vertices, "triangles": triangles, "degenerate_triangles": quality.degenerate_triangles, "uv0": format & Mesh.ARRAY_FORMAT_TEX_UV != 0, "uv0_outside_unit": quality.uv0_outside_unit, "uv1": format & Mesh.ARRAY_FORMAT_TEX_UV2 != 0, "material": material_info(node.get_active_material(index))})
    if node is Skeleton3D:
        for index in node.get_bone_count():
            totals.bones += 1
            if bones.size() < MAX_BONES:
                bones.append({"skeleton": path, "index": index, "name": node.get_bone_name(index), "parent": node.get_bone_parent(index), "rest_transform": transform(node.get_bone_rest(index))})
    if node is AnimationPlayer:
        for name in node.get_animation_list():
            totals.clips += 1
            var animation = node.get_animation(name)
            var keys = 0
            for track in animation.get_track_count():
                keys += animation.track_get_key_count(track)
            qa.animation_keys += keys
            if clips.size() < MAX_CLIPS:
                clips.append({"player": path, "name": str(name), "duration": animation.length, "tracks": animation.get_track_count(), "keys": keys})
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
    if scene_bounds != null:
        qa.bounds = bounds(scene_bounds)
    var report = {"status": "ok", "godot_version": Engine.get_version_info(), "units": "metres", "bounds_semantics": "imported rest pose", "totals": totals, "qa": qa, "truncated": {"nodes": totals.nodes > nodes.size(), "surfaces": totals.surfaces > surfaces.size(), "bones": totals.bones > bones.size(), "clips": totals.clips > clips.size()}, "nodes": nodes, "surfaces": surfaces, "bones": bones, "clips": clips}
    var file = FileAccess.open("res://report.json", FileAccess.WRITE)
    if file == null:
        push_error("Could not write inspection report")
        quit(1)
        return
    file.store_string(JSON.stringify(report, "", true, true))
    file.close()
    scene.free()
    quit(0)
