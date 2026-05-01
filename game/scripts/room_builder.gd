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
	_add_decor(width, height, tile_m, room.get("doors", []))
	_add_door_triggers(room.get("doors", []), tile_m)
	_add_objects(room.get("objects", []), tile_m, width, height, room.get("doors", []))
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

		# Small destination tag right above the door arch so it's only
		# readable when the player is fairly close. Keeps the label from
		# dominating the view across the room. ASCII only (CLAUDE.md rule 9).
		var label := Label3D.new()
		label.text = leads_to_name
		label.font_size = 24
		label.pixel_size = 0.0025
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.modulate = Color(0.85, 0.75, 0.55, 0.85)
		label.outline_size = 4
		label.outline_modulate = Color(0, 0, 0)
		label.transform.origin = Vector3(
			dx * tile_m + tile_m * 0.5,
			WALL_HEIGHT * 0.85,
			dy * tile_m + tile_m * 0.5,
		)
		add_child(label)


func _on_door_body_entered(body: Node, leads_to: String) -> void:
	if body is CharacterBody3D:
		emit_signal("door_entered", leads_to)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

const WALL_MOUNT_PATTERNS := ["mirror", "curtain", "window", "painting", "frame", "ledge"]


func _is_wall_mounted(entity_name: String) -> bool:
	var lower := entity_name.to_lower()
	for p in WALL_MOUNT_PATTERNS:
		if p in lower:
			return true
	return false


func _add_objects(objects: Array, tile_m: float, width: int, height: int, doors: Array) -> void:
	# Split: anything wall-mountable (mirror, curtain, painting, window
	# ledge) gets stuck on a real wall instead of standing on the floor.
	var wall_objects: Array = []
	var floor_objects: Array = []
	for o in objects:
		if _is_wall_mounted(String(o.get("name", ""))):
			wall_objects.append(o)
		else:
			floor_objects.append(o)

	# Floor-placed props (everything else)
	for o in floor_objects:
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

	# Wall-mounted props
	var slots := _build_wall_slots(width, height, tile_m, doors, wall_objects.size())
	for i in wall_objects.size():
		var wo: Dictionary = wall_objects[i]
		if i >= slots.size():
			# Out of wall slots — fall back to floor placement
			var fallback_kind: String = String(wo.get("kind", "object"))
			var fallback_name: String = String(wo.get("name", "object"))
			var fallback_x: int = int(wo.get("x", 0))
			var fallback_y: int = int(wo.get("y", 0))
			var fb_holder := _spawn_prop_box(fallback_x, fallback_y, tile_m, OBJECT_COLOR, fallback_name, 0.55)
			fb_holder.set_meta("entity_kind", "object")
			fb_holder.set_meta("entity_name", fallback_name)
			fb_holder.set_meta("entity_id", String(wo.get("id", "")))
			fb_holder.set_meta("entity_subkind", fallback_kind)
			continue
		_spawn_wall_object(wo, slots[i])


func _build_wall_slots(width: int, height: int, tile_m: float, doors: Array, needed: int) -> Array:
	# Generate evenly-spaced positions along walls. Walls without doors are
	# filled first; door-walls are used only if the room has more wall
	# objects than empty walls can hold.
	var size_x: float = width * tile_m
	var size_z: float = height * tile_m
	var doors_walls: Array = []
	for d in doors:
		doors_walls.append(String(d.get("wall", "")))

	var fracs := [0.30, 0.70, 0.20, 0.80, 0.50]  # priority order along each wall
	var ordered_walls: Array = []
	# Walls without doors first
	for w in ["north", "south", "east", "west"]:
		if not w in doors_walls:
			ordered_walls.append(w)
	# Then walls with doors (skip the door's slot via fracs offset)
	for w in ["north", "south", "east", "west"]:
		if w in doors_walls:
			ordered_walls.append(w)

	var poke: float = 0.10
	var y: float = 1.40

	var slots: Array = []
	for w in ordered_walls:
		for f in fracs:
			# Skip door-blocking centre on door walls
			if w in doors_walls and absf(f - 0.5) < 0.18:
				continue
			var pos: Vector3
			var rot_y: float
			match w:
				"north":
					pos = Vector3(size_x * f, y, 1.0 + poke)
					rot_y = PI  # face +Z (south, into room)
				"south":
					pos = Vector3(size_x * f, y, size_z - 1.0 - poke)
					rot_y = 0.0  # face -Z (north, into room)
				"east":
					pos = Vector3(size_x - 1.0 - poke, y, size_z * f)
					rot_y = -PI * 0.5  # face -X (west, into room)
				"west":
					pos = Vector3(1.0 + poke, y, size_z * f)
					rot_y = PI * 0.5  # face +X (east, into room)
				_:
					continue
			slots.append({"pos": pos, "rot_y": rot_y, "wall": w})
			if slots.size() >= needed:
				return slots
	return slots


func _spawn_wall_object(o: Dictionary, slot: Dictionary) -> void:
	var entity_name: String = String(o.get("name", "object"))
	var lower := entity_name.to_lower()
	var color: Color = OBJECT_COLOR

	var holder := Node3D.new()
	holder.transform.origin = slot["pos"]
	holder.rotation.y = float(slot["rot_y"])
	add_child(holder)

	holder.set_meta("entity_kind", "object")
	holder.set_meta("entity_name", entity_name)
	holder.set_meta("entity_id", String(o.get("id", "")))
	holder.set_meta("entity_subkind", String(o.get("kind", "object")))

	# Pick a wall-friendly mesh. We build it locally so we can orient it
	# flat against the wall in the holder's local frame (the holder has
	# already been rotated to face into the room).
	var visual: Node3D
	if "curtain" in lower:
		visual = _build_wall_curtain()
	elif "mirror" in lower:
		visual = _build_wall_mirror()
	elif "painting" in lower or "frame" in lower:
		visual = _build_wall_painting()
	elif "window" in lower or "ledge" in lower:
		visual = _build_wall_window()
	else:
		visual = _build_wall_painting()  # generic catch-all
	holder.add_child(visual)

	# Collision body so the player can interact (raycast for E-key) and
	# can't walk into the wall mounting.
	var body := StaticBody3D.new()
	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(1.4, 1.0, 0.2)
	col.shape = shape
	body.add_child(col)
	holder.add_child(body)

	# Label drifts slightly out from the wall, in front of the visual.
	var label := Label3D.new()
	label.text = entity_name
	label.font_size = LABEL_FONT_SIZE
	label.pixel_size = LABEL_PIXEL_SIZE
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.modulate = color.lerp(Color(1, 1, 1), 0.4)
	label.outline_size = 8
	label.outline_modulate = Color(0, 0, 0)
	label.transform.origin = Vector3(0.0, 1.0, 0.20)
	holder.add_child(label)


func _build_wall_curtain() -> Node3D:
	var root := Node3D.new()
	var heavy := Color(0.40, 0.10, 0.15)
	var rod := Color(0.35, 0.25, 0.10)
	# Rod
	_add_block(root, Vector3(0.0, 1.20, 0.0), Vector3(1.60, 0.06, 0.06), rod)
	# Pleated panels (vertical strips poking slightly into the room)
	for i in 9:
		var x: float = -0.80 + i * 0.18
		var c: Color = heavy.darkened(0.05) if (i % 2 == 0) else heavy
		_add_block(root, Vector3(x, 0.10, 0.04), Vector3(0.16, 2.30, 0.06), c)
	return root


