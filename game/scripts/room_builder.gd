extends Node3D

# Builds a 3D room from the server's tile-grid response.
#
# Tile codes (see server/godot_server.py serialize_room):
#   "F" = floor
#   "W" = wall    (solid, full height)
#   "D" = door    (gap in wall — visual floor marker only; an Area3D trigger
#                  fires `door_entered(leads_to)` when the player overlaps)
#
# Entities (CLAUDE.md rule 5: always visibly drawn — never filter by state):
#   objects     -> small coloured boxes with name labels
#   characters  -> capsules with name labels (alive: standing; dead body: laid
#                  flat in red — CLAUDE.md rule 14: bodies don't move)
#
# Coordinate convention:
#   tile (tx, ty) center is at world (tx * TILE_M + TILE_M/2, *, ty * TILE_M + TILE_M/2)
#   tile X -> world X (east), tile Y -> world Z (south), Y is up.
#
# Collision (CLAUDE.md rule 11): walls/objects/characters have BoxShape3D
# colliders matching their visual mesh, so AABB collision is exact. Player is
# a CapsuleShape3D — see player.gd.

signal door_entered(leads_to: String)

const WALL_HEIGHT := 3.0
const FLOOR_COLOR := Color(0.45, 0.36, 0.28)
const WALL_COLOR  := Color(0.78, 0.76, 0.72)
const DOOR_FLOOR_COLOR := Color(0.55, 0.32, 0.18)  # warm wood patch on floor

const OBJECT_COLOR        := Color(0.85, 0.78, 0.45)  # generic prop: tan
const WEAPON_COLOR        := Color(0.85, 0.30, 0.25)  # weapon: red
const MURDER_WEAPON_COLOR := Color(0.55, 0.10, 0.10)  # murder weapon: dark red

const SUSPECT_COLOR  := Color(0.30, 0.55, 0.85)
const INNOCENT_COLOR := Color(0.40, 0.75, 0.45)
const WITNESS_COLOR  := Color(0.65, 0.55, 0.35)
const VICTIM_COLOR   := Color(0.70, 0.20, 0.20)

const LABEL_FONT_SIZE := 32
const LABEL_PIXEL_SIZE := 0.004

const ASSET_MAP_PATH := "res://config/asset_map.json"

# Loaded lazily via _load_asset_map(). When the user has not run the Kenney
# download script, this stays {} and every entity falls back to a coloured
# cube / capsule — no error.
var _asset_map: Dictionary = {}
var _asset_map_loaded: bool = false


func build_from(room: Dictionary, tile_m: float) -> void:
	# Clear previous geometry (transitions reuse the same RoomBuilder node).
	for child in get_children():
		child.queue_free()

	var width: int = int(room.get("width", 0))
	var height: int = int(room.get("height", 0))
	var tiles: Array = room.get("tiles", [])

	if width == 0 or height == 0 or tiles.is_empty():
		push_error("room_builder: empty room payload")
		return

	_load_asset_map()

	_add_floor(width, height, tile_m, tiles)
	_add_walls(tiles, width, height, tile_m)
	_add_door_triggers(room.get("doors", []), tile_m)
	_add_objects(room.get("objects", []), tile_m)
	_add_characters(room.get("characters", []), tile_m)
	_add_lighting(width, height, tile_m)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

func _add_floor(width: int, height: int, tile_m: float, tiles: Array) -> void:
	var size_x: float = width * tile_m
	var size_z: float = height * tile_m
	var center := Vector3(size_x * 0.5, 0.0, size_z * 0.5)

	var floor_mat := StandardMaterial3D.new()
	floor_mat.albedo_color = FLOOR_COLOR
	floor_mat.roughness = 0.95

	# Visible top surface
	var floor_mesh := PlaneMesh.new()
	floor_mesh.size = Vector2(size_x, size_z)
	floor_mesh.material = floor_mat
	var mi := MeshInstance3D.new()
	mi.mesh = floor_mesh
	mi.transform.origin = center
	add_child(mi)

	# Collider slab so the player rests on top at y=0.
	const SLAB := 0.2
	var body := StaticBody3D.new()
	body.transform.origin = Vector3(center.x, -SLAB * 0.5, center.z)
	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(size_x, SLAB, size_z)
	col.shape = shape
	body.add_child(col)
	add_child(body)

	# Door floor patches — visually distinct so the player can spot doors.
	var door_mat := StandardMaterial3D.new()
	door_mat.albedo_color = DOOR_FLOOR_COLOR
	door_mat.roughness = 0.7
	for x in width:
		var col_arr: Array = tiles[x]
		for y in height:
			if str(col_arr[y]) != "D":
				continue
			var patch := PlaneMesh.new()
			patch.size = Vector2(tile_m, tile_m)
			patch.material = door_mat
			var pmi := MeshInstance3D.new()
			pmi.mesh = patch
			# Slight Y offset to avoid z-fighting with the main floor
			pmi.transform.origin = Vector3(
				x * tile_m + tile_m * 0.5, 0.01, y * tile_m + tile_m * 0.5
			)
			add_child(pmi)


