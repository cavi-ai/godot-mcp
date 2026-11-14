extends Node3D
var camera: Camera3D
var center: Vector3
var radius := 2.0
var yaw := VIEW_YAW
var pitch := 0.3
var initial_radius: float
var dragging := false

func _ready():
 var model = load("res://subject.glb").instantiate()
 add_child(model)
 var meshes: Array[MeshInstance3D] = []
 collect_meshes(model, meshes)
 if meshes.is_empty():
  push_error("No mesh instances")
  return
 var bounds: AABB
 var first = true
 for mesh in meshes:
  var b = mesh.global_transform * mesh.get_aabb()
  bounds = b if first else bounds.merge(b)
  first = false
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
  "bounds_size": [bounds.size.x, bounds.size.y, bounds.size.z], "units": "metres"}
 var label = Label.new()
 label.text = "Drag to orbit · Scroll to zoom · R to reset | white bar = 1 m"
 var hud = CanvasLayer.new()
 add_child(hud)
 hud.add_child(label)
 await get_tree().process_frame
 await get_tree().process_frame
 if OS.has_feature("web"):
  JavaScriptBridge.eval("window.godotReviewReady = " + JSON.stringify(metadata))

func collect_meshes(node: Node, meshes: Array[MeshInstance3D]):
 if node is MeshInstance3D and node.mesh:
  meshes.append(node)
 for child in node.get_children():
  collect_meshes(child, meshes)

func update_camera():
 camera.position = center + Vector3(sin(yaw)*cos(pitch), sin(pitch), cos(yaw)*cos(pitch)) * radius * 1.75
 camera.look_at(center)

func _input(event):
 if event is InputEventMouseButton:
  if event.button_index == MOUSE_BUTTON_LEFT:
   dragging = event.pressed
  if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
   radius = max(radius * 0.9, 0.05)
   update_camera()
  if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
   radius *= 1.1
   update_camera()
 if event is InputEventMouseMotion and dragging:
  yaw -= event.relative.x * 0.01
  pitch = clamp(pitch + event.relative.y * 0.01, -1.3, 1.3)
  update_camera()

 if event is InputEventKey and event.pressed and event.keycode == KEY_R:
  yaw = VIEW_YAW
  pitch = 0.3
  radius = initial_radius
  update_camera()