func _build_wall_mirror() -> Node3D:
	var root := Node3D.new()
	var frame := Color(0.65, 0.50, 0.20)
	var glass := Color(0.75, 0.85, 0.92)
	var glass_dark := Color(0.45, 0.55, 0.65)
	# Ornate frame
	_add_block(root, Vector3(0.0, 0.0, 0.0), Vector3(1.20, 1.60, 0.10), frame)
	# Mirror surface
	_add_block(root, Vector3(0.0, 0.0, 0.06), Vector3(1.00, 1.40, 0.02), glass)
	# Subtle vertical reflection band for visual interest
	_add_block(root, Vector3(-0.20, 0.0, 0.07), Vector3(0.10, 1.30, 0.005), glass_dark)
	return root


func _build_wall_painting() -> Node3D:
	var root := Node3D.new()
	var frame := Color(0.40, 0.30, 0.20)
	# Frame
	_add_block(root, Vector3(0.0, 0.0, 0.0), Vector3(1.30, 1.00, 0.08), frame)
	# Three colored bands inside the frame as a stylized painting
	var palette := [Color(0.18, 0.28, 0.45), Color(0.55, 0.45, 0.30), Color(0.85, 0.78, 0.55)]
	for i in palette.size():
		var c: Color = palette[i]
		var h: float = 0.80 / palette.size()
		_add_block(
			root,
			Vector3(0.0, -0.40 + h * 0.5 + i * h, 0.05),
			Vector3(1.10, h, 0.02),
			c,
		)
	return root


func _build_wall_window() -> Node3D:
	var root := Node3D.new()
	var frame := Color(0.40, 0.30, 0.20)
	var glass := Color(0.55, 0.70, 0.85)
	# Outer frame (a thick rectangle)
	_add_block(root, Vector3(0.0, 0.0, 0.0), Vector3(1.40, 1.40, 0.10), frame)
	# Glass
	_add_block(root, Vector3(0.0, 0.0, 0.06), Vector3(1.20, 1.20, 0.02), glass)
	# Cross mullions
	_add_block(root, Vector3(0.0, 0.0, 0.07), Vector3(1.20, 0.06, 0.03), frame)
	_add_block(root, Vector3(0.0, 0.0, 0.07), Vector3(0.06, 1.20, 0.03), frame)
	# Sill (narrow ledge sticking out at the bottom)
	_add_block(root, Vector3(0.0, -0.78, 0.10), Vector3(1.60, 0.10, 0.20), frame.darkened(0.1))
	return root