func _add_walls(tiles: Array, width: int, height: int, tile_m: float) -> void:
	var wall_mat := StandardMaterial3D.new()
	wall_mat.albedo_color = WALL_COLOR
	wall_mat.roughness = 0.85

	for x in width:
		var col_arr: Array = tiles[x]
		for y in height:
			if str(col_arr[y]) != "W":
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


# ---------------------------------------------------------------------------
# Door triggers
# ---------------------------------------------------------------------------

func _add_door_triggers(doors: Array, tile_m: float) -> void:
	for d in doors:
		var dx: int = int(d.get("x", 0))
		var dy: int = int(d.get("y", 0))
		var leads_to: String = String(d.get("leads_to", ""))
		var leads_to_name: String = String(d.get("leads_to_name", leads_to))
		if leads_to == "":
			continue

		var area := Area3D.new()
		area.transform.origin = Vector3(
			dx * tile_m + tile_m * 0.5,
			WALL_HEIGHT * 0.5,
			dy * tile_m + tile_m * 0.5,
		)
		var col := CollisionShape3D.new()
		var shape := BoxShape3D.new()
		shape.size = Vector3(tile_m, WALL_HEIGHT, tile_m)
		col.shape = shape
		area.add_child(col)
		# CLAUDE.md rule 12: trigger on AABB overlap, not centre point.
		area.body_entered.connect(_on_door_body_entered.bind(leads_to))
		add_child(area)

		# Floating "Door -> Billiard Room" sign above the doorway so the
		# player knows where each door leads. ASCII only (CLAUDE.md rule 9).
		var label := Label3D.new()
		label.text = "-> %s" % leads_to_name
		label.font_size = LABEL_FONT_SIZE
		label.pixel_size = LABEL_PIXEL_SIZE
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.modulate = Color(1.0, 0.85, 0.55)
		label.outline_size = 8
		label.outline_modulate = Color(0, 0, 0)
		label.transform.origin = Vector3(
			dx * tile_m + tile_m * 0.5,
			WALL_HEIGHT - 0.4,
			dy * tile_m + tile_m * 0.5,
		)
		add_child(label)


func _on_door_body_entered(body: Node, leads_to: String) -> void:
	if body is CharacterBody3D:
		emit_signal("door_entered", leads_to)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

func _add_objects(objects: Array, tile_m: float) -> void:
	for o in objects:
		var kind: String = String(o.get("kind", "object"))
		var color: Color
		match kind:
			"weapon":        color = WEAPON_COLOR
			"murder_weapon": color = MURDER_WEAPON_COLOR
			_:               color = OBJECT_COLOR

		var entity_name: String = String(o.get("name", "object"))
		var ox: int = int(o.get("x", 0))
		var oy: int = int(o.get("y", 0))

		var holder := _spawn_prop_box(ox, oy, tile_m, color, entity_name, 0.55)
		holder.set_meta("entity_kind", "object")
		holder.set_meta("entity_name", entity_name)
		holder.set_meta("entity_id", String(o.get("id", "")))
		holder.set_meta("entity_subkind", kind)


