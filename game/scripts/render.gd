extends Node3D

# Headless one-shot Godot renderer for MysteryArena (3D variant of M3).
#
# Used to talk to the Python side over stdin/stdout, but
# OS.read_string_from_stdin() in Godot 4 on macOS doesn't reliably receive
# bytes from a Python subprocess pipe -- the script reaches _ready() and
# prints READY, but no subsequent command line ever arrives. The protocol
# is now a localhost TCP socket: the Python side opens a listening server,
# launches Godot with --port=N as a user arg, Godot's render.gd connects
# back, and both sides exchange line-delimited JSON over that socket.
#
# Protocol
# --------
# Python (server) listens on 127.0.0.1:<port>.
# Godot (client) connects, then emits "READY\n" once.
# Then a request/response loop:
#   Python -> Godot:
#     {"cmd": "render",
#      "room": {<room dict from serialize_room>},
#      "evidence_overlays": [
#          {"id": "ev_123", "x": 4, "y": 7, "evidence_family": "bloodstain",
#           "visual_state": "BRIGHT"|"DULL"|"FADED"},
#          ...
#      ],
#      "width": 720, "height": 720}
#     {"cmd": "shutdown"}
#   Godot -> Python:
#     READY                              (once, on connect)
#     RENDER <base64-png>                (per render command)
#     ERR <message>                      (on bad input)
#
# All diagnostic output goes through printerr -> the socket isn't used for
# Godot's own debug log; that stays on stderr and is drained by the
# Python side's _drain_stderr thread.

const TILE_M: float = 1.0   # 1 tile = 1 metre.

const AGING_COLOR_BY_STATE: Dictionary = {
	"BRIGHT": Color(0.84, 0.15, 0.15),
	"DULL":   Color(0.55, 0.18, 0.12),
	"FADED":  Color(0.32, 0.22, 0.18),
}

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

var _socket: StreamPeerTCP = null
var _connected: bool = false
var _recv_buffer: String = ""
var _frame_counter: int = 0


func _ready() -> void:
	_camera.current = true
	if _world_env and _world_env.environment:
		_camera.environment = _world_env.environment

	var port: int = _parse_port_from_args()
	if port <= 0:
		printerr("[render] FATAL: no --port=N argument supplied.")
		printerr("[render] cmdline_args=", OS.get_cmdline_args())
		printerr("[render] cmdline_user_args=", OS.get_cmdline_user_args())
		get_tree().quit(1)
		return

	printerr("[render] connecting to 127.0.0.1:", port)
	_socket = StreamPeerTCP.new()
	var err: int = _socket.connect_to_host("127.0.0.1", port)
	if err != OK:
		printerr("[render] connect_to_host error=", err)
		get_tree().quit(1)
		return


func _parse_port_from_args() -> int:
	for arg in OS.get_cmdline_user_args():
		if str(arg).begins_with("--port="):
			return int(str(arg).substr("--port=".length()))
	for arg in OS.get_cmdline_args():
		if str(arg).begins_with("--port="):
			return int(str(arg).substr("--port=".length()))
	return -1


func _process(_delta: float) -> void:
	_frame_counter += 1
	if _frame_counter == 1 or _frame_counter % 120 == 0:
		printerr("[render] heartbeat frame=", _frame_counter, " connected=", _connected)

	if _socket == null:
		return
	_socket.poll()
	var status: int = _socket.get_status()

	if not _connected:
		if status == StreamPeerTCP.STATUS_CONNECTED:
			_connected = true
			_send_line("READY")
			printerr("[render] connected, sent READY")
		elif status == StreamPeerTCP.STATUS_ERROR:
			printerr("[render] connection error -- Godot couldn't reach the Python server")
			get_tree().quit(1)
		return

	if status != StreamPeerTCP.STATUS_CONNECTED:
		printerr("[render] socket dropped (status=", status, "); shutting down")
		get_tree().quit(0)
		return

	# Pull any available bytes off the socket into our line buffer, then
	# process complete lines one at a time.
	var available: int = _socket.get_available_bytes()
	if available > 0:
		var raw: String = _socket.get_utf8_string(available)
		_recv_buffer += raw
		while true:
			var nl_pos: int = _recv_buffer.find("\n")
			if nl_pos == -1:
				break
			var line: String = _recv_buffer.substr(0, nl_pos).strip_edges()
			_recv_buffer = _recv_buffer.substr(nl_pos + 1)
			if line.length() == 0:
				continue
			await _handle_command(line)


func _send_line(s: String) -> void:
	if _socket == null:
		return
	var data: PackedByteArray = (s + "\n").to_utf8_buffer()
	_socket.put_data(data)