func _spawn_prop_box(tx: int, ty: int, tile_m: float, color: Color, label_text: String, size: float) -> Node3D:
	var holder := Node3D.new()
	holder.transform.origin = Vector3(
		tx * tile_m + tile_m * 0.5,
		0.0,
		ty * tile_m + tile_m * 0.5,
	)
	add_child(holder)

	# 1) Procedural shape for items that have no good Kenney equivalent
	#    (cleaver, revolver, candlestick, rope, vial, poker, etc.).
	# 2) Fall through to Kenney GLB lookup.
	# 3) Last resort: tinted cube.
	var procedural := _try_build_procedural_object(label_text)

	var body := StaticBody3D.new()
	body.transform.origin = Vector3(0.0, 0.0, 0.0)

	var visual_height: float = size * 1.2
	var col_size: float = size

	if not procedural.is_empty():
		body.add_child(procedural["node"] as Node3D)
		visual_height = float(procedural["height"])
		col_size = clampf(visual_height, 0.3, 1.2)
	else:
		var resolved := _resolve_asset_full("objects", label_text, "")
		var glb := _try_instance_asset(String(resolved.get("path", "")))
		if glb != null:
			var s: float = float(resolved.get("scale", 1.0))
			if not is_equal_approx(s, 1.0):
				glb.scale = Vector3(s, s, s)
			var asset_path := String(resolved.get("path", ""))
			var tex := _try_load_sibling_texture(asset_path)
			if tex != null:
				_apply_external_texture(glb, tex)
			else:
				_apply_tint(glb, color)
			body.add_child(glb)
			col_size = size * max(1.0, s)
			visual_height = col_size
		else:
			# Cube fallback (the body offset must lift the centred mesh).
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
			col_size = size
			visual_height = size

	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(col_size, col_size, col_size)
	col.shape = shape
	# When the visual sits on the floor (procedural / GLB), centre the
	# collider above y=0; the cube fallback already shifted the body.
	if not procedural.is_empty() or body.transform.origin == Vector3.ZERO:
		col.transform.origin = Vector3(0.0, col_size * 0.5, 0.0)
	body.add_child(col)
	holder.add_child(body)

	# Label sits just above the visual (visual_height was set per-asset).
	var label_y: float = visual_height + 0.30
	_attach_label(holder, label_text, label_y, color)
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
	# Procedural Minecraft-style stacked-blocks character.
	#
	# Why not Kenney GLBs? Repeated attempts to render Kenney's mini-character
	# meshes produced either solid white (when materials didn't load) or
	# solid green (when the sibling .png override mapped wrong UVs).
	# Building the figure from BoxMesh primitives gives full control: the
	# colours we set are the colours that render. No import quirks.
	var holder := Node3D.new()
	holder.transform.origin = Vector3(
		tx * tile_m + tile_m * 0.5,
		0.0,
		ty * tile_m + tile_m * 0.5,
	)
	add_child(holder)

	# Rig: holder -> rig (rotates 90° if dead) -> body parts.
	# Label and collision live on the holder so they stay upright/correct
	# regardless of the rig's orientation.
	var rig := Node3D.new()
	holder.add_child(rig)
	if not alive:
		rig.rotation = Vector3(0.0, 0.0, deg_to_rad(90.0))

	# Per-character variation, deterministic from the display name so the
	# same NPC always looks the same across reloads.
	var seed_int := int(_string_hash(display_name))
	var skin_color := _skin_palette(seed_int)
	var hair_color := _hair_palette(seed_int >> 2)

	# Use the role colour for the shirt so the player can quickly tell
	# suspect-from-innocent at a glance, without making the whole body
	# flat-coloured. Pants stay dark.
	var shirt_color := color
	if not alive or role == "victim":
		shirt_color = Color(0.42, 0.36, 0.30)  # neutral darker tone for the body
		skin_color = skin_color.darkened(0.25)
	var pants_color := Color(0.22, 0.20, 0.17)

	# Dimensions in metres at SCALE=1; total figure ~2.15m -> SCALE 0.85
	# yields ~1.83m, a believable human height.
	var HEAD: float = 0.55
	var TORSO_W: float = 0.70
	var TORSO_H: float = 0.85
	var TORSO_D: float = 0.40
	var ARM_W: float = 0.20
	var ARM_H: float = 0.95
	var ARM_D: float = 0.20
	var LEG_W: float = 0.28
	var LEG_H: float = 0.75
	var LEG_D: float = 0.28
	var SCALE: float = 0.85

	rig.scale = Vector3(SCALE, SCALE, SCALE)

	# Heights relative to the feet (y=0 in rig-local space).
	var leg_top: float = LEG_H
	var torso_top: float = leg_top + TORSO_H
	var head_top: float = torso_top + HEAD

	# Legs
	_add_block(rig, Vector3(-LEG_W * 0.55, LEG_H * 0.5, 0.0), Vector3(LEG_W, LEG_H, LEG_D), pants_color)
	_add_block(rig, Vector3( LEG_W * 0.55, LEG_H * 0.5, 0.0), Vector3(LEG_W, LEG_H, LEG_D), pants_color)

	# Torso (shirt)
	_add_block(rig, Vector3(0.0, leg_top + TORSO_H * 0.5, 0.0), Vector3(TORSO_W, TORSO_H, TORSO_D), shirt_color)

	# Arms (sleeve = shirt color, hanging straight down from shoulders)
	var arm_y_center: float = torso_top - ARM_H * 0.5
	var shoulder_x: float = TORSO_W * 0.5 + ARM_W * 0.5
	_add_block(rig, Vector3(-shoulder_x, arm_y_center, 0.0), Vector3(ARM_W, ARM_H, ARM_D), shirt_color)
	_add_block(rig, Vector3( shoulder_x, arm_y_center, 0.0), Vector3(ARM_W, ARM_H, ARM_D), shirt_color)

	# Head (skin)
	_add_block(rig, Vector3(0.0, torso_top + HEAD * 0.5, 0.0), Vector3(HEAD, HEAD, HEAD), skin_color)

	# Hair: a thin slab on top of the head
	var hair_h: float = 0.10
	_add_block(rig, Vector3(0.0, head_top + hair_h * 0.5, 0.0), Vector3(HEAD * 1.02, hair_h, HEAD * 1.02), hair_color)

	# Face: eyebrows + eyes + mouth, embedded just outside the head's front
	# face (along +Z) so they read as a face rather than free-floating dots.
	var dark := Color(0.08, 0.06, 0.05)
	var face_z: float = HEAD * 0.5 + 0.005
	var eye_y: float = torso_top + HEAD * 0.62
	var eye_white := Color(0.95, 0.92, 0.88)

	# Eye whites (bigger blocks) with dark pupils on top of them
	var eye_white_size := Vector3(0.13, 0.10, 0.02)
	var pupil_size := Vector3(0.06, 0.06, 0.025)
	_add_block(rig, Vector3(-0.13, eye_y, face_z), eye_white_size, eye_white)
	_add_block(rig, Vector3( 0.13, eye_y, face_z), eye_white_size, eye_white)
	_add_block(rig, Vector3(-0.13, eye_y, face_z + 0.01), pupil_size, dark)
	_add_block(rig, Vector3( 0.13, eye_y, face_z + 0.01), pupil_size, dark)

	# Eyebrows above the eyes
	var brow_size := Vector3(0.16, 0.04, 0.02)
	var brow_y: float = eye_y + 0.10
	_add_block(rig, Vector3(-0.13, brow_y, face_z), brow_size, hair_color)
	_add_block(rig, Vector3( 0.13, brow_y, face_z), brow_size, hair_color)

	# Mouth (a horizontal slit)
	var mouth_size := Vector3(0.18, 0.04, 0.02)
	var mouth_y: float = eye_y - 0.18
	if not alive:
		# Open-mouth-looking small square for the deceased
		mouth_size = Vector3(0.12, 0.08, 0.02)
	_add_block(rig, Vector3(0.0, mouth_y, face_z), mouth_size, dark)

	# Blood pool under the victim — a flat irregular red splash on the floor.
	if not alive:
		_add_blood_pool(holder)

	# Collision: a single capsule covering the whole figure.
	var col_body := StaticBody3D.new()
	var col := CollisionShape3D.new()
	var shape := CapsuleShape3D.new()
	var total_height: float = head_top * SCALE  # approximate
	shape.radius = max(TORSO_W, TORSO_D) * 0.5 * SCALE
	shape.height = total_height
	col.shape = shape
	if alive:
		col.transform.origin = Vector3(0.0, total_height * 0.5, 0.0)
	else:
		# Body is rotated to lie flat; a small fixed-Y capsule covers it.
		col.transform.origin = Vector3(0.0, shape.radius, 0.0)
		col.rotation = Vector3(0.0, 0.0, deg_to_rad(90.0))
	col_body.add_child(col)
	holder.add_child(col_body)

	# Pass the rig back via the function's return so the caller can attach
	# metadata. The caller already sets meta on the holder; we don't need to.
	# The label is added below by the original logic.
	var label_prefix := ""
	var label_height: float
	if alive:
		label_height = head_top * SCALE + 0.5
	else:
		label_prefix = "[BODY] "
		# Body lies along X with height = TORSO_D * SCALE; label just above it.
		label_height = TORSO_D * SCALE + 0.6

	var label_text := label_prefix + display_name
	if role == "victim" and alive:
		# Edge case (shouldn't happen in current generator but be safe)
		label_text = "[VICTIM] " + display_name

	_attach_label(holder, label_text, label_height, color)
	return holder


func _try_build_procedural_object(entity_name: String) -> Dictionary:
	# Returns {"node": Node3D, "height": float} for items that should use a
	# procedural shape (no good Kenney equivalent or visual is too distinct
	# to fake). Returns {} if no match — caller falls through to Kenney GLB.
	var lower := entity_name.to_lower()
	if "cleaver" in lower or "knife" in lower or "letter opener" in lower:
		return {"node": _build_cleaver(), "height": 0.45}
	if "shears" in lower:
		return {"node": _build_shears(), "height": 0.70}
	if "revolver" in lower or "pistol" in lower:
		return {"node": _build_revolver(), "height": 0.45}
	if "candlestick" in lower:
		return {"node": _build_candlestick(), "height": 1.35}
	if "decanter" in lower:
		return {"node": _build_decanter(), "height": 0.75}
	if "vial" in lower or "poison" in lower:
		return {"node": _build_vial(), "height": 0.45}
	if "rope" in lower:
		return {"node": _build_rope(), "height": 0.45}
	if "poker" in lower or ("iron" in lower and "fireplace" in lower):
		return {"node": _build_poker(), "height": 1.20}
	if "bookend" in lower:
		return {"node": _build_bookend(), "height": 0.70}
	if "scarf" in lower:
		return {"node": _build_scarf(), "height": 0.15}
	if "statuette" in lower:
		return {"node": _build_statuette(), "height": 0.65}
	if "fireplace" in lower or "mantel" in lower:
		return {"node": _build_fireplace(), "height": 1.80}
	if "curtain" in lower:
		return {"node": _build_curtain(), "height": 2.40}
	if "boots" in lower:
		return {"node": _build_boots(), "height": 0.30}
	if "umbrella" in lower:
		return {"node": _build_umbrella(), "height": 1.00}
	if "storage trunk" in lower or "trunk" in lower:
		return {"node": _build_trunk(), "height": 0.55}
	if "coat rack" in lower or "hat stand" in lower or "umbrella stand" in lower:
		return {"node": _build_coatrack(), "height": 1.80}
	if "radiator" in lower:
		return {"node": _build_radiator(), "height": 0.80}
	if "shelf of books" in lower or "bookcase" in lower or "bookshelf" in lower:
		return {"node": _build_bookcase(), "height": 1.85}
	if "envelope" in lower or "letter" in lower or "ticket" in lower or "receipt" in lower or "fingerprint" in lower:
		return {"node": _build_paper(), "height": 0.10}
	if "diary" in lower:
		return {"node": _build_book(), "height": 0.20}
	if "watch" in lower:
		return {"node": _build_watch(), "height": 0.20}
	if "key" in lower:
		return {"node": _build_keyring(), "height": 0.15}
	if "spectacles" in lower:
		return {"node": _build_spectacles(), "height": 0.15}
	if "glove" in lower:
		return {"node": _build_glove(), "height": 0.15}
	if "cigar" in lower:
		return {"node": _build_cigar(), "height": 0.15}
	if "ink" in lower and ("bottle" in lower or "spilled" in lower):
		return {"node": _build_inkbottle(), "height": 0.20}
	if "chess" in lower:
		return {"node": _build_chessboard(), "height": 0.30}
	return {}


