extends CanvasLayer

# Minimal HUD: FPS + current room name. ASCII only (CLAUDE.md rule 9).
# Status line is for transient connection messages; clears once the room
# is loaded.

var _fps_label: Label
var _room_label: Label
var _status_label: Label


func _ready() -> void:
	_fps_label = _make_label(Vector2(10, 10), 18)
	_room_label = _make_label(Vector2(10, 36), 22)
	_status_label = _make_label(Vector2(10, 70), 16)
	add_child(_fps_label)
	add_child(_room_label)
	add_child(_status_label)


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