func _spawn_prop_box(tx: int, ty: int, tile_m: float, color: Color, label_text: String, size: float) -> Node3D:
	var holder := Node3D.new()
	holder.transform.origin = Vector3(
		tx * tile_m + tile_m * 0.5,
		0.0,
		ty * tile_m + tile_m * 0.5,
	)
	add_child(holder)

	# Try to load a Kenney mesh first; fall back to a tinted cube on failure
	# so the game stays playable even when no assets are installed.
	var resolved := _resolve_asset_full("objects", label_text, "")
	var glb := _try_instance_asset(String(resolved.get("path", "")))

	var body := StaticBody3D.new()

	if glb != null:
		# Kenney models have their origin at the base (floor), so place the
		# body at y=0 — adding the cube's half-height offset would lift the
		# model off the ground.
		body.transform.origin = Vector3(0.0, 0.0, 0.0)
		var s: float = float(resolved.get("scale", 1.0))
		if not is_equal_approx(s, 1.0):
			glb.scale = Vector3(s, s, s)
		# Try to load the sibling .png so the prop shows Kenney's natural
		# colors. Only fall back to a flat category tint if no texture is
		# available.
		var asset_path := String(resolved.get("path", ""))
		var tex := _try_load_sibling_texture(asset_path)
		if tex != null:
			_apply_external_texture(glb, tex)
		else:
			_apply_tint(glb, color)
		body.add_child(glb)
		# Generic AABB collider sized to the slot. Approximate per CLAUDE.md
		# rule 11 (AABB collision, not pixel-perfect).
		var col := CollisionShape3D.new()
		var shape := BoxShape3D.new()
		var col_size: float = size * max(1.0, s)
		shape.size = Vector3(col_size, col_size, col_size)
		col.shape = shape
		col.transform.origin = Vector3(0.0, col_size * 0.5, 0.0)
		body.add_child(col)
	else:
		body.transform.origin = Vector3(0.0, size * 0.5, 0.0)
		var mat := StandardMaterial3D.new()
		mat.albedo_color = color
		mat.roughness = 0.6
		var box := BoxMesh.new()
		box.size = Vector3(size, size, size)
		box.material = mat
		var mi := MeshInstance3D.new()
		mi.mesh = box
		body.add_child(mi)
		var col := CollisionShape3D.new()
		var shape := BoxShape3D.new()
		shape.size = Vector3(size, size, size)
		col.shape = shape
		body.add_child(col)

	holder.add_child(body)
	_attach_label(holder, label_text, size + 0.3, color)
	return holder


func _add_characters(characters: Array, tile_m: float) -> void:
	for c in characters:
		var entity_name: String = String(c.get("name", "?"))
		var role: String = String(c.get("role", "innocent"))
		var alive: bool = bool(c.get("alive", true))
		var cx: int = int(c.get("x", 0))
		var cy: int = int(c.get("y", 0))

		var color: Color
		if not alive or role == "victim":
			color = VICTIM_COLOR
		else:
			match role:
				"suspect":  color = SUSPECT_COLOR
				"witness":  color = WITNESS_COLOR
				_:          color = INNOCENT_COLOR

		var holder := _spawn_character(cx, cy, tile_m, color, entity_name, alive, role)
		holder.set_meta("entity_kind", "character")
		holder.set_meta("entity_name", entity_name)
		holder.set_meta("entity_id", String(c.get("id", "")))
		holder.set_meta("entity_alive", alive)
		holder.set_meta("entity_role", role)