func _build_cleaver() -> Node3D:
	var root := Node3D.new()
	var wood := Color(0.45, 0.30, 0.18)
	var blade := Color(0.85, 0.85, 0.90)
	# Handle (lying horizontally on the ground)
	_add_block(root, Vector3(-0.18, 0.06, 0.0), Vector3(0.32, 0.06, 0.06), wood)
	# Wide rectangular blade
	_add_block(root, Vector3(0.12, 0.18, 0.0), Vector3(0.40, 0.26, 0.02), blade)
	# Edge highlight (bottom of blade)
	_add_block(root, Vector3(0.12, 0.04, 0.0), Vector3(0.42, 0.03, 0.02), Color(0.95, 0.95, 0.98))
	return root


func _build_shears() -> Node3D:
	var root := Node3D.new()
	var metal := Color(0.65, 0.65, 0.70)
	var wood := Color(0.45, 0.30, 0.18)
	# Two slightly-open blades
	_add_block(root, Vector3(0.0, 0.40, 0.05), Vector3(0.06, 0.40, 0.04), metal)
	_add_block(root, Vector3(0.0, 0.40, -0.05), Vector3(0.06, 0.40, 0.04), metal)
	# Pivot
	_add_block(root, Vector3(0.0, 0.20, 0.0), Vector3(0.10, 0.06, 0.10), metal)
	# Handle loops
	_add_block(root, Vector3(0.0, 0.10, 0.12), Vector3(0.06, 0.20, 0.05), wood)
	_add_block(root, Vector3(0.0, 0.10, -0.12), Vector3(0.06, 0.20, 0.05), wood)
	return root


func _build_revolver() -> Node3D:
	var root := Node3D.new()
	var metal := Color(0.28, 0.28, 0.32)
	var wood := Color(0.45, 0.30, 0.18)
	# Barrel
	_add_block(root, Vector3(0.16, 0.30, 0.0), Vector3(0.32, 0.06, 0.06), metal)
	# Cylinder
	_add_block(root, Vector3(0.0, 0.30, 0.0), Vector3(0.10, 0.12, 0.12), metal)
	# Handle (angled grip)
	_add_block(root, Vector3(-0.10, 0.18, 0.0), Vector3(0.06, 0.22, 0.05), wood)
	# Trigger guard
	_add_block(root, Vector3(-0.04, 0.22, 0.0), Vector3(0.06, 0.04, 0.06), metal)
	return root


func _build_candlestick() -> Node3D:
	var root := Node3D.new()
	var brass := Color(0.85, 0.65, 0.20)
	var wax := Color(0.95, 0.92, 0.85)
	var flame := Color(1.0, 0.75, 0.20)
	_add_block(root, Vector3(0.0, 0.025, 0.0), Vector3(0.20, 0.05, 0.20), brass)
	_add_block(root, Vector3(0.0, 0.45, 0.0), Vector3(0.06, 0.80, 0.06), brass)
	_add_block(root, Vector3(0.0, 0.90, 0.0), Vector3(0.16, 0.06, 0.16), brass)
	_add_block(root, Vector3(0.0, 1.05, 0.0), Vector3(0.06, 0.20, 0.06), wax)
	_add_block(root, Vector3(0.0, 1.20, 0.0), Vector3(0.05, 0.10, 0.05), flame)
	return root


func _build_decanter() -> Node3D:
	var root := Node3D.new()
	var glass := Color(0.85, 0.92, 0.95)
	var wine := Color(0.45, 0.10, 0.18)
	_add_block(root, Vector3(0.0, 0.20, 0.0), Vector3(0.22, 0.40, 0.22), glass)
	_add_block(root, Vector3(0.0, 0.18, 0.0), Vector3(0.18, 0.30, 0.18), wine)
	# Neck
	_add_block(root, Vector3(0.0, 0.50, 0.0), Vector3(0.08, 0.20, 0.08), glass)
	# Stopper
	_add_block(root, Vector3(0.0, 0.62, 0.0), Vector3(0.10, 0.05, 0.10), Color(0.75, 0.55, 0.30))
	return root


func _build_vial() -> Node3D:
	var root := Node3D.new()
	var glass := Color(0.85, 0.92, 0.95)
	var poison := Color(0.20, 0.65, 0.20)
	_add_block(root, Vector3(0.0, 0.18, 0.0), Vector3(0.10, 0.36, 0.10), glass)
	_add_block(root, Vector3(0.0, 0.14, 0.0), Vector3(0.07, 0.22, 0.07), poison)
	# Cork
	_add_block(root, Vector3(0.0, 0.38, 0.0), Vector3(0.08, 0.06, 0.08), Color(0.55, 0.42, 0.25))
	return root


func _build_rope() -> Node3D:
	var root := Node3D.new()
	var rope := Color(0.55, 0.40, 0.22)
	# Coiled rope (overlapping flat rings)
	for i in 5:
		var y: float = 0.04 + i * 0.07
		_add_block(root, Vector3(0.0, y, 0.0), Vector3(0.36, 0.07, 0.36), rope)
	# Hollow centre is implicit (we don't poke through it; it's "minecraft" enough)
	return root


func _build_poker() -> Node3D:
	var root := Node3D.new()
	var iron := Color(0.20, 0.20, 0.22)
	var brass := Color(0.85, 0.65, 0.20)
	# Long thin rod standing up
	_add_block(root, Vector3(0.0, 0.50, 0.0), Vector3(0.04, 1.00, 0.04), iron)
	# Brass handle cap
	_add_block(root, Vector3(0.0, 1.05, 0.0), Vector3(0.06, 0.10, 0.06), brass)
	# Hooked tip at the base (bent)
	_add_block(root, Vector3(0.05, 0.04, 0.0), Vector3(0.12, 0.04, 0.04), iron)
	return root


func _build_bookend() -> Node3D:
	var root := Node3D.new()
	var stone := Color(0.85, 0.85, 0.82)
	var leather := Color(0.45, 0.20, 0.15)
	var pages := Color(0.92, 0.88, 0.78)
	# Marble base (the bookend itself)
	_add_block(root, Vector3(0.0, 0.10, 0.0), Vector3(0.40, 0.20, 0.16), stone)
	# Vertical brace
	_add_block(root, Vector3(-0.18, 0.30, 0.0), Vector3(0.04, 0.30, 0.16), stone)
	# Two leaning books
	_add_block(root, Vector3(-0.10, 0.30, 0.0), Vector3(0.06, 0.30, 0.14), leather)
	_add_block(root, Vector3(-0.04, 0.30, 0.0), Vector3(0.06, 0.28, 0.14), Color(0.20, 0.30, 0.55))
	_add_block(root, Vector3( 0.04, 0.28, 0.0), Vector3(0.06, 0.26, 0.14), pages)
	return root


