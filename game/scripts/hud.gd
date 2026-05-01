extends CanvasLayer

# HUD: all 2D UI for the detective game.
#
# Always-visible: FPS, room name, status, crosshair, hover prompt, minimap,
# help bar.
#
# Modal panels (one at a time):
#   result, chat, inventory, accuse, accusation_result.
#
# Signals consumed by main.gd:
#   talk_submitted(character_name, question)
#   accusation_submitted(suspect, weapon, location)
#
# CLAUDE.md rule 9: ASCII-only text in user-visible UI.

signal talk_submitted(character_name: String, question: String)
signal accusation_submitted(suspect: String, weapon: String, location: String)
signal start_game_requested(mode: String, seed: String, openai_api_key: String)

# always-on
var _fps_label: Label
var _room_label: Label
var _budget_label: Label
var _status_label: Label
var _crosshair: Label
var _hover_label: Label
var _help_label: Label
var _minimap_label: Label

# modals
var _result_panel: Control
var _result_text: RichTextLabel

var _chat_panel: Control
var _chat_title: Label
var _chat_history: RichTextLabel
var _chat_input: LineEdit
var _chat_character_name: String = ""

var _inventory_panel: Control
var _inventory_text: RichTextLabel

var _accuse_panel: Control
var _accuse_suspect: LineEdit
var _accuse_weapon: LineEdit
var _accuse_location: LineEdit

var _result_modal_panel: Control
var _result_modal_text: RichTextLabel

var _case_panel: Control
var _case_text: RichTextLabel

var _start_panel: Control
var _start_seed_input: LineEdit
var _start_api_key_input: LineEdit
var _start_mode_buttons: ButtonGroup


func _ready() -> void:
	_build_always_on()
	_build_result_panel()
	_build_chat_panel()
	_build_inventory_panel()
	_build_accuse_panel()
	_build_accusation_result_panel()
	_build_case_panel()
	_build_start_panel()


# =============================================================================
# Always-on widgets
# =============================================================================

func _build_always_on() -> void:
	_fps_label = _make_label(Vector2(10, 10), 16)
	_room_label = _make_label(Vector2(10, 32), 22)
	_budget_label = _make_label(Vector2(10, 60), 16)
	_budget_label.modulate = Color(0.75, 0.95, 0.75)
	_status_label = _make_label(Vector2(10, 84), 14)
	_status_label.modulate = Color(1.0, 0.85, 0.55)
	add_child(_fps_label)
	add_child(_room_label)
	add_child(_budget_label)
	add_child(_status_label)

	_crosshair = _make_label(Vector2.ZERO, 20)
	_crosshair.text = "+"
	_crosshair.add_theme_color_override("font_color", Color(1, 1, 1, 0.85))
	_crosshair.set_anchors_preset(Control.PRESET_CENTER)
	_crosshair.position = Vector2(-6, -12)
	add_child(_crosshair)

	_hover_label = _make_label(Vector2.ZERO, 18)
	_hover_label.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	_hover_label.position = Vector2(-220, -110)
	_hover_label.size = Vector2(440, 30)
	_hover_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	add_child(_hover_label)

	_help_label = _make_label(Vector2(0, 0), 13)
	_help_label.text = "WASD move | E interact | C case file | TAB inventory | F accuse | ESC close"
	_help_label.modulate = Color(0.85, 0.85, 0.85)
	_help_label.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	_help_label.position = Vector2(10, -28)
	add_child(_help_label)

	_minimap_label = _make_label(Vector2(0, 0), 14)
	_minimap_label.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	_minimap_label.position = Vector2(-260, 10)
	_minimap_label.size = Vector2(250, 200)
	add_child(_minimap_label)


func _make_label(pos: Vector2, font_size: int) -> Label:
	var lbl := Label.new()
	lbl.position = pos
	lbl.add_theme_font_size_override("font_size", font_size)
	lbl.add_theme_color_override("font_color", Color(0.95, 0.95, 0.95))
	lbl.add_theme_color_override("font_outline_color", Color(0, 0, 0))
	lbl.add_theme_constant_override("outline_size", 4)
	return lbl


func set_fps(fps: int) -> void:
	if _fps_label:
		_fps_label.text = "FPS %d" % fps


func set_room_name(room_name: String) -> void:
	if _room_label:
		_room_label.text = "Room: %s" % room_name


func set_status(text: String) -> void:
	if _status_label:
		_status_label.text = text


func set_budget(remaining: int, taken: int) -> void:
	if _budget_label:
		_budget_label.text = "Actions: %d left  (%d taken)" % [remaining, taken]


