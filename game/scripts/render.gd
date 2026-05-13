extends Node3D

# Headless one-shot Godot renderer for MysteryArena (3D variant of M3).
#
# Reads JSON commands from stdin (one per line) and writes line-delimited
# responses to stdout. The Python side (mystery_world/godot_render.py) spawns
# this scene as a subprocess and talks to it per env.step() call. The result
# is a 3D first-person-style image of the agent's current room with
# evidence-aging materials applied -- the visual channel of the
# visual-temporal benchmark.
#
# Protocol
# --------
# stdin (line-delimited JSON):
#   {"cmd": "render",
#    "room": {<room dict from serialize_room>},
#    "evidence_overlays": [
#        {"id": "ev_123", "x": 4, "y": 7, "evidence_family": "bloodstain",
#         "visual_state": "BRIGHT"|"DULL"|"FADED"},
#        ...
#    ],
#    "width": 720, "height": 720}
#   {"cmd": "shutdown"}
#
# stdout (one ASCII line per response, never binary):
#   READY                              -- printed once after _ready
#   RENDER <base64-png>                -- response to a render command
#   ERR <message>                      -- if the command was malformed
#
# All other diagnostics (Godot's own logs) go to stderr so the stdout
# channel stays a clean text protocol.

const TILE_M: float = 1.0   # 1 tile = 1 metre. Matches main.gd's TILE_M.

const AGING_COLOR_BY_STATE: Dictionary = {
	"BRIGHT": Color(0.84, 0.15, 0.15),  # vivid wet red
	"DULL":   Color(0.55, 0.18, 0.12),  # dark dried red
	"FADED":  Color(0.32, 0.22, 0.18),  # brown crust
}

# Per-family rendering of the aging trace. "disc" = flat coloured disc on the
# floor (bloodstains, footprints). "cylinder" = vertical mesh that shrinks
# with age (candles, melted-things). Default is "disc".
const FAMILY_AGING_MESH: Dictionary = {
	"bloodstain": "disc",
	"blood":      "disc",
	"footprint":  "disc",
	"trace":      "disc",
	"candle":     "cylinder",
}

@onready var _viewport: SubViewport = $RenderViewport
@onready var _camera:   Camera3D    = $RenderViewport/Camera
@onready var _builder:  Node3D      = $RenderViewport/RoomMount
@onready var _overlays: Node3D      = $RenderViewport/EvidenceOverlays

var _stdin_thread: Thread = null
var _command_queue: Array = []
var _command_mutex: Mutex = Mutex.new()
var _shutdown_flag: bool = false


func _ready() -> void:
	print("READY")
	# Background thread reads stdin so _process can pull commands without
	# blocking the main render thread on OS.read_string_from_stdin.
	_stdin_thread = Thread.new()
	_stdin_thread.start(_stdin_loop)


func _stdin_loop() -> void:
	while not _shutdown_flag:
		var line: String = OS.read_string_from_stdin().strip_edges()
		if line == "":
			# EOF or empty line: brief sleep, retry. Empty stdin is harmless.
			OS.delay_msec(5)
			continue
		_command_mutex.lock()
		_command_queue.push_back(line)
		_command_mutex.unlock()


func _process(_delta: float) -> void:
	var pending: Array = []
	_command_mutex.lock()
	if _command_queue.size() > 0:
		pending = _command_queue
		_command_queue = []
	_command_mutex.unlock()
	for raw in pending:
		await _handle_command(raw)


func _handle_command(raw: String) -> void:
	var parsed = JSON.parse_string(raw)
	if parsed == null or typeof(parsed) != TYPE_DICTIONARY:
		push_error("[render] bad JSON: " + raw.substr(0, 200))
		print("ERR bad_json")
		return
	var cmd: String = String(parsed.get("cmd", ""))
	if cmd == "render":
		await _do_render(parsed)
	elif cmd == "shutdown":
		_shutdown_flag = true
		get_tree().quit()
	else:
		print("ERR unknown_command:", cmd)