func _build_scarf() -> Node3D:
	var root := Node3D.new()
	var silk := Color(0.30, 0.20, 0.55)
	# A long crumpled cloth on the floor
	_add_block(root, Vector3(0.0, 0.04, 0.0), Vector3(0.80, 0.04, 0.30), silk)
	_add_block(root, Vector3(-0.30, 0.06, 0.10), Vector3(0.20, 0.06, 0.20), silk)
	return root


func _build_statuette() -> Node3D:
	var root := Node3D.new()
	var bronze := Color(0.55, 0.35, 0.15)
	_add_block(root, Vector3(0.0, 0.05, 0.0), Vector3(0.20, 0.10, 0.20), bronze.darkened(0.2))
	_add_block(root, Vector3(0.0, 0.30, 0.0), Vector3(0.10, 0.30, 0.10), bronze)
	_add_block(root, Vector3(0.0, 0.52, 0.0), Vector3(0.10, 0.10, 0.10), bronze)
	return root


func _build_fireplace() -> Node3D:
	var root := Node3D.new()
	var stone := Color(0.55, 0.50, 0.45)
	var dark := Color(0.18, 0.16, 0.15)
	var fire := Color(1.0, 0.55, 0.15)
	var ember := Color(0.85, 0.25, 0.10)
	# Outer mantel surround (taller than wide)
	_add_block(root, Vector3(-0.65, 0.70, 0.0), Vector3(0.16, 1.40, 0.40), stone)
	_add_block(root, Vector3( 0.65, 0.70, 0.0), Vector3(0.16, 1.40, 0.40), stone)
	# Top mantel slab
	_add_block(root, Vector3(0.0, 1.45, 0.0), Vector3(1.60, 0.18, 0.45), stone)
	# Inner firebox (dark)
	_add_block(root, Vector3(0.0, 0.65, -0.05), Vector3(1.10, 1.20, 0.30), dark)
	# Logs and flame
	_add_block(root, Vector3(-0.20, 0.18, 0.0), Vector3(0.50, 0.10, 0.10), Color(0.30, 0.18, 0.10))
	_add_block(root, Vector3( 0.20, 0.18, 0.0), Vector3(0.50, 0.10, 0.10), Color(0.25, 0.15, 0.08))
	_add_block(root, Vector3(0.0, 0.35, 0.0), Vector3(0.50, 0.30, 0.20), fire)
	_add_block(root, Vector3(0.0, 0.55, 0.0), Vector3(0.30, 0.20, 0.15), ember)
	return root


func _build_curtain() -> Node3D:
	var root := Node3D.new()
	var heavy := Color(0.40, 0.10, 0.15)
	var rod := Color(0.35, 0.25, 0.10)
	# Curtain rod
	_add_block(root, Vector3(0.0, 2.30, 0.0), Vector3(1.40, 0.06, 0.06), rod)
	# Two heavy panels with vertical pleats (slightly different shades)
	for i in 7:
		var x: float = -0.65 + i * 0.20
		var c := heavy.darkened(0.05) if (i % 2 == 0) else heavy
		_add_block(root, Vector3(x, 1.15, 0.0), Vector3(0.18, 2.20, 0.06), c)
	return root


func _build_boots() -> Node3D:
	var root := Node3D.new()
	var leather := Color(0.30, 0.20, 0.12)
	var sole := Color(0.10, 0.08, 0.06)
	# Two boots side by side
	for offset in [Vector3(-0.10, 0, 0), Vector3(0.10, 0, 0)]:
		_add_block(root, offset + Vector3(0.0, 0.03, 0.0), Vector3(0.10, 0.06, 0.22), sole)
		_add_block(root, offset + Vector3(0.0, 0.13, 0.0), Vector3(0.10, 0.14, 0.18), leather)
		_add_block(root, offset + Vector3(0.0, 0.13, -0.06), Vector3(0.10, 0.14, 0.06), leather.darkened(0.1))
	return root


func _build_paper() -> Node3D:
	var root := Node3D.new()
	# Crumpled sheet on the floor
	_add_block(root, Vector3(0.0, 0.01, 0.0), Vector3(0.22, 0.02, 0.30), Color(0.92, 0.88, 0.78))
	_add_block(root, Vector3(0.06, 0.04, 0.05), Vector3(0.10, 0.04, 0.10), Color(0.85, 0.80, 0.70))
	return root


func _build_book() -> Node3D:
	var root := Node3D.new()
	_add_block(root, Vector3(0.0, 0.05, 0.0), Vector3(0.22, 0.10, 0.16), Color(0.30, 0.15, 0.12))
	_add_block(root, Vector3(0.0, 0.08, 0.0), Vector3(0.18, 0.04, 0.14), Color(0.92, 0.88, 0.78))
	_add_block(root, Vector3(-0.08, 0.10, 0.0), Vector3(0.02, 0.10, 0.16), Color(0.75, 0.40, 0.20))
	return root


func _build_watch() -> Node3D:
	var root := Node3D.new()
	# Small disc with a chain
	_add_block(root, Vector3(0.0, 0.05, 0.0), Vector3(0.10, 0.04, 0.10), Color(0.85, 0.65, 0.20))
	# Inner face
	_add_block(root, Vector3(0.0, 0.07, 0.0), Vector3(0.07, 0.02, 0.07), Color(0.95, 0.92, 0.88))
	# Hands
	_add_block(root, Vector3(0.0, 0.085, 0.0), Vector3(0.05, 0.005, 0.005), Color.BLACK)
	_add_block(root, Vector3(0.0, 0.085, 0.01), Vector3(0.005, 0.005, 0.04), Color.BLACK)
	# Chain (a couple links)
	for i in 4:
		_add_block(root, Vector3(0.07 + i * 0.02, 0.05, 0.0), Vector3(0.018, 0.018, 0.018), Color(0.85, 0.65, 0.20))
	return root


func _build_keyring() -> Node3D:
	var root := Node3D.new()
	var brass := Color(0.85, 0.65, 0.20)
	# Ring base
	for i in 6:
		var ang: float = i * TAU / 6.0
		_add_block(root, Vector3(cos(ang) * 0.06, 0.02, sin(ang) * 0.06), Vector3(0.04, 0.02, 0.04), brass)
	# Two keys hanging
	_add_block(root, Vector3(0.10, 0.04, 0.02), Vector3(0.02, 0.05, 0.07), brass)
	_add_block(root, Vector3(0.10, 0.04, -0.02), Vector3(0.02, 0.05, 0.07), brass)
	return root