func update_focused_entity(info: Dictionary) -> void:
	if info.is_empty():
		_hover_label.text = ""
		return
	var kind: String = String(info.get("kind", ""))
	var entity_name: String = String(info.get("name", ""))
	if kind == "object":
		_hover_label.text = "[E] Examine %s" % entity_name
	elif kind == "character":
		var alive: bool = bool(info.get("alive", true))
		if alive:
			_hover_label.text = "[E] Talk to %s" % entity_name
		else:
			_hover_label.text = "[BODY] %s" % entity_name
	else:
		_hover_label.text = ""


func update_world_graph(locations: Array) -> void:
	var lines: PackedStringArray = []
	lines.append("Map  (* here, x visited)")
	lines.append("")
	for loc in locations:
		var mark := " "
		if loc.get("current", false):
			mark = "*"
		elif loc.get("visited", false):
			mark = "x"
		lines.append("[%s] %s" % [mark, String(loc.get("name", "?"))])
	_minimap_label.text = "\n".join(lines)


# =============================================================================
# Modal builder helpers
# =============================================================================

func _make_modal_root() -> Control:
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_STOP

	var bg := ColorRect.new()
	bg.color = Color(0, 0, 0, 0.55)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	bg.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(bg)
	return root


func _make_modal_box(parent: Control, size: Vector2, anchor_preset: int = Control.PRESET_CENTER) -> VBoxContainer:
	var box := PanelContainer.new()
	box.set_anchors_preset(anchor_preset)
	box.position = Vector2(-size.x * 0.5, -size.y * 0.5)
	box.size = size
	parent.add_child(box)

	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	box.add_child(v)
	return v


func _make_richtext() -> RichTextLabel:
	var rt := RichTextLabel.new()
	rt.fit_content = false
	rt.scroll_active = true
	rt.bbcode_enabled = false
	rt.size_flags_vertical = Control.SIZE_EXPAND_FILL
	rt.custom_minimum_size = Vector2(0, 200)
	rt.add_theme_font_size_override("normal_font_size", 16)
	rt.add_theme_color_override("default_color", Color(0.95, 0.95, 0.95))
	return rt


func _make_hint(text: String) -> Label:
	var hint := Label.new()
	hint.text = text
	hint.add_theme_font_size_override("font_size", 13)
	hint.add_theme_color_override("font_color", Color(0.8, 0.8, 0.8))
	return hint


# =============================================================================
# Result panel (action observation)
# =============================================================================

func _build_result_panel() -> void:
	_result_panel = _make_modal_root()
	var v := _make_modal_box(_result_panel, Vector2(720, 280))

	var title := Label.new()
	title.add_theme_font_size_override("font_size", 20)
	title.text = "Observation"
	v.add_child(title)

	_result_text = _make_richtext()
	v.add_child(_result_text)

	v.add_child(_make_hint("[E] or [ESC] to continue"))

	_result_panel.visible = false
	add_child(_result_panel)


func show_result(text: String, evidence_found: Array = []) -> void:
	hide_all_modals()
	var body := text
	if evidence_found.size() > 0:
		var names: PackedStringArray = []
		for e in evidence_found:
			names.append(str(e))
		body += "\n\n[Evidence found] " + ", ".join(names)
	_result_text.text = body
	_result_panel.visible = true


func is_result_open() -> bool:
	return _result_panel != null and _result_panel.visible


func close_result() -> void:
	_result_panel.visible = false


# =============================================================================
# Chat panel (TALK_TO with multi-turn)
# =============================================================================

func _build_chat_panel() -> void:
	_chat_panel = _make_modal_root()
	var v := _make_modal_box(_chat_panel, Vector2(900, 420))

	_chat_title = Label.new()
	_chat_title.add_theme_font_size_override("font_size", 22)
	_chat_title.text = "Interview"
	v.add_child(_chat_title)

	_chat_history = _make_richtext()
	_chat_history.scroll_following = true
	_chat_history.custom_minimum_size = Vector2(0, 280)
	v.add_child(_chat_history)

	_chat_input = LineEdit.new()
	_chat_input.placeholder_text = "Ask a question. ENTER to send, ESC to leave."
	_chat_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_input.text_submitted.connect(_on_chat_submitted)
	v.add_child(_chat_input)

	v.add_child(_make_hint("ESC to leave the interview"))

	_chat_panel.visible = false
	add_child(_chat_panel)


func _on_chat_submitted(question: String) -> void:
	question = question.strip_edges()
	if question == "":
		return
	_chat_history.append_text("\n[You] %s\n" % question)
	_chat_input.text = ""
	_chat_input.editable = false
	_chat_input.placeholder_text = "Waiting for reply..."
	emit_signal("talk_submitted", _chat_character_name, question)


