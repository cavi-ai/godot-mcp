extends Node3D
const REQUIRE_POSE = POSE_ENABLED
var camera: Camera3D
var center: Vector3
var radius := 2.0
var yaw := VIEW_YAW
var pitch := 0.3
var initial_radius: float
var dragging := false
var pose_player: AnimationPlayer
var pose_player_path := ""
var pose_animation := ""
var initial_pose_time := 0.0
var pose_duration := 0.0
var play_button: Button
var pose_slider: HSlider
var pose_time_label: Label
var playback_elapsed := 0.0

func _ready():
 var model = load("res://subject.glb").instantiate()
 add_child(model)
 var pose_metadata = null
 var pose_points: Array[Vector3] = []
 if REQUIRE_POSE:
  if not FileAccess.file_exists("res://pose.json"):
   push_error("Pose configuration missing")
   return
  var selected = JSON.parse_string(FileAccess.get_file_as_string("res://pose.json"))
  if not selected is Dictionary:
   push_error("Invalid pose configuration")
   return
  var selected_player = find_player(model, "", selected.player)
  if selected_player == null or not selected_player.has_animation(selected.animation):
   push_error("Animation player or clip not found")
   return
  var clip = selected_player.get_animation(selected.animation)
  if selected.time_seconds < 0 or selected.time_seconds > clip.length:
   push_error("Pose time outside clip")
   return
  selected_player.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
  selected_player.play(selected.animation)
  selected_player.seek(float(selected.time_seconds), true, true)
  selected_player.pause()
  pose_player = selected_player
  pose_player_path = str(selected.player)
  pose_animation = str(selected.animation)
  initial_pose_time = float(selected.time_seconds)
  pose_duration = clip.length
  var bones = []
  if not collect_pose(model, "", bones, pose_points):
   push_error("Pose skeleton exceeds limit")
   return
  pose_metadata = {"player": selected.player, "animation": str(selected_player.assigned_animation),
   "time_seconds": selected_player.current_animation_position, "duration": clip.length,
   "bone_count": bones.size(), "bones": bones}
 var meshes: Array[MeshInstance3D] = []
 collect_meshes(model, meshes)
 if meshes.is_empty():
  push_error("No mesh instances")
  return
 if not REQUIRE_POSE:
  for mesh in meshes:
   if mesh.skin != null or not mesh.mesh is ArrayMesh:
    continue
   var array_mesh := mesh.mesh as ArrayMesh
   for surface in range(array_mesh.get_surface_count()):
    var material := mesh.get_active_material(surface) as BaseMaterial3D
    var has_color := (array_mesh.surface_get_format(surface) & Mesh.ARRAY_FORMAT_COLOR) != 0
    # Imported drafts may have vertex colors without the material flag.
    if has_color and material != null and not material.vertex_color_use_as_albedo and material.albedo_texture == null:
     var adjusted := material.duplicate() as BaseMaterial3D
     adjusted.vertex_color_use_as_albedo = true
     mesh.set_surface_override_material(surface, adjusted)
 var bounds: AABB
 var first = true
 for mesh in meshes:
  var b = mesh.global_transform * mesh.get_aabb()
  bounds = b if first else bounds.merge(b)
  first = false
 if REQUIRE_POSE and not pose_points.is_empty():
  var pose_bounds = AABB(pose_points[0], Vector3.ZERO)
  for point in pose_points:
   pose_bounds = pose_bounds.expand(point)
  var padding = 0.05
  for mesh in meshes:
   var mesh_bounds = mesh.global_transform * mesh.get_aabb()
   if mesh.skin == null:
    pose_bounds = pose_bounds.merge(mesh_bounds)
   else:
    padding = max(padding, mesh_bounds.size.length() * 0.2)
  bounds = pose_bounds.grow(padding)
 center = bounds.get_center()
 radius = max(bounds.size.length() * 0.8, 0.1)
 initial_radius = radius
 camera = Camera3D.new()
 add_child(camera)
 camera.fov = 45
 camera.current = true
 update_camera()
 var key = DirectionalLight3D.new()
 add_child(key)
 key.rotation_degrees = Vector3(-45, -35, 0)
 key.light_energy = 1.4
 var env = WorldEnvironment.new()
 env.environment = Environment.new()
 env.environment.background_mode = Environment.BG_COLOR
 env.environment.background_color = Color(0.12, 0.13, 0.15)
 env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
 env.environment.ambient_light_color = Color.WHITE
 env.environment.ambient_light_energy = 0.45
 add_child(env)
 var ruler = MeshInstance3D.new()
 var bar = BoxMesh.new()
 bar.size = Vector3(1, 0.025, 0.025)
 ruler.mesh = bar
 add_child(ruler)
 ruler.position = Vector3(bounds.position.x + 0.5, bounds.position.y, bounds.end.z + 0.08)
 var surfaces = 0
 var textures = 0
 var materials = []
 for m in meshes:
  surfaces += m.mesh.get_surface_count()
  for i in range(m.mesh.get_surface_count()):
   var mat = m.get_active_material(i)
   var texture_path = ""
   if mat is BaseMaterial3D and mat.albedo_texture:
    textures += 1
    texture_path = mat.albedo_texture.resource_path
   materials.append({"node": str(m.get_path()), "surface": i,
    "material": mat.resource_name if mat else "", "albedo_texture": texture_path})
 var metadata = {"mesh_nodes": meshes.size(), "material_surfaces": surfaces, "textured_surfaces": textures, "materials": materials,
  "bounds_origin": [bounds.position.x, bounds.position.y, bounds.position.z],
  "bounds_size": [bounds.size.x, bounds.size.y, bounds.size.z], "units": "metres",
  "godot_version": Engine.get_version_info()}
 if REQUIRE_POSE:
  metadata["pose"] = pose_metadata
 var label = Label.new()
 label.text = "Drag to orbit · Scroll to zoom · R to reset | white bar = 1 m"
 var hud = CanvasLayer.new()
 add_child(hud)
 hud.add_child(label)
 if REQUIRE_POSE:
  create_playback_controls(hud)
  update_playback_controls()
  publish_playback()
 await get_tree().process_frame
 await get_tree().process_frame
 if OS.has_feature("web"):
  JavaScriptBridge.eval("window.godotReviewReady = " + JSON.stringify(metadata, "", true, true))