func _do_render(cmd: Dictionary) -> void:
	var room: Dictionary = cmd.get("room", {})
	var overlays: Array = cmd.get("evidence_overlays", [])
	var width: int = int(cmd.get("width", 720))
	var height: int = int(cmd.get("height", 720))

	_viewport.size = Vector2i(width, height)

	# Tear down the previous frame's scene contents.
	for child in _builder.get_children():
		_builder.remove_child(child)
		child.queue_free()
	for child in _overlays.get_children():
		_overlays.remove_child(child)
		child.queue_free()

	if not room.is_empty():
		_builder.build_from(room, TILE_M)
		var room_w: float = float(room.get("width", 1))
		var room_h: float = float(room.get("height", 1))
		_setup_camera(room_w * TILE_M, room_h * TILE_M)

	_spawn_overlays(overlays)

	# Two frames give the SubViewport time to flush the new scene and update
	# its texture; one is sometimes enough but two is safer across drivers.
	await get_tree().process_frame
	await get_tree().process_frame

	var img: Image = _viewport.get_texture().get_image()
	if img == null:
		print("ERR null_image")
		return
	var png_bytes: PackedByteArray = img.save_png_to_buffer()
	var b64: String = Marshalls.raw_to_base64(png_bytes)
	print("RENDER ", b64)


func _setup_camera(world_w: float, world_h: float) -> void:
	# Fixed third-person camera framing the entire room from above-and-back at
	# ~45deg. Wider rooms get pulled further out. The look-at target sits at
	# y=0.5 (chair height) so the scene's verticals are well composed.
	var centre: Vector3 = Vector3(world_w / 2.0, 0.0, world_h / 2.0)
	var diag: float = max(world_w, world_h)
	_camera.position = centre + Vector3(0.0, diag * 1.0, diag * 0.85)
	_camera.look_at(centre + Vector3(0.0, 0.5, 0.0), Vector3.UP)


func _spawn_overlays(overlays: Array) -> void:
	for ov in overlays:
		var family: String = String(ov.get("evidence_family", "default"))
		var state: String = String(ov.get("visual_state", "BRIGHT"))
		var color: Color = AGING_COLOR_BY_STATE.get(state, Color(0.5, 0.5, 0.5))
		var x: float = float(ov.get("x", 0)) + 0.5
		var z: float = float(ov.get("y", 0)) + 0.5  # tile y maps to world z
		var kind: String = FAMILY_AGING_MESH.get(family, "disc")

		var node: Node3D = _make_overlay_mesh(kind, color, state)
		node.position = Vector3(x * TILE_M, 0.02, z * TILE_M)
		_overlays.add_child(node)


func _make_overlay_mesh(kind: String, color: Color, state: String) -> Node3D:
	var mat: StandardMaterial3D = StandardMaterial3D.new()
	mat.albedo_color = color
	# Wet/bright stays glossy; dried/faded gets rougher.
	mat.roughness = 0.35 if state == "BRIGHT" else (0.7 if state == "DULL" else 0.9)
	mat.metallic = 0.0

	var mi: MeshInstance3D = MeshInstance3D.new()
	if kind == "cylinder":
		var cyl: CylinderMesh = CylinderMesh.new()
		cyl.top_radius = 0.08
		cyl.bottom_radius = 0.08
		var h_by_state: Dictionary = {"BRIGHT": 0.6, "DULL": 0.4, "FADED": 0.2}
		cyl.height = float(h_by_state.get(state, 0.4))
		mi.mesh = cyl
		mi.position = Vector3(0.0, cyl.height / 2.0, 0.0)
		mi.material_override = mat
	else:
		# "disc" -- a flattened cylinder pretending to be a floor decal.
		var disc: CylinderMesh = CylinderMesh.new()
		disc.top_radius = 0.28
		disc.bottom_radius = 0.28
		disc.height = 0.02
		mi.mesh = disc
		mi.position = Vector3(0.0, 0.01, 0.0)
		mi.material_override = mat
	return mi
