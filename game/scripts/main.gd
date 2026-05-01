extends Node3D

# Coordinates the WebSocket session, the 3D scene, the player, and the HUD.
#
# Responsibilities:
#   * Connect to the Python server, request the spawn room.
#   * Build/rebuild geometry on every `room` payload (see RoomBuilder).
#   * Spawn / teleport the player on every room reload.
#   * Own keyboard input that's NOT player movement: E / TAB / F / ESC.
#   * Route server messages to the right HUD panel based on _pending_action.
#   * Manage mouse capture + player physics gating around modals.
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
const TILE_M := 2.0

var _ws: WSClient
var _builder: RoomBuilder
var _player: CharacterBody3D
var _hud: CanvasLayer

var _transitioning: bool = false
var _pending_action: String = ""   # "examine" | "talk" | "take" | "inventory" | "accuse" | ""
var _focused_entity: Dictionary = {}
var _request_counter: int = 0


func _ready() -> void:
	var server_url := _resolve_server_url()
	print("[main] connecting to ", server_url)

	_hud = Hud.new()
	_hud.talk_submitted.connect(_on_talk_submitted)
	_hud.accusation_submitted.connect(_on_accusation_submitted)
	add_child(_hud)
	_hud.set_status("Connecting to %s ..." % server_url)
	_hud.set_room_name("(loading)")

	_add_safety_floor()

	_builder = RoomBuilder.new()
	_builder.door_entered.connect(_on_door_entered)
	add_child(_builder)

	_ws = WSClient.new(server_url)
	_ws.message_received.connect(_on_message)
	_ws.connection_opened.connect(_on_connected)
	_ws.connection_closed.connect(_on_disconnected)
	add_child(_ws)

	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


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


# =============================================================================
# WebSocket lifecycle
# =============================================================================

func _on_connected() -> void:
	print("[main] connected; requesting current room")
	_hud.set_status("Loading room ...")
	var rid := _next_rid("init")
	_ws.send_json({"type": "get_current_room", "request_id": rid})


func _on_disconnected(code: int, reason: String) -> void:
	print("[main] disconnected: ", code, " ", reason)
	_hud.set_status("Disconnected (code %d)" % code)


func _on_message(msg: Dictionary) -> void:
	match msg.get("type", ""):
		"room":
			_apply_room(msg)
		"action_result":
			_apply_action_result(msg)
		"inventory":
			_hud.show_inventory(msg.get("items", []))
			_pending_action = ""
			_set_modal_active(true)
		"accusation_result":
			_apply_accusation_result(msg)
		"world_graph":
			_hud.update_world_graph(msg.get("locations", []))
		"error":
			push_error("server error: " + str(msg.get("error", "")))
			_hud.set_status("Server error: " + str(msg.get("error", "")))
			# Re-enable chat input if a chat request errored
			if _hud.is_chat_open():
				_hud.append_chat_response("(server error: %s)" % str(msg.get("error", "")))
			_pending_action = ""
			_set_transitioning(false)
		"pong":
			pass
		_:
			push_warning("unknown server message: " + str(msg))


# =============================================================================
# Room transitions
# =============================================================================

func _apply_room(msg: Dictionary) -> void:
	var room_name: String = msg.get("name", "(unknown)")
	print("[main] received room: ", room_name)
	_builder.build_from(msg, TILE_M)
	_focused_entity = {}
	_hud.update_focused_entity({})
	_hud.update_world_graph(msg.get("world_graph", []))

	var spawn: Dictionary = msg.get("spawn", {})
	var sx: float = float(spawn.get("x", 1.0)) * TILE_M
	var sz: float = float(spawn.get("y", 1.0)) * TILE_M
	var facing_deg: float = float(spawn.get("facing_deg", 0.0))

	if _player == null:
		_player = Player.new()
		_player.focus_changed.connect(_on_focus_changed)
		add_child(_player)
	_player.transform.origin = Vector3(sx, 0.0, sz)
	_player.rotation.y = deg_to_rad(facing_deg)
	_player.velocity = Vector3.ZERO

	_hud.set_room_name(room_name)
	_hud.set_status("")
	_hud.hide_all_modals()
	_set_transitioning(false)


func _on_door_entered(leads_to: String) -> void:
	if _transitioning or leads_to == "":
		return
	if _hud.is_any_modal_open():
		return
	print("[main] door entered -> ", leads_to)
	_set_transitioning(true)
	_hud.set_status("Moving ...")
	_ws.send_json({
		"type": "move_to_room",
		"request_id": _next_rid("move"),
		"target_location_id": leads_to,
	})


