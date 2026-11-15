extends SceneTree

var objects: Array = []
var skeletons: Array = []
var bones: Array = []
var player_node: AnimationPlayer = null
var chosen_player: String = ""
var total_nodes: int = 0
var overflow: bool = false

func vec(v: Vector3) -> Array:
    return [v.x, v.y, v.z]

func matrix(t: Transform3D) -> Dictionary:
    return {"origin": vec(t.origin), "basis": [vec(t.basis.x), vec(t.basis.y), vec(t.basis.z)]}

func collect(node: Node, parent_path: String) -> void:
    total_nodes += 1
    if total_nodes > 256:
        overflow = true
        return
    var path = parent_path + "/" + str(node.name)
    if node is Node3D:
        objects.append({"node": node, "path": path, "baseline": node.transform})
    if node is Skeleton3D:
        skeletons.append(node)
        if bones.size() + node.get_bone_count() > 256:
            overflow = true
            return
        for i in node.get_bone_count():
            bones.append({"node": node, "path": path, "index": i, "name": node.get_bone_name(i), "parent": node.get_bone_parent(i), "baseline": node.get_bone_pose(i)})
    if node is AnimationPlayer and path == chosen_player:
        player_node = node
    for child in node.get_children():
        if overflow: return
        collect(child, path)

func fail(message: String) -> void:
    push_error(message)
    quit(1)

func _initialize() -> void:
    call_deferred("run")

func run() -> void:
    var config = JSON.parse_string(FileAccess.get_file_as_string("res://config.json"))
    chosen_player = config.player
    var document = GLTFDocument.new()
    var state = GLTFState.new()
    if document.append_from_file("res://subject.glb", state) != OK:
        fail("GLB import failed")
        return
    var scene = document.generate_scene(state)
    if scene == null:
        fail("GLB scene generation failed")
        return
    root.add_child(scene)
    collect(scene, "")
    if overflow:
        fail("Motion scene exceeds node or bone limit")
        return
    if player_node == null or not player_node.has_animation(config.animation):
        fail("Player or animation not found")
        return
    var animation = player_node.get_animation(config.animation)
    if animation.length <= 0.0:
        fail("Animation duration must be positive")
        return
    player_node.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
    var node_inventory = []
    var bone_inventory = []
    for item in objects:
        node_inventory.append({"path": item.path, "type": item.node.get_class()})
    for item in bones:
        bone_inventory.append({"skeleton": item.path, "index": item.index, "name": item.name, "parent": item.parent})
    var samples = []
    for time in config.normalized_times:
        player_node.stop()
        for item in objects:
            item.node.transform = item.baseline
        for item in bones:
            item.node.set_bone_pose(item.index, item.baseline)
        player_node.play(config.animation)
        var seconds = float(time) * animation.length
        player_node.seek(seconds, true, true)
        for skeleton in skeletons:
            skeleton.force_update_all_bone_transforms()
        var node_values = []
        var bone_values = []
        for item in objects:
            node_values.append({"local_transform": matrix(item.node.transform), "world_transform": matrix(item.node.global_transform)})
        for item in bones:
            var pose = item.node.get_bone_global_pose(item.index)
            bone_values.append({"skeleton_transform": matrix(pose), "world_transform": matrix(item.node.global_transform * pose)})
        samples.append({"normalized_time": time, "seconds": seconds, "nodes": node_values, "bones": bone_values})
    var report = {"status": "ok", "godot_version": Engine.get_version_info(), "player": chosen_player, "animation": config.animation, "duration": animation.length, "loop_mode": animation.loop_mode, "units": "Godot scene units", "bone_space": "skeleton relative and Godot world", "nodes": node_inventory, "bones": bone_inventory, "samples": samples}
    var file = FileAccess.open("res://report.json", FileAccess.WRITE)
    if file == null:
        fail("Could not write motion report")
        return
    file.store_string(JSON.stringify(report))
    file.close()
    scene.free()
    quit(0)
