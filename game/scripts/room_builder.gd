extends Node3D

# Builds a 3D room from the server's tile-grid response.
#
# Tile codes from server (see server/godot_server.py serialize_room):
#   "F" = floor
#   "W" = wall
#   "D" = door (walkable; in M1 the door is just a gap in the wall, no
#                interactable trigger yet)
#
# Coordinate convention:
#   tile (tx, ty) center is at world (tx * TILE_M + TILE_M/2, *, ty * TILE_M + TILE_M/2)
#   tile X maps to world X (east), tile Y maps to world Z (south), Y is up.
#
# CLAUDE.md rule 11: walls are StaticBody3D with BoxShape3D collider matching
# the visual mesh, so AABB collision is exact.

const WALL_HEIGHT := 3.0
const FLOOR_COLOR := Color(0.45, 0.36, 0.28)
const WALL_COLOR  := Color(0.78, 0.76, 0.72)


func build_from(room: Dictionary, tile_m: float) -> void:
	# Clear any previous geometry (in case M2 hot-reloads).
	for child in get_children():
		child.queue_free()

	var width: int = int(room.get("width", 0))
	var height: int = int(room.get("height", 0))
	var tiles: Array = room.get("tiles", [])

	if width == 0 or height == 0 or tiles.is_empty():
		push_error("room_builder: empty room payload")
		return

	_add_floor(width, height, tile_m)
	_add_walls(tiles, width, height, tile_m)
	_add_lighting(width, height, tile_m)


func _add_floor(width: int, height: int, tile_m: float) -> void:
	var size_x: float = width * tile_m
	var size_z: float = height * tile_m
	var center := Vector3(size_x * 0.5, 0.0, size_z * 0.5)

	var mat := StandardMaterial3D.new()
	mat.albedo_color = FLOOR_COLOR
	mat.roughness = 0.95

	# Visible top surface
	var floor_mesh := PlaneMesh.new()
	floor_mesh.size = Vector2(size_x, size_z)
	floor_mesh.material = mat
	var mi := MeshInstance3D.new()
	mi.mesh = floor_mesh
	mi.transform.origin = center
	add_child(mi)

	# Collider: a thin slab so the player's capsule rests on top at y=0.
	# Without this, gravity pulls the player through the floor forever.
	const SLAB := 0.2
	var body := StaticBody3D.new()
	body.transform.origin = Vector3(center.x, -SLAB * 0.5, center.z)
	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(size_x, SLAB, size_z)
	col.shape = shape
	body.add_child(col)
	add_child(body)


func _add_walls(tiles: Array, width: int, height: int, tile_m: float) -> void:
	var wall_mat := StandardMaterial3D.new()
	wall_mat.albedo_color = WALL_COLOR
	wall_mat.roughness = 0.85

	for x in width:
		var col: Array = tiles[x]
		for y in height:
			if str(col[y]) != "W":
				continue
			_add_wall_block(x, y, tile_m, wall_mat)


func _add_wall_block(tx: int, ty: int, tile_m: float, mat: StandardMaterial3D) -> void:
	var body := StaticBody3D.new()
	body.transform.origin = Vector3(
		tx * tile_m + tile_m * 0.5,
		WALL_HEIGHT * 0.5,
		ty * tile_m + tile_m * 0.5,
	)

	var box := BoxMesh.new()
	box.size = Vector3(tile_m, WALL_HEIGHT, tile_m)
	box.material = mat
	var mi := MeshInstance3D.new()
	mi.mesh = box
	body.add_child(mi)

	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(tile_m, WALL_HEIGHT, tile_m)
	col.shape = shape
	body.add_child(col)

	add_child(body)


func _add_lighting(width: int, height: int, tile_m: float) -> void:
	var light := DirectionalLight3D.new()
	light.transform = Transform3D(
		Basis.from_euler(Vector3(deg_to_rad(-50.0), deg_to_rad(35.0), 0.0)),
		Vector3(width * tile_m * 0.5, 6.0, height * tile_m * 0.5)
	)
	light.light_energy = 0.9
	add_child(light)

	# A bit of ambient so corners aren't pitch black.
	var env := WorldEnvironment.new()
	var e := Environment.new()
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.12, 0.12, 0.14)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.55, 0.55, 0.6)
	e.ambient_light_energy = 0.5
	env.environment = e
	add_child(env)
