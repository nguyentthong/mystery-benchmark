extends CharacterBody3D

# First-person controller.
#
# Locomotion: WASD on the XZ plane, no flying. Mouse rotates yaw (this body)
# and pitch (the camera), pitch clamped to +/- 85 degrees.
# Collider: CapsuleShape3D radius 0.35, height 1.7 (CLAUDE.md rule 11).
# Camera at eye height 1.6 m.
#
# Each frame, casts a ray ~3.5m forward from the camera and emits
# `focus_changed(info)` whenever the focused entity changes. `info` is empty
# `{}` for "nothing in focus", or `{kind, name, id, alive?, role?}` resolved
# from metadata set by room_builder.gd on each entity holder.
#
# Mouse capture is owned by main.gd (it knows when modals are open). This
# script only emits movement/focus.

signal focus_changed(info: Dictionary)

const WALK_SPEED  := 4.0
const MOUSE_SENS  := 0.0025
const PITCH_LIMIT := deg_to_rad(85.0)
const GRAVITY     := 18.0
const EYE_HEIGHT  := 1.6
const CAPSULE_R   := 0.35
const CAPSULE_H   := 1.7
const RAY_LENGTH  := 3.5

var _camera: Camera3D
var _last_focus: Dictionary = {}


func _ready() -> void:
	var col := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = CAPSULE_R
	capsule.height = CAPSULE_H
	col.shape = capsule
	col.transform.origin = Vector3(0.0, CAPSULE_H * 0.5, 0.0)
	add_child(col)

	_camera = Camera3D.new()
	_camera.transform.origin = Vector3(0.0, EYE_HEIGHT, 0.0)
	_camera.fov = 75.0
	_camera.current = true
	add_child(_camera)


func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		rotate_y(-event.relative.x * MOUSE_SENS)
		var new_pitch: float = _camera.rotation.x - event.relative.y * MOUSE_SENS
		_camera.rotation.x = clampf(new_pitch, -PITCH_LIMIT, PITCH_LIMIT)


func _physics_process(delta: float) -> void:
	# Movement is gated by main.gd via set_physics_process(); when a modal is
	# open the controller is paused.
	var input_dir := Vector3.ZERO
	if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		if Input.is_physical_key_pressed(KEY_W):
			input_dir.z -= 1.0
		if Input.is_physical_key_pressed(KEY_S):
			input_dir.z += 1.0
		if Input.is_physical_key_pressed(KEY_A):
			input_dir.x -= 1.0
		if Input.is_physical_key_pressed(KEY_D):
			input_dir.x += 1.0

	if input_dir.length() > 0.0:
		input_dir = input_dir.normalized()

	var world_dir := (transform.basis * input_dir)
	world_dir.y = 0.0
	if world_dir.length() > 0.0:
		world_dir = world_dir.normalized()

	velocity.x = world_dir.x * WALK_SPEED
	velocity.z = world_dir.z * WALK_SPEED

	if not is_on_floor():
		velocity.y -= GRAVITY * delta
	else:
		velocity.y = 0.0

	move_and_slide()


func _process(_delta: float) -> void:
	var focus := _do_raycast()
	if not _focus_eq(focus, _last_focus):
		_last_focus = focus
		emit_signal("focus_changed", focus)


func _do_raycast() -> Dictionary:
	if _camera == null:
		return {}
	var space := get_world_3d().direct_space_state
	var origin := _camera.global_position
	var forward := -_camera.global_transform.basis.z
	var query := PhysicsRayQueryParameters3D.create(origin, origin + forward * RAY_LENGTH)
	query.exclude = [self.get_rid()]
	var hit := space.intersect_ray(query)
	if hit.is_empty():
		return {}
	var node: Node = hit.get("collider")
	while node != null:
		if node.has_meta("entity_kind"):
			var info := {
				"kind":  String(node.get_meta("entity_kind")),
				"name":  String(node.get_meta("entity_name", "")),
				"id":    String(node.get_meta("entity_id", "")),
			}
			if node.has_meta("entity_alive"):
				info["alive"] = bool(node.get_meta("entity_alive"))
			if node.has_meta("entity_role"):
				info["role"] = String(node.get_meta("entity_role"))
			if node.has_meta("entity_subkind"):
				info["subkind"] = String(node.get_meta("entity_subkind"))
			return info
		node = node.get_parent()
	return {}


func _focus_eq(a: Dictionary, b: Dictionary) -> bool:
	return String(a.get("id", "")) == String(b.get("id", ""))