func create_playback_controls(hud: CanvasLayer):
 var controls = HBoxContainer.new()
 hud.add_child(controls)
 controls.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
 controls.offset_top = 28
 controls.offset_bottom = 68
 controls.offset_left = 8
 controls.offset_right = -8
 play_button = Button.new()
 play_button.custom_minimum_size.x = 72
 play_button.pressed.connect(toggle_playback)
 controls.add_child(play_button)
 pose_slider = HSlider.new()
 pose_slider.min_value = 0.0
 pose_slider.max_value = pose_duration
 pose_slider.step = 0.0
 pose_slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
 pose_slider.value_changed.connect(seek_pose)
 controls.add_child(pose_slider)
 pose_time_label = Label.new()
 controls.add_child(pose_time_label)
 var reset_button = Button.new()
 reset_button.text = "Reset pose"
 reset_button.pressed.connect(reset_pose)
 controls.add_child(reset_button)

func toggle_playback():
 if pose_player.is_playing():
  pose_player.pause()
 else:
  pose_player.play(pose_animation)
 update_playback_controls()
 publish_playback()

func seek_pose(seconds: float):
 pose_player.pause()
 pose_player.seek(seconds, true, true)
 update_playback_controls()
 publish_playback()

func reset_pose():
 seek_pose(initial_pose_time)

func update_playback_controls():
 play_button.text = "Pause" if pose_player.is_playing() else "Play"
 pose_slider.set_value_no_signal(pose_player.current_animation_position)
 pose_time_label.text = "%.2f / %.2f s" % [pose_player.current_animation_position, pose_duration]

func publish_playback():
 if OS.has_feature("web"):
  var state = {"player": pose_player_path, "animation": str(pose_player.assigned_animation),
   "time_seconds": pose_player.current_animation_position, "duration": pose_duration,
   "is_playing": pose_player.is_playing()}
  JavaScriptBridge.eval("window.godotReviewPlayback = " + JSON.stringify(state, "", true, true))
 playback_elapsed = 0.0

func _process(delta: float):
 if pose_player != null and pose_player.is_playing():
  pose_player.advance(delta)
  update_playback_controls()
  playback_elapsed += delta
  if playback_elapsed >= 0.2 or not pose_player.is_playing():
   publish_playback()

func find_player(node: Node, parent_path: String, wanted: String) -> AnimationPlayer:
 var path = parent_path + "/" + str(node.name)
 if node is AnimationPlayer and path == wanted:
  return node
 for child in node.get_children():
  var found = find_player(child, path, wanted)
  if found != null:
   return found
 return null

func vector(v: Vector3) -> Array:
 return [v.x, v.y, v.z]

func collect_pose(node: Node, parent_path: String, bones: Array, points: Array[Vector3]) -> bool:
 var path = parent_path + "/" + str(node.name)
 if node is Skeleton3D:
  if bones.size() + node.get_bone_count() > 256:
   return false
  node.force_update_all_bone_transforms()
  for i in node.get_bone_count():
   var t = node.global_transform * node.get_bone_global_pose(i)
   points.append(t.origin)
   bones.append({"skeleton": path, "index": i, "name": node.get_bone_name(i),
    "origin": vector(t.origin), "basis": [vector(t.basis.x), vector(t.basis.y), vector(t.basis.z)]})
 for child in node.get_children():
  if not collect_pose(child, path, bones, points):
   return false
 return true

func collect_meshes(node: Node, meshes: Array[MeshInstance3D]):
 if node is MeshInstance3D and node.mesh:
  meshes.append(node)
 for child in node.get_children():
  collect_meshes(child, meshes)

func update_camera():
 camera.position = center + Vector3(sin(yaw)*cos(pitch), sin(pitch), cos(yaw)*cos(pitch)) * radius * 1.75
 camera.look_at(center)

func _unhandled_input(event):
 if event is InputEventMouseButton:
  if event.button_index == MOUSE_BUTTON_LEFT:
   dragging = event.pressed
  if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
   radius = max(radius * 0.9, 0.05)
   update_camera()
  if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
   radius *= 1.1
   update_camera()
 if event is InputEventMouseMotion and dragging and event.button_mask & MOUSE_BUTTON_MASK_LEFT:
  yaw -= event.relative.x * 0.01
  pitch = clamp(pitch + event.relative.y * 0.01, -1.3, 1.3)
  update_camera()

 if event is InputEventKey and event.pressed and event.keycode == KEY_R:
  yaw = VIEW_YAW
  pitch = 0.3
  radius = initial_radius
  update_camera()
