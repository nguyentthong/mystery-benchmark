extends Node3D

# Main scene: connects to the Python WebSocket server, fetches the spawn
# room layout, builds geometry, and spawns the player.
#
# Server URL discovery (highest precedence first):
#   1. CLI: --server ws://host:port
#   2. Env: MYSTERY_SERVER_URL
#   3. Default: ws://127.0.0.1:7777

const WSClient   = preload("res://scripts/ws_client.gd")
const RoomBuilder = preload("res://scripts/room_builder.gd")
const Player     = preload("res://scripts/player.gd")
const Hud        = preload("res://scripts/hud.gd")

const DEFAULT_SERVER_URL := "ws://127.0.0.1:7777"
const TILE_M := 2.0  # 1 tile = 2 metres

var _ws: WSClient
var _builder: RoomBuilder
var _player: CharacterBody3D
var _hud: CanvasLayer
var _request_id: String = ""


func _ready() -> void:
	var server_url := _resolve_server_url()
	print("[main] connecting to ", server_url)

	_hud = Hud.new()
	add_child(_hud)
	_hud.set_status("Connecting to %s ..." % server_url)
	_hud.set_room_name("(loading)")

	_builder = RoomBuilder.new()
	add_child(_builder)

	_ws = WSClient.new(server_url)
	_ws.message_received.connect(_on_message)
	_ws.connection_opened.connect(_on_connected)
	_ws.connection_closed.connect(_on_disconnected)
	add_child(_ws)


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
		"pong":
			pass
		_:
			push_warning("unknown server message: " + str(msg))


func _apply_room(msg: Dictionary) -> void:
	var room_name: String = msg.get("name", "(unknown)")
	print("[main] received room: ", room_name)
	_builder.build_from(msg, TILE_M)

	# Spawn the player.
	var spawn: Dictionary = msg.get("spawn", {})
	var sx: float = float(spawn.get("x", 1.0)) * TILE_M
	var sz: float = float(spawn.get("y", 1.0)) * TILE_M
	var facing_deg: float = float(spawn.get("facing_deg", 0.0))

	_player = Player.new()
	_player.transform.origin = Vector3(sx, 0.0, sz)
	_player.rotation.y = deg_to_rad(facing_deg)
	add_child(_player)

	_hud.set_room_name(room_name)
	_hud.set_status("")  # clear


func _process(_delta: float) -> void:
	if _hud:
		_hud.set_fps(Engine.get_frames_per_second())
