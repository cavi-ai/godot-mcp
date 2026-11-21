extends SceneTree

const FRAME_RATE = 30
const MAX_FRAMES = 1801
const Pose = preload("res://pose.gd")
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
        objects.append({"node": node, "path": path, "parent": parent_path, "baseline": node.transform})
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

func pose_at(animation: String, seconds: float) -> void:
    Pose.apply_pose(player_node, animation, seconds, objects, bones)
    for skeleton in skeletons:
        skeleton.force_update_all_bone_transforms()

# Local transforms and world positions of every node, then every bone.
func frame_state() -> Array:
    var local = []
    var world = []
    for item in objects:
        local.append(item.node.transform)
        world.append(item.node.global_position)
    for item in bones:
        local.append(item.node.get_bone_pose(item.index))
        world.append((item.node.global_transform * item.node.get_bone_global_pose(item.index)).origin)
    return [local, world]

func turn(a: Transform3D, b: Transform3D) -> float:
    return a.basis.get_rotation_quaternion().angle_to(b.basis.get_rotation_quaternion())

func motion_qa(name: String, clip: Animation, scene_path: String) -> Dictionary:
    var targets = []
    for item in objects:
        targets.append({"path": item.path, "bone": null})
    for item in bones:
        targets.append({"path": item.path, "bone": item.name})
    var loop_mode = clip.loop_mode
    clip.loop_mode = Animation.LOOP_NONE
    var frames = mini(ceili(clip.length * FRAME_RATE) + 1, MAX_FRAMES)
    var step = clip.length / (frames - 1)
    var linear = {"value": 0.0, "path": null, "bone": null}
    var angular = {"value": 0.0, "path": null, "bone": null}
    var first = []
    var previous = []
    var state = []
    for frame in frames:
        pose_at(name, min(frame * step, clip.length))
        state = frame_state()
        if frame == 0:
            first = state
        else:
            for i in targets.size():
                var speed = (state[1][i] - previous[1][i]).length() / step
                if speed > linear.value:
                    linear = {"value": speed, "path": targets[i].path, "bone": targets[i].bone}
                var spin = turn(previous[0][i], state[0][i]) / step
                if spin > angular.value:
                    angular = {"value": spin, "path": targets[i].path, "bone": targets[i].bone}
        previous = state
    clip.loop_mode = loop_mode
    var seam = {"position": 0.0, "rotation": 0.0}
    var root_motion = []
    for i in targets.size():
        seam.position = max(seam.position, (state[0][i].origin - first[0][i].origin).length())
        seam.rotation = max(seam.rotation, turn(first[0][i], state[0][i]))
        var is_root = false
        if i < objects.size():
            is_root = objects[i].parent == scene_path
        else:
            is_root = bones[i - objects.size()].parent == -1
        if is_root:
            root_motion.append({"path": targets[i].path, "bone": targets[i].bone, "displacement": vec(state[1][i] - first[1][i])})
    return {"frame_rate": FRAME_RATE, "frames": frames, "seconds_per_frame": step, "loop_seam": seam, "max_linear_speed": linear, "max_angular_speed": angular, "root_motion": root_motion}

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
        var seconds = float(time) * animation.length
        pose_at(config.animation, seconds)
        var node_values = []
        var bone_values = []
        for item in objects:
            node_values.append({"local_transform": matrix(item.node.transform), "world_transform": matrix(item.node.global_transform)})
        for item in bones:
            var pose = item.node.get_bone_global_pose(item.index)
            bone_values.append({"skeleton_transform": matrix(pose), "world_transform": matrix(item.node.global_transform * pose)})
        samples.append({"normalized_time": time, "seconds": seconds, "nodes": node_values, "bones": bone_values})
    var qa = motion_qa(config.animation, animation, "/" + str(scene.name))
    var report = {"status": "ok", "godot_version": Engine.get_version_info(), "player": chosen_player, "animation": config.animation, "duration": animation.length, "loop_mode": animation.loop_mode, "units": "metres", "bone_space": "skeleton relative and Godot world", "nodes": node_inventory, "bones": bone_inventory, "samples": samples, "qa": qa}
    var file = FileAccess.open("res://report.json", FileAccess.WRITE)
    if file == null:
        fail("Could not write motion report")
        return
    file.store_string(JSON.stringify(report, "", true, true))
    file.close()
    scene.free()
    quit(0)