func open_chat(character_name: String) -> void:
	hide_all_modals()
	_chat_character_name = character_name
	_chat_title.text = "Interview: %s" % character_name
	_chat_history.text = "(Type a question below. Try asking where they were at the time of the murder.)"
	_chat_input.editable = true
	_chat_input.placeholder_text = "Ask a question. ENTER to send, ESC to leave."
	_chat_input.text = ""
	_chat_panel.visible = true
	_chat_input.grab_focus()


func append_chat_response(text: String) -> void:
	_chat_history.append_text("\n%s\n" % text)
	_chat_input.editable = true
	_chat_input.placeholder_text = "Ask another question. ENTER to send, ESC to leave."
	_chat_input.grab_focus()


func is_chat_open() -> bool:
	return _chat_panel != null and _chat_panel.visible


func close_chat() -> void:
	_chat_panel.visible = false
	_chat_character_name = ""


# =============================================================================
# Inventory panel
# =============================================================================

func _build_inventory_panel() -> void:
	_inventory_panel = _make_modal_root()
	var v := _make_modal_box(_inventory_panel, Vector2(560, 480))

	var title := Label.new()
	title.add_theme_font_size_override("font_size", 22)
	title.text = "Inventory"
	v.add_child(title)

	_inventory_text = _make_richtext()
	_inventory_text.custom_minimum_size = Vector2(0, 360)
	v.add_child(_inventory_text)

	v.add_child(_make_hint("[TAB] or [ESC] to close"))

	_inventory_panel.visible = false
	add_child(_inventory_panel)


func show_inventory(items: Array) -> void:
	hide_all_modals()
	if items.is_empty():
		_inventory_text.text = "(0 items)\n\nYou are not carrying any evidence yet."
	else:
		var lines: PackedStringArray = []
		lines.append("(%d items)\n" % items.size())
		for it in items:
			var nm := String(it.get("name", "?"))
			var desc := String(it.get("description", ""))
			lines.append("- %s" % nm)
			if desc != "":
				lines.append("    %s" % desc)
		_inventory_text.text = "\n".join(lines)
	_inventory_panel.visible = true


func is_inventory_open() -> bool:
	return _inventory_panel != null and _inventory_panel.visible


func close_inventory() -> void:
	_inventory_panel.visible = false


# =============================================================================
# Accuse panel
# =============================================================================

func _build_accuse_panel() -> void:
	_accuse_panel = _make_modal_root()
	var v := _make_modal_box(_accuse_panel, Vector2(620, 360))

	var title := Label.new()
	title.add_theme_font_size_override("font_size", 22)
	title.text = "Make your accusation"
	v.add_child(title)

	var help := Label.new()
	help.add_theme_font_size_override("font_size", 13)
	help.modulate = Color(0.85, 0.85, 0.85)
	help.text = "Type the suspect, weapon, and location names. ENTER on Location to submit."
	help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	v.add_child(help)

	_accuse_suspect = _accuse_field(v, "Suspect:")
	_accuse_weapon = _accuse_field(v, "Weapon:")
	_accuse_location = _accuse_field(v, "Location:")
	_accuse_location.text_submitted.connect(_on_accuse_submitted)

	v.add_child(_make_hint("ESC to cancel"))

	_accuse_panel.visible = false
	add_child(_accuse_panel)


func _accuse_field(parent: VBoxContainer, label_text: String) -> LineEdit:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var lbl := Label.new()
	lbl.text = label_text
	lbl.custom_minimum_size = Vector2(110, 0)
	lbl.add_theme_font_size_override("font_size", 16)
	row.add_child(lbl)
	var input := LineEdit.new()
	input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(input)
	parent.add_child(row)
	return input


func _on_accuse_submitted(_text: String) -> void:
	var s := _accuse_suspect.text.strip_edges()
	var w := _accuse_weapon.text.strip_edges()
	var l := _accuse_location.text.strip_edges()
	if s == "" or w == "" or l == "":
		return
	emit_signal("accusation_submitted", s, w, l)


func open_accuse() -> void:
	hide_all_modals()
	_accuse_suspect.text = ""
	_accuse_weapon.text = ""
	_accuse_location.text = ""
	_accuse_panel.visible = true
	_accuse_suspect.grab_focus()


func is_accuse_open() -> bool:
	return _accuse_panel != null and _accuse_panel.visible


func close_accuse() -> void:
	_accuse_panel.visible = false


# =============================================================================
# Accusation result panel (end of game)
# =============================================================================

