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
const TILE_M := 1.0  # 1 tile = 1 metre, so an 18x13 room is ~18x13 metres.

var _ws: WSClient
var _builder: RoomBuilder
var _player: CharacterBody3D
var _hud: CanvasLayer

var _transitioning: bool = false
var _pending_action: String = ""   # "examine" | "talk" | "take" | "inventory" | "case_file" | "accuse" | ""
var _focused_entity: Dictionary = {}
var _request_counter: int = 0
var _briefing_shown: bool = false
var _game_over: bool = false
# After a room transition, the player is teleported to a spawn cell next to
# the door they entered through. Godot's Area3D for that door is *almost*
# touching the player's capsule (~0.15m gap with TILE_M=1m), and can fire
# body_entered on the frame the area is created — re-triggering MOVE and
# burning a second action. Suppress door triggers during a brief grace
# period after every spawn.
const DOOR_COOLDOWN_SEC := 0.5
var _door_cooldown: float = 0.0


func _ready() -> void:
	var server_url := _resolve_server_url()
	print("[main] connecting to ", server_url)

	_hud = Hud.new()
	_hud.talk_submitted.connect(_on_talk_submitted)
	_hud.accusation_submitted.connect(_on_accusation_submitted)
	_hud.start_game_requested.connect(_on_start_game_requested)
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

	# Mouse stays visible while the start form is open; player physics is
	# disabled until the world arrives (no _player exists yet anyway).
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


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
	print("[main] connected; showing start form")
	_hud.set_status("")
	_hud.show_start_form()


func _on_start_game_requested(_mode: String, seed: String, openai_api_key: String, complexity: String) -> void:
	print("[main] new_game requested seed=%s difficulty=%s key_provided=%s" % [seed, complexity, str(openai_api_key != "")])
	_hud.set_status("Starting new game ...")
	_briefing_shown = false
	_game_over = false
	_ws.send_json({
		"type": "new_game",
		"request_id": _next_rid("new"),
		"seed": seed,
		"openai_api_key": openai_api_key,
		"complexity": complexity,
	})


func _on_disconnected(code: int, reason: String) -> void:
	print("[main] disconnected: ", code, " ", reason)
	_hud.set_status("Disconnected (code %d)" % code)


func _on_message(msg: Dictionary) -> void:
	# Budget / status fields piggy-back on most response types.
	if msg.has("budget_remaining"):
		_hud.set_budget(int(msg["budget_remaining"]), int(msg.get("actions_taken", 0)))

	match msg.get("type", ""):
		"room":
			_apply_room(msg)
		"action_result":
			_apply_action_result(msg)
		"inventory":
			_hud.show_inventory(msg.get("items", []))
			_pending_action = ""
			_set_modal_active(true)
		"case_file":
			_hud.show_case_file(msg)
			_pending_action = ""
			_set_modal_active(true)
		"accusation_result":
			_apply_accusation_result(msg)
		"world_graph":
			_hud.update_world_graph(msg.get("locations", []))
		"talk_history":
			_hud.populate_chat_history(
				String(msg.get("character_name", "")),
				msg.get("history", []),
			)
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
	_hud.hide_start_form()
	_set_transitioning(false)
	_door_cooldown = DOOR_COOLDOWN_SEC

	# Auto-open the case file briefing on the very first room of a new game,
	# the way the 2D version greets the player with the case explanation.
	if not _briefing_shown:
		_briefing_shown = true
		_request_case_file()
	else:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _on_door_entered(leads_to: String) -> void:
	if _transitioning or leads_to == "":
		return
	if _door_cooldown > 0.0:
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

	# Start form (pre-game): let LineEdits and the Start button handle
	# everything. We don't intercept any keys here.
	if _hud.is_start_form_open():
		return

	# When a typing modal is open, only ESC is intercepted globally — let the
	# LineEdit consume everything else.
	if _hud.is_typing_modal_open():
		if key == KEY_ESCAPE:
			_close_open_modal()
		return

	# Non-typing modals (result panels, inventory, case file) can be dismissed
	# with E/TAB/C/ESC.
	if _hud.is_any_modal_open():
		var dismiss := key == KEY_ESCAPE or key == KEY_E
		if key == KEY_TAB and _hud.is_inventory_open():
			dismiss = true
		if key == KEY_C and _hud.is_case_open():
			dismiss = true
		if dismiss:
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
		KEY_C:
			_request_case_file()
			get_viewport().set_input_as_handled()


func _close_open_modal() -> void:
	if _hud.is_chat_open():
		_hud.close_chat()
	elif _hud.is_accuse_open():
		_hud.close_accuse()
	elif _hud.is_inventory_open():
		_hud.close_inventory()
	elif _hud.is_case_open():
		_hud.close_case()
	elif _hud.is_result_open():
		_hud.close_result()
	elif _hud.is_accusation_result_open():
		_hud.close_accusation_result()
		# Game ends after the accusation result panel is dismissed.
		if _game_over:
			print("[main] game over — quitting")
			get_tree().quit()
			return
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
			# The "body of X" WorldObject often isn't in
			# location.objects_here (room hits max_objects_per_room
			# first), so env.step(EXAMINE_OBJECT, ...) fails to find
			# it. Show a flavor blurb instead — the case file (key C)
			# also names the victim and body location.
			_pending_action = ""
			_set_modal_active(true)
			_hud.show_result(
				("The body of %s lies here. Examining the wounds suggests violent trauma " +
				"inflicted by a sharp or heavy instrument. (See the case file for full briefing.)") % nm
			)
			return
		_pending_action = "talk"
		_set_modal_active(true)
		_hud.open_chat(nm)
		# Pull any previous interview turns so the player can reread the
		# conversation rather than starting fresh each time they reopen
		# the chat.
		_ws.send_json({
			"type": "talk_history",
			"request_id": _next_rid("hist"),
			"character_name": nm,
		})


func _request_inventory() -> void:
	_pending_action = "inventory"
	_ws.send_json({"type": "inventory", "request_id": _next_rid("inv")})


func _request_case_file() -> void:
	_pending_action = "case_file"
	_ws.send_json({"type": "case_file", "request_id": _next_rid("case")})


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
		String(msg.get("solution_text", "")),
	)
	_set_modal_active(true)
	_game_over = true  # next dismissal of this panel quits the app


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


func _process(delta: float) -> void:
	if _hud:
		_hud.set_fps(Engine.get_frames_per_second())
	if _door_cooldown > 0.0:
		_door_cooldown = max(0.0, _door_cooldown - delta)
	_track_npcs_to_player(delta)


func _track_npcs_to_player(delta: float) -> void:
	# Alive NPCs rotate to face the player so their eyes follow you across
	# the room. Dead bodies stay in their fallen pose (CLAUDE.md rule 14:
	# bodies don't move).
	if _player == null or _builder == null:
		return
	var ppos: Vector3 = _player.global_position
	var turn_speed: float = 4.0
	for child in _builder.get_children():
		if not child is Node3D:
			continue
		if not child.has_meta("entity_kind"):
			continue
		if String(child.get_meta("entity_kind")) != "character":
			continue
		if not bool(child.get_meta("entity_alive", true)):
			continue
		var holder := child as Node3D
		var dir: Vector3 = ppos - holder.global_position
		dir.y = 0.0
		if dir.length() < 0.05:
			continue
		var target_yaw: float = atan2(dir.x, dir.z)
		holder.rotation.y = lerp_angle(holder.rotation.y, target_yaw, clampf(turn_speed * delta, 0.0, 1.0))