func _handle_command(raw: String) -> void:
	printerr("[render] got command (len=", raw.length(), ")")
	var parsed = JSON.parse_string(raw)
	if parsed == null or typeof(parsed) != TYPE_DICTIONARY:
		printerr("[render] bad JSON (len=", raw.length(), ", head=", raw.substr(0, 120), ")")
		_send_line("ERR bad_json")
		return
	var cmd: String = String(parsed.get("cmd", ""))
	printerr("[render] cmd=", cmd)
	if cmd == "render":
		await _do_render(parsed)
	elif cmd == "shutdown":
		get_tree().quit(0)
	else:
		_send_line("ERR unknown_command:" + cmd)


func _do_render(cmd: Dictionary) -> void:
	printerr("[render] do_render start")
	var room: Dictionary = cmd.get("room", {})
	var overlays: Array = cmd.get("evidence_overlays", [])
	var width: int = int(cmd.get("width", 720))
	var height: int = int(cmd.get("height", 720))

	_viewport.size = Vector2i(width, height)
	printerr("[render] viewport sized to ", _viewport.size)

	for child in _builder.get_children():
		_builder.remove_child(child)
		child.queue_free()
	for child in _overlays.get_children():
		_overlays.remove_child(child)
		child.queue_free()

	if not room.is_empty():
		printerr("[render] building room w=", room.get("width"), " h=", room.get("height"),
			" objects=", room.get("objects", []).size(),
			" characters=", room.get("characters", []).size())
		_builder.build_from(room, TILE_M)
		var room_w: float = float(room.get("width", 1))
		var room_h: float = float(room.get("height", 1))
		_setup_camera(room_w * TILE_M, room_h * TILE_M)
		printerr("[render] camera at ", _camera.global_position, " builder children=", _builder.get_child_count())
	else:
		printerr("[render] room payload empty")

	_spawn_overlays(overlays)
	printerr("[render] spawned ", _overlays.get_child_count(), " overlays")

	# Force a fresh draw, then wait a couple of process_frames for the GPU
	# to actually emit pixels into the SubViewport texture.
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	await get_tree().process_frame
	await get_tree().process_frame
	await get_tree().process_frame
	printerr("[render] awaited 3 process_frames")

	var tex := _viewport.get_texture()
	if tex == null:
		printerr("[render] null texture")
		_send_line("ERR null_texture")
		return
	var img: Image = tex.get_image()
	if img == null:
		printerr("[render] null image")
		_send_line("ERR null_image")
		return

	_dump_diagnostics(img)

	var png_bytes: PackedByteArray = img.save_png_to_buffer()
	var b64: String = Marshalls.raw_to_base64(png_bytes)
	printerr("[render] emitting RENDER (b64 len=", b64.length(), ")")
	_send_line("RENDER " + b64)


func _dump_diagnostics(img: Image) -> void:
	var n_objects := _builder.get_child_count()
	var n_overlays := _overlays.get_child_count()
	var vp_size := _viewport.size
	var img_size := Vector2i(img.get_width(), img.get_height())
	var cam_pos := _camera.global_position
	var blank := _image_is_blank(img)
	var sample_centre: Color = img.get_pixel(img.get_width() / 2, img.get_height() / 2)
	var sample_tl:     Color = img.get_pixel(0, 0)
	var sample_br:     Color = img.get_pixel(img.get_width() - 1, img.get_height() - 1)
	printerr("[render-diag] viewport_size=", vp_size, " img_size=", img_size,
		" room_children=", n_objects, " overlay_children=", n_overlays,
		" cam_pos=", cam_pos, " blank=", blank,
		" tl=", sample_tl, " centre=", sample_centre, " br=", sample_br)
	var mesh_count := 0
	for child in _builder.get_children():
		if child is MeshInstance3D:
			mesh_count += 1
		if mesh_count <= 3:
			printerr("[render-diag]   room_child[", mesh_count, "] type=", child.get_class(),
				" name=", child.name,
				" pos=", ((child as Node3D).global_position if child is Node3D else "n/a"))
	printerr("[render-diag] total_mesh_instances=", mesh_count)


func _image_is_blank(img: Image) -> bool:
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
		var z: float = float(ov.get("y", 0)) + 0.5
		var kind: String = FAMILY_AGING_MESH.get(family, "disc")

		var node: Node3D = _make_overlay_mesh(kind, color, state)
		node.position = Vector3(x * TILE_M, 0.02, z * TILE_M)
		_overlays.add_child(node)


func _make_overlay_mesh(kind: String, color: Color, state: String) -> Node3D:
	var mat: StandardMaterial3D = StandardMaterial3D.new()
	mat.albedo_color = color
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
		var disc: CylinderMesh = CylinderMesh.new()
		disc.top_radius = 0.28
		disc.bottom_radius = 0.28
		disc.height = 0.02
		mi.mesh = disc
		mi.position = Vector3(0.0, 0.01, 0.0)
		mi.material_override = mat
	return mi
