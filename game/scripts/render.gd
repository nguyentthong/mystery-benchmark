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
@onready var _world_env: WorldEnvironment = $RenderViewport/WorldEnv
@onready var _camera:   Camera3D    = $RenderViewport/Camera
@onready var _builder:  Node3D      = $RenderViewport/RoomMount
@onready var _overlays: Node3D      = $RenderViewport/EvidenceOverlays

var _stdin_thread: Thread = null
var _command_queue: Array = []
var _command_mutex: Mutex = Mutex.new()
var _shutdown_flag: bool = false
var _stdin_buffer: String = ""


func _ready() -> void:
	# Belt and braces against the "SubViewport renders black" quirk:
	# (1) make the camera explicitly current within the SubViewport's own
	#     World3D, (2) attach the WorldEnv's Environment resource to the
	#     Camera as a fallback so the background colour applies even if
	#     own_world_3d swallows the WorldEnvironment node, (3) keep
	#     update_mode = ALWAYS so the SubViewport always renders, and
	#     additionally request UPDATE_ONCE before each capture below.
	_camera.current = true
	if _world_env and _world_env.environment:
		_camera.environment = _world_env.environment

	print("READY")
	# Background thread reads stdin so _process can pull commands without
	# blocking the main render thread on OS.read_string_from_stdin.
	_stdin_thread = Thread.new()
	_stdin_thread.start(_stdin_loop)


func _stdin_loop() -> void:
	# Godot 4 read_string_from_stdin reads up to a fixed buffer size, NOT a
	# whole line, so we accumulate chunks and split on newlines ourselves.
	# Otherwise JSON payloads larger than the buffer get truncated and the
	# parser sees a fragment.
	while not _shutdown_flag:
		var chunk: String = OS.read_string_from_stdin()
		if chunk.length() == 0:
			OS.delay_msec(5)
			continue
		_stdin_buffer += chunk
		while true:
			var nl_pos: int = _stdin_buffer.find("\n")
			if nl_pos == -1:
				break
			var line: String = _stdin_buffer.substr(0, nl_pos).strip_edges()
			_stdin_buffer = _stdin_buffer.substr(nl_pos + 1)
			if line.length() == 0:
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
		# Log the offending payload's prefix + length to stderr so we can
		# tell at a glance whether it's truncation vs a malformed message.
		push_error("[render] bad JSON (len=" + str(raw.length())
			+ ", head=" + raw.substr(0, 120) + ")")
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

	# Make absolutely sure the SubViewport renders a fresh frame and that we
	# wait for the GPU to actually finish drawing before sampling its
	# texture. UPDATE_ALWAYS alone is unreliable when the SubViewport is
	# offscreen; UPDATE_ONCE + frame_post_draw is the recommended sync.
	_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	await RenderingServer.frame_post_draw
	# One more frame for good measure -- some drivers seem to delay the
	# very first draw of a freshly-mutated SubViewport.
	_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE
	await RenderingServer.frame_post_draw

	var tex := _viewport.get_texture()
	if tex == null:
		print("ERR null_texture")
		return
	var img: Image = tex.get_image()
	if img == null:
		print("ERR null_image")
		return

	_dump_diagnostics(img)

	var png_bytes: PackedByteArray = img.save_png_to_buffer()
	var b64: String = Marshalls.raw_to_base64(png_bytes)
	print("RENDER ", b64)


func _dump_diagnostics(img: Image) -> void:
	# Print everything useful to stderr so the Python wrapper's stderr
	# drain surfaces it for us. Helps diagnose black-image issues.
	var n_objects := _builder.get_child_count()
	var n_overlays := _overlays.get_child_count()
	var vp_size := _viewport.size
	var img_size := Vector2i(img.get_width(), img.get_height())
	var cam_pos := _camera.global_position
	var cam_basis := _camera.global_transform.basis
	var blank := _image_is_blank(img)
	var sample_centre: Color = img.get_pixel(img.get_width() / 2, img.get_height() / 2)
	var sample_tl:     Color = img.get_pixel(0, 0)
	var sample_br:     Color = img.get_pixel(img.get_width() - 1, img.get_height() - 1)
	push_warning("[render-diag] viewport_size=%s img_size=%s room_children=%d overlay_children=%d cam_pos=%s blank=%s tl=%s centre=%s br=%s" % [
		vp_size, img_size, n_objects, n_overlays, cam_pos, blank,
		sample_tl, sample_centre, sample_br,
	])
	# Walk the room mount once and print bounding info for the first few
	# children so we can tell whether room_builder actually emitted
	# geometry (MeshInstance3D nodes).
	var mesh_count := 0
	for child in _builder.get_children():
		if child is MeshInstance3D:
			mesh_count += 1
		if mesh_count <= 3:
			push_warning("[render-diag]   room_child[%d] type=%s name=%s pos=%s" % [
				mesh_count, child.get_class(), child.name,
				(child as Node3D).global_position if child is Node3D else "n/a",
			])
	push_warning("[render-diag] total_mesh_instances=%d" % mesh_count)


func _image_is_blank(img: Image) -> bool:
	# Probe a handful of pixels; if all are zero (or alpha-only), the
	# viewport almost certainly never rendered.
	if img.get_width() < 2 or img.get_height() < 2:
		return false
	var samples: Array = [
		img.get_pixel(0, 0),
		img.get_pixel(img.get_width() - 1, 0),
		img.get_pixel(0, img.get_height() - 1),
		img.get_pixel(img.get_width() / 2, img.get_height() / 2),
	]
	for c in samples:
		if c.r > 0.01 or c.g > 0.01 or c.b > 0.01:
			return false
	return true


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