func _build_accusation_result_panel() -> void:
	_result_modal_panel = _make_modal_root()
	var v := _make_modal_box(_result_modal_panel, Vector2(960, 640))

	_result_modal_text = _make_richtext()
	_result_modal_text.scroll_following = false
	_result_modal_text.custom_minimum_size = Vector2(0, 540)
	v.add_child(_result_modal_text)

	v.add_child(_make_hint("[ESC] to dismiss (game is over). Scroll for the full reveal."))

	_result_modal_panel.visible = false
	add_child(_result_modal_panel)


func show_accusation_result(correct: bool, observation: String, details: Dictionary, solution_text: String = "") -> void:
	hide_all_modals()
	var lines: PackedStringArray = []
	if solution_text != "":
		# Server-built reveal screen (mirrors the 2D solution screen).
		lines.append(solution_text)
	else:
		# Fallback if the server didn't supply solution_text.
		var verdict := ("CORRECT" if correct else "INCORRECT") + " accusation"
		lines.append(verdict)
		lines.append("")
		lines.append(observation)
		if details.has("partial_score"):
			lines.append("")
			lines.append("Partial score: %.2f / 1.00" % float(details["partial_score"]))
	_result_modal_text.text = "\n".join(lines)
	_result_modal_panel.visible = true


func is_accusation_result_open() -> bool:
	return _result_modal_panel != null and _result_modal_panel.visible


func close_accusation_result() -> void:
	_result_modal_panel.visible = false


# =============================================================================
# Case file panel
# =============================================================================

func _build_case_panel() -> void:
	_case_panel = _make_modal_root()
	var v := _make_modal_box(_case_panel, Vector2(820, 560))

	var title := Label.new()
	title.add_theme_font_size_override("font_size", 22)
	title.text = "Case File"
	v.add_child(title)

	_case_text = _make_richtext()
	_case_text.custom_minimum_size = Vector2(0, 460)
	v.add_child(_case_text)

	v.add_child(_make_hint("[C] or [ESC] to close"))

	_case_panel.visible = false
	add_child(_case_panel)


func show_case_file(data: Dictionary) -> void:
	hide_all_modals()
	var lines: PackedStringArray = []

	lines.append("THE CASE")
	lines.append("--------")
	lines.append("Victim:    %s" % String(data.get("victim_name", "?")))
	lines.append("Body in:   %s" % String(data.get("body_location_name", "?")))
	lines.append("Evidence:  %d / %d collected" % [
		int(data.get("evidence_found", 0)),
		int(data.get("evidence_total", 0)),
	])
	lines.append("Actions:   %d taken / %d remaining" % [
		int(data.get("actions_taken", 0)),
		int(data.get("budget_remaining", 0)),
	])
	lines.append("")

	var briefing := String(data.get("briefing", ""))
	if briefing != "":
		lines.append("BRIEFING")
		lines.append("--------")
		lines.append(briefing)
		lines.append("")

	var suspects: Array = data.get("suspects", [])
	lines.append("SUSPECTS (%d)" % suspects.size())
	lines.append("------------")
	for s in suspects:
		var nm := String(s.get("name", "?"))
		lines.append("  %s" % nm)
		var motive := String(s.get("motive", ""))
		if motive != "":
			lines.append("    motive:  %s" % motive)
		var build := String(s.get("build", ""))
		var hair  := String(s.get("hair", ""))
		var hands := String(s.get("hands", ""))
		var traits_parts: PackedStringArray = []
		if build != "": traits_parts.append(build)
		if hair  != "": traits_parts.append(hair)
		if hands != "": traits_parts.append(hands)
		if traits_parts.size() > 0:
			lines.append("    appears: %s" % ", ".join(traits_parts))
		lines.append("")

	var innocents: Array = data.get("innocents", [])
	if innocents.size() > 0:
		lines.append("INNOCENTS / WITNESSES (%d)" % innocents.size())
		lines.append("--------------------------")
		for c in innocents:
			lines.append("  %s" % String(c.get("name", "?")))
		lines.append("")

	_case_text.text = "\n".join(lines)
	_case_panel.visible = true


func is_case_open() -> bool:
	return _case_panel != null and _case_panel.visible


func close_case() -> void:
	_case_panel.visible = false


# =============================================================================
# Modal management
# =============================================================================

func is_any_modal_open() -> bool:
	return is_result_open() or is_chat_open() or is_inventory_open() or is_accuse_open() or is_accusation_result_open() or is_case_open()


func is_typing_modal_open() -> bool:
	# Modals that need the mouse free + focus a LineEdit
	return is_chat_open() or is_accuse_open()


