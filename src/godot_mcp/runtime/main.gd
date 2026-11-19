extends Node3D
const REQUIRE_POSE = POSE_ENABLED
const ALL_VIEWS = ALL_VIEWS_ENABLED
const REQUIRE_FRAMING = FRAMING_ENABLED
const GEOMETRY_PREVIEW = GEOMETRY_PREVIEW_ENABLED
const VIEW = VIEW_NAME
const VIEW_YAW = {"front": 0.0, "three-quarter": PI / 4, "side": PI / 2}
const PITCH = 0.3
const FOV = 45.0
var camera: Camera3D
var center: Vector3
var distance := 1.0
var yaw: float = VIEW_YAW[VIEW]
var pitch := PITCH
var initial_distance: float
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
var view_callback

func _ready():
 var model = load("res://subject.glb").instantiate()
 add_child(model)
 var pose_metadata = null
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
  if not collect_pose(model, "", bones):
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
 var geometry_preview_surfaces = 0
 if GEOMETRY_PREVIEW and not REQUIRE_POSE:
  var shader = Shader.new()
  shader.code = """shader_type spatial;
render_mode specular_disabled;
void fragment() {
 vec3 face = cross(dFdx(VERTEX), dFdy(VERTEX));
 float magnitude = length(face);
 if (magnitude > 0.0) {
  face /= magnitude;
  NORMAL = dot(face, VIEW) < 0.0 ? -face : face;
 } else {
  NORMAL = VIEW;
 }
 ALBEDO = vec3(0.55);
 ROUGHNESS = 1.0;
}"""
  var preview = ShaderMaterial.new()
  preview.shader = shader
  for mesh in meshes:
   if mesh.skin != null or not mesh.mesh is ArrayMesh:
    continue
   var array_mesh := mesh.mesh as ArrayMesh
   for surface in range(array_mesh.get_surface_count()):
    if array_mesh.surface_get_primitive_type(surface) == Mesh.PRIMITIVE_TRIANGLES:
     mesh.set_surface_override_material(surface, preview)
     geometry_preview_surfaces += 1
 if not REQUIRE_POSE and not GEOMETRY_PREVIEW:
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
  var local = mesh.get_aabb()
  if REQUIRE_POSE and mesh.skin != null:
   local = posed_aabb(mesh)
  var b = mesh.global_transform * local
  bounds = b if first else bounds.merge(b)
  first = false
 center = bounds.get_center()
 if bounds.size.length() == 0.0:
  push_error("Subject has no extent")
  return
 distance = fit_distance(bounds.size.length() / 2)
 if REQUIRE_FRAMING:
  var frame = JSON.parse_string(FileAccess.get_file_as_string("res://framing.json"))
  if not frame is Dictionary or not frame.has("center") or not frame.has("distance"):
   push_error("Invalid camera framing")
   return
  center = Vector3(frame.center[0], frame.center[1], frame.center[2])
  distance = float(frame.distance)
 initial_distance = distance
 camera = Camera3D.new()
 add_child(camera)
 camera.fov = FOV
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
 # The bar is the largest power of ten that fits along the bottom front edge of the bounds.
 var axis = Vector3.AXIS_X if bounds.size.x >= bounds.size.z else Vector3.AXIS_Z
 if bounds.size[axis] == 0.0:
  axis = Vector3.AXIS_Y
 var bar_length = pow(10.0, floor(log(bounds.size[axis]) / log(10.0) + 1e-9))
 var bar_start = Vector3(bounds.position.x, bounds.position.y, bounds.end.z)
 if axis == Vector3.AXIS_Z:
  bar_start = Vector3(bounds.end.x, bounds.position.y, bounds.position.z)
 var bar_end = bar_start
 bar_end[axis] += bar_length
 var bar = BoxMesh.new()
 bar.size = Vector3.ONE * bar_length * 0.025
 bar.size[axis] = bar_length
 var ruler = MeshInstance3D.new()
 ruler.mesh = bar
 add_child(ruler)
 ruler.position = (bar_start + bar_end) / 2
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
 else:
  metadata["camera_frame"] = {"center": vector(center), "distance": camera.global_position.distance_to(center)}
  metadata["geometry_preview_surfaces"] = geometry_preview_surfaces
 metadata["camera_position"] = vector(camera.global_position)
 metadata["camera_clip"] = [camera.near, camera.far]
 metadata["scale_bar"] = {"length": bar_length, "start": vector(bar_start), "end": vector(bar_end)}
 var label = Label.new()
 label.text = "Drag to orbit · Scroll to zoom · R to reset | white bar = " + length_text(bar_length)
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
  if ALL_VIEWS:
   view_callback = JavaScriptBridge.create_callback(select_capture_view)
   JavaScriptBridge.get_interface("window").godotReviewSetView = view_callback
  JavaScriptBridge.eval("window.godotReviewReady = " + JSON.stringify(metadata, "", true, true))
 else:
  print("godot-review-ready ", JSON.stringify(metadata, "", true, true))

