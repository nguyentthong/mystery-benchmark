extends Node3D

# Main scene: connects to the Python WebSocket server, fetches the spawn
# room layout, builds geometry, spawns the player, and handles room
# transitions when the player walks through a door.
#
# Server URL discovery (highest precedence first):
#   1. CLI: -- --server ws://host:port
#   2. Env: MYSTERY_SERVER_URL
#   3. Default: ws://127.0.0.1:7777

const WSClient    = preload("res://scripts/ws_client.gd")
const RoomBuilder = preload("res://scripts/room_builder.gd")
const Player      = preload("res://scripts/player.gd")
const Hud         = preload("res://scripts/hud.gd")

const DEFAULT_SERVER_URL := "ws://127.0.0.1:7777"
const TILE_M := 2.0  # 1 tile = 2 metres

var _ws: WSClient
var _builder: RoomBuilder
var _player: CharacterBody3D
var _hud: CanvasLayer
var _request_id: String = ""
var _transitioning: bool = false


func _ready() -> void:
	var server_url := _resolve_server_url()
	print("[main] connecting to ", server_url)

	_hud = Hud.new()
	add_child(_hud)
	_hud.set_status("Connecting to %s ..." % server_url)
	_hud.set_room_name("(loading)")

	# Big safety floor so the player can never fall into the void if a room
	# transition lags or a layout has unexpected geometry. Sits below the
	# visible per-room floor (which is at y=0).
	_add_safety_floor()

	_builder = RoomBuilder.new()
	_builder.door_entered.connect(_on_door_entered)
	add_child(_builder)

	_ws = WSClient.new(server_url)
	_ws.message_received.connect(_on_message)
	_ws.connection_opened.connect(_on_connected)
	_ws.connection_closed.connect(_on_disconnected)
	add_child(_ws)


func _add_safety_floor() -> void:
	const SIZE := 1000.0
	const Y := -1.0
	var body := StaticBody3D.new()
	body.transform.origin = Vector3(0.0, Y, 0.0)
	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(SIZE, 0.5, SIZE)
	col.shape = shape
	body.add_child(col)
	add_child(body)


func _resolve_server_url() -> String:
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		var arg: String = args[i]
		if arg.begins_with("--server="):
			return arg.substr("--server=".length())
		if arg == "--server" and i + 1 < args.size():
			return args[i + 1]
	var env_url := OS.get_environment("MYSTERY_SERVER_URL")
	if env_url != "":
		return env_url
	return DEFAULT_SERVER_URL


func _on_connected() -> void:
	print("[main] connected; requesting current room")
	_hud.set_status("Loading room ...")
	_request_id = "init-%d" % Time.get_ticks_msec()
	_ws.send_json({
		"type": "get_current_room",
		"request_id": _request_id,
	})


func _on_disconnected(code: int, reason: String) -> void:
	print("[main] disconnected: ", code, " ", reason)
	_hud.set_status("Disconnected (code %d)" % code)


func _on_message(msg: Dictionary) -> void:
	match msg.get("type", ""):
		"room":
			_apply_room(msg)
		"error":
			push_error("server error: " + str(msg.get("error", "")))
			_hud.set_status("Server error: " + str(msg.get("error", "")))
			_set_transitioning(false)
		"pong":
			pass
		_:
			push_warning("unknown server message: " + str(msg))


func _apply_room(msg: Dictionary) -> void:
	var room_name: String = msg.get("name", "(unknown)")
	print("[main] received room: ", room_name)
	_builder.build_from(msg, TILE_M)

	var spawn: Dictionary = msg.get("spawn", {})
	var sx: float = float(spawn.get("x", 1.0)) * TILE_M
	var sz: float = float(spawn.get("y", 1.0)) * TILE_M
	var facing_deg: float = float(spawn.get("facing_deg", 0.0))

	if _player == null:
		_player = Player.new()
		add_child(_player)
	# Teleport to the new spawn (used for both initial spawn and room
	# transitions). Setting transform.origin sidesteps any leftover physics
	# velocity from the previous room.
	_player.transform.origin = Vector3(sx, 0.0, sz)
	_player.rotation.y = deg_to_rad(facing_deg)
	_player.velocity = Vector3.ZERO

	_hud.set_room_name(room_name)
	_hud.set_status("")

	_set_transitioning(false)


func _on_door_entered(leads_to: String) -> void:
	if _transitioning:
		return
	if leads_to == "":
		return
	print("[main] door entered -> ", leads_to)
	_set_transitioning(true)
	_hud.set_status("Moving ...")
	_request_id = "move-%d" % Time.get_ticks_msec()
	_ws.send_json({
		"type": "move_to_room",
		"request_id": _request_id,
		"target_location_id": leads_to,
	})


func _set_transitioning(active: bool) -> void:
	_transitioning = active
	if _player != null:
		_player.set_physics_process(not active)


func _process(_delta: float) -> void:
	if _hud:
		_hud.set_fps(Engine.get_frames_per_second())