func _build_spectacles() -> Node3D:
	var root := Node3D.new()
	var frame := Color(0.30, 0.20, 0.10)
	# Two lenses
	_add_block(root, Vector3(-0.06, 0.05, 0.0), Vector3(0.08, 0.08, 0.01), frame)
	_add_block(root, Vector3( 0.06, 0.05, 0.0), Vector3(0.08, 0.08, 0.01), frame)
	# Bridge
	_add_block(root, Vector3(0.0, 0.05, 0.0), Vector3(0.04, 0.02, 0.01), frame)
	# Earpieces
	_add_block(root, Vector3(-0.10, 0.05, 0.04), Vector3(0.02, 0.02, 0.08), frame)
	_add_block(root, Vector3( 0.10, 0.05, 0.04), Vector3(0.02, 0.02, 0.08), frame)
	return root


func _build_glove() -> Node3D:
	var root := Node3D.new()
	var cloth := Color(0.10, 0.08, 0.07)
	var blood := Color(0.55, 0.05, 0.05)
	# Palm
	_add_block(root, Vector3(0.0, 0.03, 0.0), Vector3(0.10, 0.04, 0.16), cloth)
	# Fingers (four small)
	for i in 4:
		var x: float = -0.04 + i * 0.025
		_add_block(root, Vector3(x, 0.04, 0.10), Vector3(0.02, 0.04, 0.06), cloth)
	# Bloodstain on top
	_add_block(root, Vector3(0.0, 0.06, 0.04), Vector3(0.06, 0.005, 0.05), blood)
	return root


func _build_cigar() -> Node3D:
	var root := Node3D.new()
	var ash_tray := Color(0.20, 0.20, 0.22)
	var cigar := Color(0.45, 0.30, 0.18)
	var ash := Color(0.60, 0.55, 0.50)
	# Ashtray dish
	_add_block(root, Vector3(0.0, 0.02, 0.0), Vector3(0.20, 0.04, 0.20), ash_tray)
	# Stub
	_add_block(root, Vector3(0.04, 0.05, 0.0), Vector3(0.10, 0.03, 0.03), cigar)
	# Ash on the end
	_add_block(root, Vector3(-0.02, 0.05, 0.0), Vector3(0.03, 0.02, 0.03), ash)
	return root


func _build_inkbottle() -> Node3D:
	var root := Node3D.new()
	var glass := Color(0.20, 0.18, 0.30)
	var ink := Color(0.05, 0.05, 0.20)
	# Bottle
	_add_block(root, Vector3(0.0, 0.06, 0.0), Vector3(0.10, 0.12, 0.10), glass)
	# Spilled puddle
	_add_block(root, Vector3(0.20, 0.005, 0.05), Vector3(0.30, 0.005, 0.20), ink)
	_add_block(root, Vector3(0.30, 0.006, -0.05), Vector3(0.18, 0.005, 0.10), ink)
	return root


func _build_chessboard() -> Node3D:
	var root := Node3D.new()
	var dark := Color(0.20, 0.15, 0.10)
	var light := Color(0.85, 0.78, 0.65)
	# Board base
	_add_block(root, Vector3(0.0, 0.02, 0.0), Vector3(0.40, 0.04, 0.40), dark)
	# A few alternating squares (suggest chequerboard)
	for i in 4:
		for j in 4:
			if (i + j) % 2 == 0:
				_add_block(
					root,
					Vector3(-0.15 + i * 0.10, 0.04, -0.15 + j * 0.10),
					Vector3(0.09, 0.005, 0.09),
					light,
				)
	# A piece or two
	_add_block(root, Vector3(-0.10, 0.10, -0.10), Vector3(0.05, 0.10, 0.05), light)
	_add_block(root, Vector3( 0.10, 0.10,  0.10), Vector3(0.05, 0.10, 0.05), dark)
	return root


func _build_trunk() -> Node3D:
	var root := Node3D.new()
	var wood := Color(0.36, 0.22, 0.14)
	var iron := Color(0.18, 0.16, 0.16)
	var lid_wood := Color(0.30, 0.18, 0.12)
	# Body
	_add_block(root, Vector3(0.0, 0.20, 0.0), Vector3(0.80, 0.40, 0.45), wood)
	# Lid
	_add_block(root, Vector3(0.0, 0.45, 0.0), Vector3(0.82, 0.10, 0.47), lid_wood)
	# Iron bands wrapping around
	_add_block(root, Vector3(0.0, 0.20, 0.235), Vector3(0.84, 0.42, 0.02), iron)
	_add_block(root, Vector3(0.0, 0.20, -0.235), Vector3(0.84, 0.42, 0.02), iron)
	_add_block(root, Vector3(0.41, 0.20, 0.0), Vector3(0.02, 0.42, 0.46), iron)
	_add_block(root, Vector3(-0.41, 0.20, 0.0), Vector3(0.02, 0.42, 0.46), iron)
	# Lock plate on front
	_add_block(root, Vector3(0.0, 0.40, 0.236), Vector3(0.10, 0.10, 0.02), iron)
	return root


func _build_coatrack() -> Node3D:
	var root := Node3D.new()
	var wood := Color(0.30, 0.20, 0.12)
	var brass := Color(0.85, 0.65, 0.20)
	# Base disc (square slab)
	_add_block(root, Vector3(0.0, 0.04, 0.0), Vector3(0.45, 0.08, 0.45), wood)
	# Pole
	_add_block(root, Vector3(0.0, 0.90, 0.0), Vector3(0.06, 1.70, 0.06), wood)
	# Hooks (4 around top)
	for ang in [0.0, PI * 0.5, PI, PI * 1.5]:
		var x: float = cos(ang) * 0.10
		var z: float = sin(ang) * 0.10
		_add_block(root, Vector3(x, 1.60, z), Vector3(0.06, 0.04, 0.06), brass)
		_add_block(root, Vector3(x * 1.6, 1.50, z * 1.6), Vector3(0.04, 0.10, 0.04), brass)
	# A draped coat on one side (visual variety)
	_add_block(root, Vector3(0.18, 1.00, 0.0), Vector3(0.04, 0.80, 0.30), Color(0.18, 0.20, 0.30))
	return root


func _build_radiator() -> Node3D:
	var root := Node3D.new()
	var iron := Color(0.85, 0.84, 0.82)
	# 6 vertical fins
	for i in 6:
		var x: float = -0.40 + i * 0.16
		_add_block(root, Vector3(x, 0.40, 0.0), Vector3(0.10, 0.78, 0.18), iron)
	# Top rail
	_add_block(root, Vector3(0.0, 0.80, 0.0), Vector3(1.00, 0.05, 0.20), iron.darkened(0.1))
	# Bottom rail
	_add_block(root, Vector3(0.0, 0.04, 0.0), Vector3(1.00, 0.06, 0.20), iron.darkened(0.1))
	# Valve knob
	_add_block(root, Vector3(0.55, 0.20, 0.0), Vector3(0.04, 0.06, 0.04), Color(0.45, 0.30, 0.18))
	return root