func hide_all_modals() -> void:
	if _result_panel: _result_panel.visible = false
	if _chat_panel: _chat_panel.visible = false
	if _inventory_panel: _inventory_panel.visible = false
	if _accuse_panel: _accuse_panel.visible = false
	if _result_modal_panel: _result_modal_panel.visible = false
	if _case_panel: _case_panel.visible = false
	# Note: start panel is intentionally NOT hidden here. It's a pre-game
	# screen, controlled separately by show_start_form / hide_start_form.


# =============================================================================
# Start form (mode + seed + api key)
# =============================================================================

func _build_start_panel() -> void:
	_start_panel = _make_modal_root()
	var v := _make_modal_box(_start_panel, Vector2(640, 500))

	var title := Label.new()
	title.add_theme_font_size_override("font_size", 26)
	title.text = "MysteryArena 3D"
	v.add_child(title)

	var subtitle := Label.new()
	subtitle.add_theme_font_size_override("font_size", 14)
	subtitle.modulate = Color(0.85, 0.85, 0.85)
	subtitle.text = "First-person procedural murder mystery."
	v.add_child(subtitle)

	v.add_child(_make_spacer(8))

	# Mode
	var mode_label := Label.new()
	mode_label.add_theme_font_size_override("font_size", 14)
	mode_label.text = "Mode:"
	v.add_child(mode_label)

	_start_mode_buttons = ButtonGroup.new()
	var human_btn := CheckBox.new()
	human_btn.text = "Play yourself"
	human_btn.button_group = _start_mode_buttons
	human_btn.button_pressed = true
	human_btn.set_meta("mode", "human")
	v.add_child(human_btn)

	var vlm_btn := CheckBox.new()
	vlm_btn.text = "Watch a VLM agent  (coming soon)"
	vlm_btn.button_group = _start_mode_buttons
	vlm_btn.disabled = true
	vlm_btn.set_meta("mode", "vlm")
	v.add_child(vlm_btn)

	v.add_child(_make_spacer(8))

	# Seed
	var seed_row := HBoxContainer.new()
	seed_row.add_theme_constant_override("separation", 8)
	var seed_label := Label.new()
	seed_label.text = "Seed:"
	seed_label.custom_minimum_size = Vector2(120, 0)
	seed_row.add_child(seed_label)
	_start_seed_input = LineEdit.new()
	_start_seed_input.placeholder_text = "leave blank for random"
	_start_seed_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	seed_row.add_child(_start_seed_input)
	v.add_child(seed_row)

	# API key
	var key_row := HBoxContainer.new()
	key_row.add_theme_constant_override("separation", 8)
	var key_label := Label.new()
	key_label.text = "OpenAI key:"
	key_label.custom_minimum_size = Vector2(120, 0)
	key_row.add_child(key_label)
	_start_api_key_input = LineEdit.new()
	_start_api_key_input.placeholder_text = "sk-... (optional; deterministic NPCs without it)"
	_start_api_key_input.secret = true
	_start_api_key_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	key_row.add_child(_start_api_key_input)
	v.add_child(key_row)

	var key_help := Label.new()
	key_help.add_theme_font_size_override("font_size", 12)
	key_help.modulate = Color(0.75, 0.75, 0.75)
	key_help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	key_help.text = "The key is held in this process only — not written to disk."
	v.add_child(key_help)

	v.add_child(_make_spacer(12))

	var start_btn := Button.new()
	start_btn.text = "Start Game"
	start_btn.add_theme_font_size_override("font_size", 18)
	start_btn.custom_minimum_size = Vector2(0, 44)
	start_btn.pressed.connect(_on_start_clicked)
	v.add_child(start_btn)

	_start_panel.visible = false
	add_child(_start_panel)


func _make_spacer(h: int) -> Control:
	var c := Control.new()
	c.custom_minimum_size = Vector2(0, h)
	return c


func _on_start_clicked() -> void:
	var mode := "human"
	if _start_mode_buttons:
		var pressed := _start_mode_buttons.get_pressed_button()
		if pressed and pressed.has_meta("mode"):
			mode = String(pressed.get_meta("mode"))
	emit_signal(
		"start_game_requested",
		mode,
		_start_seed_input.text.strip_edges(),
		_start_api_key_input.text.strip_edges(),
	)


func show_start_form() -> void:
	_start_panel.visible = true
	_start_seed_input.grab_focus()


func hide_start_form() -> void:
	_start_panel.visible = false


func is_start_form_open() -> bool:
	return _start_panel != null and _start_panel.visible