func fit_distance(radius: float) -> float:
 # The bounding sphere touches the narrower of the vertical and horizontal fields of view.
 var size = get_viewport().get_visible_rect().size
 var half_vertical = deg_to_rad(FOV) / 2
 var half_horizontal = atan(tan(half_vertical) * size.x / size.y)
 return radius / sin(min(half_vertical, half_horizontal))

func length_text(length: float) -> String:
 if length >= 1.0:
  return "%d m" % roundi(length)
 if length >= 0.01:
  return "%d cm" % roundi(length * 100)
 if length >= 0.001:
  return "%d mm" % roundi(length * 1000)
 return "%s m" % length

func select_capture_view(args: Array):
 if args.size() != 1 or not args[0] is String or not VIEW_YAW.has(args[0]):
  return
 yaw = VIEW_YAW[args[0]]
 pitch = PITCH
 distance = initial_distance
 update_camera()
 await RenderingServer.frame_post_draw
 var state = {"view": args[0], "camera_position": vector(camera.global_position)}
 JavaScriptBridge.eval("window.godotReviewView = " + JSON.stringify(state, "", true, true))

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

func collect_pose(node: Node, parent_path: String, bones: Array) -> bool:
 var path = parent_path + "/" + str(node.name)
 if node is Skeleton3D:
  if bones.size() + node.get_bone_count() > 256:
   return false
  node.force_update_all_bone_transforms()
  for i in node.get_bone_count():
   var t = node.global_transform * node.get_bone_global_pose(i)
   bones.append({"skeleton": path, "index": i, "name": node.get_bone_name(i),
    "origin": vector(t.origin), "basis": [vector(t.basis.x), vector(t.basis.y), vector(t.basis.z)]})
 for child in node.get_children():
  if not collect_pose(child, path, bones):
   return false
 return true

func posed_aabb(mesh: MeshInstance3D) -> AABB:
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

func collect_meshes(node: Node, meshes: Array[MeshInstance3D]):
 if node is MeshInstance3D and node.mesh:
  meshes.append(node)
 for child in node.get_children():
  collect_meshes(child, meshes)

func update_camera():
 camera.position = center + Vector3(sin(yaw)*cos(pitch), sin(pitch), cos(yaw)*cos(pitch)) * distance
 camera.look_at(center)
 camera.near = distance / 100.0
 camera.far = distance * 100.0

func _unhandled_input(event):
 if event is InputEventMouseButton:
  if event.button_index == MOUSE_BUTTON_LEFT:
   dragging = event.pressed
  if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
   distance = max(distance * 0.9, initial_distance * 0.05)
   update_camera()
  if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
   distance *= 1.1
   update_camera()
 if event is InputEventMouseMotion and dragging and event.button_mask & MOUSE_BUTTON_MASK_LEFT:
  yaw -= event.relative.x * 0.01
  pitch = clamp(pitch + event.relative.y * 0.01, -1.3, 1.3)
  update_camera()

 if event is InputEventKey and event.pressed and event.keycode == KEY_R:
  yaw = VIEW_YAW[VIEW]
  pitch = PITCH
  distance = initial_distance
  update_camera()