func _build_bookcase() -> Node3D:
	var root := Node3D.new()
	var wood := Color(0.30, 0.18, 0.10)
	var pages := Color(0.92, 0.88, 0.78)
	# Frame: side panels + back + shelves
	_add_block(root, Vector3(-0.45, 0.92, 0.0), Vector3(0.06, 1.84, 0.36), wood)
	_add_block(root, Vector3( 0.45, 0.92, 0.0), Vector3(0.06, 1.84, 0.36), wood)
	_add_block(root, Vector3(0.0, 0.04, 0.0), Vector3(0.96, 0.08, 0.36), wood)
	_add_block(root, Vector3(0.0, 1.82, 0.0), Vector3(0.96, 0.08, 0.36), wood)
	_add_block(root, Vector3(0.0, 0.92, -0.16), Vector3(0.96, 1.84, 0.04), wood.darkened(0.15))
	# Three shelves of books (rows of vertical thin blocks)
	var shelf_ys := [0.55, 1.05, 1.55]
	var book_colors := [
		Color(0.50, 0.20, 0.18), Color(0.20, 0.30, 0.55), Color(0.55, 0.40, 0.20),
		Color(0.30, 0.45, 0.30), Color(0.40, 0.20, 0.40), Color(0.55, 0.55, 0.20),
		pages,
	]
	for sy in shelf_ys:
		# Shelf board
		_add_block(root, Vector3(0.0, sy - 0.10, 0.0), Vector3(0.92, 0.04, 0.32), wood)
		# Books standing on top of the shelf
		for i in 9:
			var x: float = -0.40 + i * 0.10
			var c: Color = book_colors[(i + int(sy * 7)) % book_colors.size()]
			_add_block(root, Vector3(x, sy + 0.18, 0.0), Vector3(0.08, 0.36, 0.20), c)
	return root


func _build_umbrella() -> Node3D:
	var root := Node3D.new()
	var fabric := Color(0.15, 0.20, 0.30)
	var handle := Color(0.30, 0.20, 0.10)
	# Shaft
	_add_block(root, Vector3(0.0, 0.45, 0.0), Vector3(0.04, 0.90, 0.04), handle)
	# Handle hook
	_add_block(root, Vector3(0.06, 0.05, 0.0), Vector3(0.10, 0.04, 0.04), handle)
	# Closed canopy (vertical fabric folds)
	for i in 4:
		var ang: float = i * TAU / 4.0
		_add_block(root, Vector3(cos(ang) * 0.04, 0.65, sin(ang) * 0.04), Vector3(0.05, 0.50, 0.05), fabric)
	return root


func _add_blood_pool(parent: Node3D) -> void:
	# Stack a few overlapping flat planes to suggest a splash shape rather
	# than a single sterile rectangle. Slightly above floor (y=0.005) to
	# avoid z-fighting with the room floor mesh.
	var blood_color := Color(0.55, 0.05, 0.05)
	var blood_dark := Color(0.35, 0.03, 0.03)
	# Sit just above the rug (y ≈ 0.014) so the splash isn't hidden under it.
	var slabs := [
		{"pos": Vector3(0.0, 0.020, 0.0),  "size": Vector3(2.4, 0.01, 1.4), "c": blood_color},
		{"pos": Vector3(0.7, 0.022, 0.4),  "size": Vector3(1.0, 0.01, 0.6), "c": blood_color},
		{"pos": Vector3(-0.6, 0.022, -0.5),"size": Vector3(0.9, 0.01, 0.7), "c": blood_color},
		{"pos": Vector3(0.0, 0.024, 0.0),  "size": Vector3(1.2, 0.01, 0.7), "c": blood_dark},
	]
	for s in slabs:
		_add_block(parent, s["pos"], s["size"], s["c"])


func _add_block(parent: Node3D, pos: Vector3, size: Vector3, color: Color) -> void:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.85
	mat.metallic = 0.0
	var box := BoxMesh.new()
	box.size = size
	var mi := MeshInstance3D.new()
	mi.mesh = box
	mi.transform.origin = pos
	# Attach the material as the surface override so each block can vary its
	# colour without sharing material state.
	mi.set_surface_override_material(0, mat)
	parent.add_child(mi)


func _string_hash(s: String) -> int:
	var h: int = 5381
	for i in s.length():
		h = ((h << 5) + h) + int(s.unicode_at(i))
		h = h & 0x7FFFFFFF
	return h


func _skin_palette(idx: int) -> Color:
	var palette := [
		Color(1.00, 0.86, 0.74),  # pale
		Color(0.95, 0.78, 0.65),  # light
		Color(0.85, 0.65, 0.50),  # tan
		Color(0.62, 0.45, 0.35),  # medium-dark
		Color(0.40, 0.28, 0.22),  # dark
	]
	return palette[abs(idx) % palette.size()]


func _hair_palette(idx: int) -> Color:
	var palette := [
		Color(0.10, 0.08, 0.07),  # black
		Color(0.30, 0.20, 0.13),  # brown
		Color(0.85, 0.70, 0.45),  # blond
		Color(0.60, 0.30, 0.20),  # auburn
		Color(0.55, 0.55, 0.55),  # grey
		Color(0.20, 0.18, 0.16),  # dark brown
	]
	return palette[abs(idx) % palette.size()]


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

func _add_decor(width: int, height: int, tile_m: float, doors: Array) -> void:
	# Pure-cosmetic dressing to make the room feel inhabited rather than a
	# bare warehouse: ceiling, area rug, baseboards, paintings on the walls
	# that aren't broken up by doors, and a hanging ceiling lamp. None of
	# this is interactive or surfaced to the agent — the server's
	# observation set is unchanged.
	var size_x: float = width * tile_m
	var size_z: float = height * tile_m

	_add_ceiling(size_x, size_z)
	_add_rug(size_x, size_z)
	_add_baseboards(size_x, size_z)
	_add_wall_paintings(size_x, size_z, doors)
	_add_ceiling_lamp(size_x, size_z)


func _add_ceiling(size_x: float, size_z: float) -> void:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.86, 0.83, 0.78)
	mat.roughness = 0.95
	var plane := PlaneMesh.new()
	plane.size = Vector2(size_x, size_z)
	plane.material = mat
	# Plane normal is +Y by default; flip so it faces down (visible from below).
	plane.orientation = PlaneMesh.FACE_Y
	var mi := MeshInstance3D.new()
	mi.mesh = plane
	mi.transform.origin = Vector3(size_x * 0.5, WALL_HEIGHT - 0.01, size_z * 0.5)
	mi.rotation = Vector3(PI, 0.0, 0.0)
	add_child(mi)


func _add_rug(size_x: float, size_z: float) -> void:
	# Outer rug border + inner pattern, slightly above the floor.
	var border_mat := StandardMaterial3D.new()
	border_mat.albedo_color = Color(0.30, 0.10, 0.10)
	border_mat.roughness = 0.85
	var inner_mat := StandardMaterial3D.new()
	inner_mat.albedo_color = Color(0.60, 0.20, 0.18)
	inner_mat.roughness = 0.85

	var border := PlaneMesh.new()
	border.size = Vector2(size_x * 0.55, size_z * 0.55)
	border.material = border_mat
	var b_mi := MeshInstance3D.new()
	b_mi.mesh = border
	b_mi.transform.origin = Vector3(size_x * 0.5, 0.012, size_z * 0.5)
	add_child(b_mi)

	var inner := PlaneMesh.new()
	inner.size = Vector2(size_x * 0.45, size_z * 0.45)
	inner.material = inner_mat
	var i_mi := MeshInstance3D.new()
	i_mi.mesh = inner
	i_mi.transform.origin = Vector3(size_x * 0.5, 0.014, size_z * 0.5)
	add_child(i_mi)