func _spawn_character(
	tx: int,
	ty: int,
	tile_m: float,
	color: Color,
	display_name: String,
	alive: bool,
	role: String,
) -> Node3D:
	var holder := Node3D.new()
	holder.transform.origin = Vector3(
		tx * tile_m + tile_m * 0.5,
		0.0,
		ty * tile_m + tile_m * 0.5,
	)
	add_child(holder)

	var resolved := _resolve_asset_full("characters", display_name, role)
	var glb := _try_instance_asset(String(resolved.get("path", "")))

	var body := StaticBody3D.new()

	var label_prefix := ""
	var label_height: float = 2.1
	var glb_path_present := glb != null

	if alive:
		# Kenney character models have origin at the FEET; place body at y=0.
		# Capsule fallback is centered, so it gets y=0.85.
		if glb_path_present:
			body.transform.origin = Vector3(0.0, 0.0, 0.0)
		else:
			body.transform.origin = Vector3(0.0, 0.85, 0.0)
	else:
		# CLAUDE.md rule 14: bodies don't move. Lay flat by rotating 90 deg
		# around Z. With a feet-origin GLB the model rotates around its feet
		# (slightly off centre but readable as a fallen body).
		if glb_path_present:
			body.transform.origin = Vector3(0.0, 0.0, 0.0)
		else:
			body.transform.origin = Vector3(0.0, 0.35, 0.0)
		body.rotation = Vector3(0.0, 0.0, deg_to_rad(90.0))
		label_prefix = "[BODY] "
		label_height = 1.2

	if glb != null:
		var s: float = float(resolved.get("scale", 1.0))
		if not is_equal_approx(s, 1.0):
			glb.scale = Vector3(s, s, s)
		# Characters: keep Kenney's natural skin / clothing. Try to load the
		# sibling .png as the surface texture; if that fails, leave the GLB's
		# embedded materials alone. The role colour stays only on the label.
		var asset_path := String(resolved.get("path", ""))
		var tex := _try_load_sibling_texture(asset_path)
		if tex != null:
			_apply_external_texture(glb, tex)
		body.add_child(glb)
	else:
		var mat := StandardMaterial3D.new()
		mat.albedo_color = color
		mat.roughness = 0.7
		var visual := MeshInstance3D.new()
		var capsule := CapsuleMesh.new()
		capsule.radius = 0.35
		capsule.height = 1.7
		capsule.material = mat
		visual.mesh = capsule
		body.add_child(visual)

	var col := CollisionShape3D.new()
	var shape := CapsuleShape3D.new()
	shape.radius = 0.35
	shape.height = 1.7
	col.shape = shape
	# When the alive GLB has its origin at the feet, lift the capsule collider
	# up so it covers the standing body, not the floor below.
	if alive and glb_path_present:
		col.transform.origin = Vector3(0.0, 0.85, 0.0)
	body.add_child(col)
	holder.add_child(body)

	var label_text := label_prefix + display_name
	if role == "victim" and alive:
		# Edge case (shouldn't happen in current generator but be safe)
		label_text = "[VICTIM] " + display_name

	_attach_label(holder, label_text, label_height, color)
	return holder


func _attach_label(holder: Node3D, text: String, height: float, tint: Color) -> void:
	var label := Label3D.new()
	label.text = text
	label.font_size = LABEL_FONT_SIZE
	label.pixel_size = LABEL_PIXEL_SIZE
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.modulate = tint.lerp(Color(1, 1, 1), 0.4)
	label.outline_size = 8
	label.outline_modulate = Color(0, 0, 0)
	label.transform.origin = Vector3(0.0, height, 0.0)
	holder.add_child(label)


# ---------------------------------------------------------------------------
# Lighting
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Asset map (Kenney mesh loader)
# ---------------------------------------------------------------------------
#
# `game/config/asset_map.json` maps logical entity kinds (objects, characters)
# to .glb/.gltf paths under `game/assets/kenney/`. The room builder tries to
# instance the mapped scene; if the file isn't there, it falls back to a
# colored cube/capsule. This is the contract that lets the game ship without
# bundled assets and still gain rich visuals when the user runs
# `scripts/download_kenney_assets.sh`.

func _load_asset_map() -> void:
	if _asset_map_loaded:
		return
	_asset_map_loaded = true
	if not FileAccess.file_exists(ASSET_MAP_PATH):
		return
	var f := FileAccess.open(ASSET_MAP_PATH, FileAccess.READ)
	if f == null:
		return
	var raw := f.get_as_text()
	var parsed: Variant = JSON.parse_string(raw)
	if typeof(parsed) == TYPE_DICTIONARY:
		_asset_map = parsed


func _resolve_asset_full(category: String, entity_name: String, role: String) -> Dictionary:
	# Returns {path: String, scale: float}. Empty path means "no asset; use fallback".
	var result := {"path": "", "scale": 1.0}
	if _asset_map.is_empty():
		return result
	var bucket: Variant = _asset_map.get(category)
	if typeof(bucket) != TYPE_DICTIONARY:
		return result
	var asset_root: String = String(_asset_map.get("asset_root", "res://assets/kenney/"))
	var category_default_scale: float = float(bucket.get("default_scale", 1.0))
	result["scale"] = category_default_scale

	# 1. Role-specific override (characters -> by_role)
	if role != "" and bucket.has("by_role"):
		var by_role: Dictionary = bucket.get("by_role", {})
		if by_role.has(role):
			var entry: Variant = by_role[role]
			result["path"] = asset_root + String(_entry_asset(entry))
			result["scale"] = _entry_scale(entry, category_default_scale)
			return result

	# 2. Substring patterns (objects -> patterns)
	if bucket.has("patterns"):
		var patterns: Array = bucket.get("patterns", [])
		var lower_name := entity_name.to_lower()
		for p in patterns:
			var needle := String(p.get("contains", "")).to_lower()
			if needle != "" and needle in lower_name:
				result["path"] = asset_root + String(p.get("asset", ""))
				result["scale"] = _entry_scale(p, category_default_scale)
				return result

	# 3. Default for the category
	if bucket.has("default"):
		var entry2: Variant = bucket["default"]
		result["path"] = asset_root + String(_entry_asset(entry2))
		result["scale"] = _entry_scale(entry2, category_default_scale)

	return result


