extends Node

# Minimal WebSocketPeer wrapper.
#
# Emits:
#   connection_opened()
#   connection_closed(code, reason)
#   message_received(msg: Dictionary)   # parsed JSON

signal connection_opened
signal connection_closed(code: int, reason: String)
signal message_received(msg: Dictionary)

var _ws: WebSocketPeer
var _url: String
var _was_open: bool = false


func _init(url: String) -> void:
	_url = url
	_ws = WebSocketPeer.new()


func _ready() -> void:
	var err := _ws.connect_to_url(_url)
	if err != OK:
		push_error("WebSocket connect_to_url failed: %d" % err)


func _process(_delta: float) -> void:
	_ws.poll()
	var state := _ws.get_ready_state()

	if state == WebSocketPeer.STATE_OPEN:
		if not _was_open:
			_was_open = true
			emit_signal("connection_opened")
		while _ws.get_available_packet_count() > 0:
			var packet := _ws.get_packet()
			var text := packet.get_string_from_utf8()
			var parsed: Variant = JSON.parse_string(text)
			if typeof(parsed) == TYPE_DICTIONARY:
				emit_signal("message_received", parsed)
			else:
				push_warning("non-dict ws message: " + text)
	elif state == WebSocketPeer.STATE_CLOSED:
		var code := _ws.get_close_code()
		var reason := _ws.get_close_reason()
		emit_signal("connection_closed", code, reason)
		set_process(false)


func send_json(msg: Dictionary) -> void:
	if _ws.get_ready_state() != WebSocketPeer.STATE_OPEN:
		push_warning("send_json called while ws not open")
		return
	_ws.send_text(JSON.stringify(msg))


func close() -> void:
	_ws.close()