func _add_baseboards(size_x: float, size_z: float) -> void:
	# Thin dark trim along the inside base of the four perimeter walls.
	var trim_mat := StandardMaterial3D.new()
	trim_mat.albedo_color = Color(0.32, 0.25, 0.20)
	trim_mat.roughness = 0.7

	var bh: float = 0.18
	var bd: float = 0.04
	# Wall blocks occupy a 1 m thick perimeter. Trim sits flush against the
	# inner wall face (z=1 for north, z=size_z-1 for south, etc.).
	# North + South run along X
	for z in [1.0 + bd * 0.5, size_z - 1.0 - bd * 0.5]:
		var b := MeshInstance3D.new()
		var box := BoxMesh.new()
		box.size = Vector3(size_x - 2.0, bh, bd)
		box.material = trim_mat
		b.mesh = box
		b.transform.origin = Vector3(size_x * 0.5, bh * 0.5, z)
		add_child(b)
	# East + West run along Z
	for x in [1.0 + bd * 0.5, size_x - 1.0 - bd * 0.5]:
		var b := MeshInstance3D.new()
		var box := BoxMesh.new()
		box.size = Vector3(bd, bh, size_z - 2.0)
		box.material = trim_mat
		b.mesh = box
		b.transform.origin = Vector3(x, bh * 0.5, size_z * 0.5)
		add_child(b)


func _add_wall_paintings(size_x: float, size_z: float, doors: Array) -> void:
	# Drop a framed painting on each wall that has no door, eyeballed at
	# realistic eye height. Three different scenes (palette only, no real
	# image) so each wall looks different.
	var walls_with_doors: Array = []
	for d in doors:
		walls_with_doors.append(String(d.get("wall", "")))

	var palettes := [
		[Color(0.18, 0.28, 0.45), Color(0.55, 0.45, 0.30), Color(0.85, 0.78, 0.55)],
		[Color(0.25, 0.40, 0.20), Color(0.65, 0.55, 0.35), Color(0.30, 0.20, 0.10)],
		[Color(0.45, 0.20, 0.15), Color(0.85, 0.70, 0.45), Color(0.25, 0.18, 0.12)],
		[Color(0.30, 0.30, 0.40), Color(0.85, 0.85, 0.85), Color(0.55, 0.40, 0.30)],
	]
	var palette_idx := 0
	var center_x: float = size_x * 0.5
	var center_z: float = size_z * 0.5
	var painting_y: float = WALL_HEIGHT * 0.55

	# Wall blocks span 1 metre out from each edge. Inner faces are at:
	#   north: z = 1.0 + small offset (poke into the room)
	#   south: z = size_z - 1.0 - small offset
	#   east:  x = size_x - 1.0 - small offset
	#   west:  x = 1.0 + small offset
	var poke: float = 0.05
	var walls := [
		{"name": "north", "pos": Vector3(center_x, painting_y, 1.0 + poke), "vertical": false},
		{"name": "south", "pos": Vector3(center_x, painting_y, size_z - 1.0 - poke), "vertical": false},
		{"name": "east",  "pos": Vector3(size_x - 1.0 - poke, painting_y, center_z), "vertical": true},
		{"name": "west",  "pos": Vector3(1.0 + poke, painting_y, center_z), "vertical": true},
	]
	for w in walls:
		if String(w["name"]) in walls_with_doors:
			continue
		_paint_one_painting(w["pos"] as Vector3, bool(w["vertical"]), palettes[palette_idx % palettes.size()])
		palette_idx += 1


func _paint_one_painting(pos: Vector3, vertical: bool, palette: Array) -> void:
	var holder := Node3D.new()
	holder.transform.origin = pos
	add_child(holder)
	if vertical:
		holder.rotation = Vector3(0.0, deg_to_rad(90.0), 0.0)

	var frame_mat := StandardMaterial3D.new()
	frame_mat.albedo_color = Color(0.40, 0.30, 0.20)
	frame_mat.roughness = 0.6
	var canvas_w: float = 1.10
	var canvas_h: float = 0.80
	var depth: float = 0.06

	# Frame
	var frame := MeshInstance3D.new()
	var frame_box := BoxMesh.new()
	frame_box.size = Vector3(canvas_w, canvas_h, depth)
	frame_box.material = frame_mat
	frame.mesh = frame_box
	frame.transform.origin = Vector3(0.0, 0.0, 0.0)
	holder.add_child(frame)

	# A few coloured rectangles inside the frame, suggesting a painting.
	var inner_w := canvas_w - 0.12
	var inner_h := canvas_h - 0.12
	var bands := palette.size()
	for i in bands:
		var c: Color = palette[i]
		var band := MeshInstance3D.new()
		var bbox := BoxMesh.new()
		var band_h: float = inner_h / float(bands)
		bbox.size = Vector3(inner_w, band_h, 0.005)
		var band_mat := StandardMaterial3D.new()
		band_mat.albedo_color = c
		bbox.material = band_mat
		band.mesh = bbox
		band.transform.origin = Vector3(
			0.0,
			-inner_h * 0.5 + band_h * 0.5 + i * band_h,
			-depth * 0.5 - 0.001,
		)
		holder.add_child(band)


func _add_ceiling_lamp(size_x: float, size_z: float) -> void:
	var holder := Node3D.new()
	holder.transform.origin = Vector3(size_x * 0.5, WALL_HEIGHT - 0.02, size_z * 0.5)
	add_child(holder)

	var rod_mat := StandardMaterial3D.new()
	rod_mat.albedo_color = Color(0.20, 0.20, 0.22)

	var brass_mat := StandardMaterial3D.new()
	brass_mat.albedo_color = Color(0.85, 0.65, 0.20)
	brass_mat.metallic = 0.6
	brass_mat.roughness = 0.4

	var glow_mat := StandardMaterial3D.new()
	glow_mat.albedo_color = Color(1.0, 0.92, 0.70)
	glow_mat.emission_enabled = true
	glow_mat.emission = Color(1.0, 0.92, 0.70)
	glow_mat.emission_energy_multiplier = 1.5

	# Hanging rod
	var rod := MeshInstance3D.new()
	var rod_box := BoxMesh.new()
	rod_box.size = Vector3(0.04, 0.40, 0.04)
	rod_box.material = rod_mat
	rod.mesh = rod_box
	rod.transform.origin = Vector3(0.0, -0.20, 0.0)
	holder.add_child(rod)

	# Brass shade
	var shade := MeshInstance3D.new()
	var shade_box := BoxMesh.new()
	shade_box.size = Vector3(0.45, 0.10, 0.45)
	shade_box.material = brass_mat
	shade.mesh = shade_box
	shade.transform.origin = Vector3(0.0, -0.45, 0.0)
	holder.add_child(shade)

	# Glowing bulb under the shade
	var bulb := MeshInstance3D.new()
	var bulb_box := BoxMesh.new()
	bulb_box.size = Vector3(0.20, 0.06, 0.20)
	bulb_box.material = glow_mat
	bulb.mesh = bulb_box
	bulb.transform.origin = Vector3(0.0, -0.55, 0.0)
	holder.add_child(bulb)

	# Local point light for atmosphere.
	var omni := OmniLight3D.new()
	omni.light_energy = 1.2
	omni.omni_range = max(size_x, size_z)
	omni.transform.origin = Vector3(0.0, -0.7, 0.0)
	omni.light_color = Color(1.0, 0.92, 0.78)
	holder.add_child(omni)


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