func _entry_asset(entry: Variant) -> String:
	if typeof(entry) == TYPE_DICTIONARY:
		return String(entry.get("asset", ""))
	return String(entry)


func _entry_scale(entry: Variant, default_scale: float) -> float:
	if typeof(entry) == TYPE_DICTIONARY and entry.has("scale"):
		return float(entry["scale"])
	return default_scale


func _try_instance_asset(path: String) -> Node3D:
	if path == "":
		return null
	if not ResourceLoader.exists(path):
		return null
	var res := ResourceLoader.load(path)
	if res == null:
		return null
	if res is PackedScene:
		var node: Node = (res as PackedScene).instantiate()
		if node is Node3D:
			return node
		else:
			node.queue_free()
	return null


func _try_load_sibling_texture(glb_path: String) -> Texture2D:
	# Kenney ships per-model PNGs alongside the .glb files. If the .glb's
	# embedded materials don't render correctly (Forward+ on Apple Silicon
	# with some GLBs strips them), explicitly load the sibling .png as the
	# albedo texture.
	if glb_path == "":
		return null
	var png_path := glb_path
	if png_path.ends_with(".glb"):
		png_path = png_path.substr(0, png_path.length() - 4) + ".png"
	elif png_path.ends_with(".gltf"):
		png_path = png_path.substr(0, png_path.length() - 5) + ".png"
	else:
		return null
	if not ResourceLoader.exists(png_path):
		return null
	var res := ResourceLoader.load(png_path)
	if res is Texture2D:
		return res as Texture2D
	return null


func _apply_external_texture(root: Node3D, tex: Texture2D) -> void:
	var stack: Array = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node is MeshInstance3D:
			var mi := node as MeshInstance3D
			var mesh := mi.mesh
			if mesh != null:
				for i in mesh.get_surface_count():
					var mat := StandardMaterial3D.new()
					mat.albedo_texture = tex
					mat.roughness = 0.75
					mat.metallic = 0.0
					mi.set_surface_override_material(i, mat)
		for child in node.get_children():
			stack.push_back(child)


func _apply_tint(root: Node3D, tint: Color) -> void:
	# Force a coloured StandardMaterial3D on every surface in the loaded GLB.
	# Kenney's embedded materials sometimes don't render correctly across
	# Godot 4 / Forward+ / GL Compatibility / Apple Silicon combinations,
	# leaving meshes solid white. Forcing a tinted material guarantees
	# visible, role-coded entities.
	var stack: Array = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node is MeshInstance3D:
			var mi := node as MeshInstance3D
			var mesh := mi.mesh
			if mesh != null:
				var n := mesh.get_surface_count()
				for i in n:
					var mat := StandardMaterial3D.new()
					mat.albedo_color = tint
					mat.roughness = 0.7
					mat.metallic = 0.0
					mi.set_surface_override_material(i, mat)
		for child in node.get_children():
			stack.push_back(child)


# ---------------------------------------------------------------------------
# Lighting
# ---------------------------------------------------------------------------

func _add_lighting(width: int, height: int, tile_m: float) -> void:
	var light := DirectionalLight3D.new()
	light.transform = Transform3D(
		Basis.from_euler(Vector3(deg_to_rad(-50.0), deg_to_rad(35.0), 0.0)),
		Vector3(width * tile_m * 0.5, 6.0, height * tile_m * 0.5)
	)
	light.light_energy = 0.9
	add_child(light)

	var env := WorldEnvironment.new()
	var e := Environment.new()
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.12, 0.12, 0.14)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.55, 0.55, 0.6)
	e.ambient_light_energy = 0.5
	env.environment = e
	add_child(env)
