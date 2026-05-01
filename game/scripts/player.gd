extends CharacterBody3D

# First-person controller.
#
# Locomotion: WASD on the XZ plane, no flying. Mouse rotates yaw (this body)
# and pitch (the camera), pitch clamped to +/- 85 degrees.
# Collider: CapsuleShape3D radius 0.35, height 1.7 — the 3D analog of the 2D
# AABB radius (CLAUDE.md rule 11).
# Camera at eye height 1.6 m.
#
# Press ESC to release the mouse for window controls; click to recapture.

const WALK_SPEED  := 4.0
const MOUSE_SENS  := 0.0025
const PITCH_LIMIT := deg_to_rad(85.0)
const GRAVITY     := 18.0
const EYE_HEIGHT  := 1.6
const CAPSULE_R   := 0.35
const CAPSULE_H   := 1.7

var _camera: Camera3D
var _yaw_velocity: float = 0.0


func _ready() -> void:
	# Collider
	var col := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = CAPSULE_R
	capsule.height = CAPSULE_H
	col.shape = capsule
	col.transform.origin = Vector3(0.0, CAPSULE_H * 0.5, 0.0)
	add_child(col)

	# Camera
	_camera = Camera3D.new()
	_camera.transform.origin = Vector3(0.0, EYE_HEIGHT, 0.0)
	_camera.fov = 75.0
	_camera.current = true
	add_child(_camera)

	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _input(event: InputEvent) -> void:
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		rotate_y(-event.relative.x * MOUSE_SENS)
		var new_pitch: float = _camera.rotation.x - event.relative.y * MOUSE_SENS
		_camera.rotation.x = clampf(new_pitch, -PITCH_LIMIT, PITCH_LIMIT)
		return

	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
		return

	if event is InputEventMouseButton and event.pressed:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _physics_process(delta: float) -> void:
	# Read WASD via physical key codes so the layout works on any keyboard.
	var input_dir := Vector3.ZERO
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

	# Convert player-local input direction to world space (XZ only).
	var world_dir := (transform.basis * input_dir)
	world_dir.y = 0.0
	if world_dir.length() > 0.0:
		world_dir = world_dir.normalized()

	velocity.x = world_dir.x * WALK_SPEED
	velocity.z = world_dir.z * WALK_SPEED

	# Simple gravity to keep the body grounded; no jumping in M1.
	if not is_on_floor():
		velocity.y -= GRAVITY * delta
	else:
		velocity.y = 0.0

	move_and_slide()