func _set_transitioning(active: bool) -> void:
	_transitioning = active
	if _player != null:
		_player.set_physics_process(not active)


# =============================================================================
# Focus + interaction
# =============================================================================

func _on_focus_changed(info: Dictionary) -> void:
	_focused_entity = info
	_hud.update_focused_entity(info)


func _input(event: InputEvent) -> void:
	if not (event is InputEventKey):
		return
	var key_event := event as InputEventKey
	if not key_event.pressed or key_event.echo:
		return
	var key: int = key_event.keycode

	# When a typing modal is open, only ESC is intercepted globally — let the
	# LineEdit consume everything else.
	if _hud.is_typing_modal_open():
		if key == KEY_ESCAPE:
			_close_open_modal()
		return

	# Non-typing modals (result panels, inventory) can be dismissed with E/TAB/ESC.
	if _hud.is_any_modal_open():
		if key == KEY_ESCAPE or key == KEY_E or (key == KEY_TAB and _hud.is_inventory_open()):
			_close_open_modal()
			get_viewport().set_input_as_handled()
		return

	# Regular gameplay
	match key:
		KEY_ESCAPE:
			Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
			get_viewport().set_input_as_handled()
		KEY_E:
			_try_interact()
			get_viewport().set_input_as_handled()
		KEY_TAB:
			_request_inventory()
			get_viewport().set_input_as_handled()
		KEY_F:
			_open_accuse()
			get_viewport().set_input_as_handled()


func _close_open_modal() -> void:
	if _hud.is_chat_open():
		_hud.close_chat()
	elif _hud.is_accuse_open():
		_hud.close_accuse()
	elif _hud.is_inventory_open():
		_hud.close_inventory()
	elif _hud.is_result_open():
		_hud.close_result()
	elif _hud.is_accusation_result_open():
		_hud.close_accusation_result()
	_set_modal_active(false)


func _try_interact() -> void:
	if _focused_entity.is_empty():
		return
	var kind := String(_focused_entity.get("kind", ""))
	var nm := String(_focused_entity.get("name", ""))
	if kind == "object":
		_pending_action = "examine"
		_set_modal_active(true)
		_hud.set_status("Examining %s ..." % nm)
		_ws.send_json({
			"type": "examine_object",
			"request_id": _next_rid("ex"),
			"object_name": nm,
		})
	elif kind == "character":
		var alive: bool = bool(_focused_entity.get("alive", true))
		if not alive:
			# Looking at a body — show a flavor blurb (no server round-trip).
			_pending_action = ""
			_set_modal_active(true)
			_hud.show_result("The body of %s lies here. (You cannot question the dead.)" % nm)
			return
		_pending_action = "talk"
		_set_modal_active(true)
		_hud.open_chat(nm)


func _request_inventory() -> void:
	_pending_action = "inventory"
	_ws.send_json({"type": "inventory", "request_id": _next_rid("inv")})


func _open_accuse() -> void:
	_set_modal_active(true)
	_hud.open_accuse()


func _on_talk_submitted(character_name: String, question: String) -> void:
	_pending_action = "talk"
	_ws.send_json({
		"type": "talk_to",
		"request_id": _next_rid("talk"),
		"character_name": character_name,
		"question": question,
	})


func _on_accusation_submitted(suspect: String, weapon: String, location: String) -> void:
	_pending_action = "accuse"
	_hud.set_status("Submitting accusation ...")
	_ws.send_json({
		"type": "accuse",
		"request_id": _next_rid("acc"),
		"suspect_name": suspect,
		"weapon_name": weapon,
		"location_name": location,
	})


func _apply_action_result(msg: Dictionary) -> void:
	_hud.set_status("")
	var observation := String(msg.get("observation", ""))
	var evidence: Array = msg.get("evidence_found", [])
	if _pending_action == "talk":
		_hud.append_chat_response(observation)
	else:
		_hud.show_result(observation, evidence)
		_set_modal_active(true)
	_pending_action = ""


func _apply_accusation_result(msg: Dictionary) -> void:
	_pending_action = ""
	_hud.set_status("")
	_hud.show_accusation_result(
		bool(msg.get("correct", false)),
		String(msg.get("observation", "")),
		msg.get("details", {}),
	)
	_set_modal_active(true)


func _set_modal_active(active: bool) -> void:
	if _player != null:
		_player.set_physics_process(not active)
	if active and _hud.is_typing_modal_open():
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	elif not active:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _next_rid(prefix: String) -> String:
	_request_counter += 1
	return "%s-%d" % [prefix, _request_counter]


func _process(_delta: float) -> void:
	if _hud:
		_hud.set_fps(Engine.get_frames_per_second())
