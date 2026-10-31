--!strict
-- `view` (what the player sees: the client agent, with the camera) and `watch` (what changes on some things over a
-- few seconds: the server). Kit frame throughout.

local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local Frame = require(script.Parent.Frame)
local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)
local World = require(script.Parent.World)

local Sight = {}

Sight.OMITTED = Json.array({
	"held",
	"scene.sun",
	"openings",
	"ways_up",
	"lane",
	"top_slope_degrees",
	"low_end",
	"high_end",
	"uphill",
	"body.under",
	"body.overhead",
	"look",
	"sonar",
	"blocks_player",
})

local function wanted(only: any, inst: Instance, path: string): boolean
	if type(only) ~= "table" then
		return true
	end
	for _, x in only do
		if x == path or x == inst.Name then
			return true
		end
	end
	return false
end

-- `view`: {"max_count"?, "max_distance"? (kit cm), "only"? [names or paths]} -> {"visible", "camera", "body", ...}.
function Sight.view(args: { [string]: any }): (boolean, any)
	local camera = Workspace.CurrentCamera
	if camera == nil then
		return false, "No camera"
	end
	local character, humanoid, root = World.body()
	local s = Frame.STUD_CM
	local maxCount = math.clamp(tonumber(args.max_count) or 24, 1, 200)
	local maxDistance = (tonumber(args.max_distance) or 20000) / s
	local camCf = camera.CFrame
	local camPos = camCf.Position
	local vp = camera.ViewportSize
	local feetY = root and (root.Position.Y - World.halfHeight(humanoid, root)) or camPos.Y
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = character and { character } or {}
	-- what is under the crosshair
	local crosshair: Instance? = nil
	local ray = camera:ViewportPointToRay(vp.X / 2, vp.Y / 2)
	local hit = Workspace:Raycast(ray.Origin, ray.Direction * maxDistance, params)
	if hit then
		crosshair = hit.Instance
	end
	local items = {}
	for _, thing in World.things() do
		if character and (thing == character or thing:IsDescendantOf(character)) then
			continue
		end
		local path = Paths.of(thing)
		if not wanted(args.only, thing, path) then
			continue
		end
		local _, box, size = World.placement(thing)
		if box == nil or size == nil then
			continue
		end
		local center = box.Position
		local distance = (center - camPos).Magnitude
		if distance > maxDistance then
			continue
		end
		local screen, onScreen = camera:WorldToViewportPoint(center)
		if not onScreen or screen.Z <= 0 then
			continue
		end
		local seen = false
		local half = Frame.aabbHalf(box, size)
		for _, target in { center, center + Vector3.new(0, half.Y * 0.95, 0) } do
			local r = Workspace:Raycast(camPos, target - camPos, params)
			if r == nil or r.Instance == thing or r.Instance:IsDescendantOf(thing) then
				seen = true
				break
			end
		end
		if not seen then
			continue
		end
		local item: { [string]: any } = {
			name = thing.Name,
			path = path,
			class = thing.ClassName,
			center = Frame.kit(center),
			size = Frame.kitSize(half * 2),
			yaw = (Frame.yawPitch(box)),
			distance = Frame.r2(distance * s),
			screen_x = Frame.r2(screen.X / vp.X * 2 - 1),
			screen_y = Frame.r2(1 - screen.Y / vp.Y * 2),
			top_above_feet = Frame.r2((center.Y + half.Y - feetY) * s),
			bottom_above_feet = Frame.r2((center.Y - half.Y - feetY) * s),
			crosshair_on = crosshair ~= nil and (crosshair == thing or crosshair:IsDescendantOf(thing)),
		}
		local _, _, _, velocity = World.placement(thing)
		if velocity and velocity.Magnitude > 0.01 then
			item.speed = Frame.r2(velocity.Magnitude * s)
		end
		local labels = thing:FindFirstChildWhichIsA("BillboardGui", true)
		local label = labels and labels:FindFirstChildWhichIsA("TextLabel", true)
		if label and label.Text ~= "" then
			item.label = label.Text
		end
		table.insert(items, item)
	end
	table.sort(items, function(a, b)
		return a.distance < b.distance
	end)
	local visible = Json.array()
	for i = 1, math.min(#items, maxCount) do
		visible[i] = items[i]
	end
	local vfov = camera.FieldOfView
	local hfov = math.deg(2 * math.atan(math.tan(math.rad(vfov) / 2) * vp.X / math.max(vp.Y, 1)))
	local yaw, pitch = Frame.yawPitch(camCf)
	local body: any = Json.null
	if root and humanoid then
		local standing =
			Workspace:Raycast(root.Position, Vector3.new(0, -(World.halfHeight(humanoid, root) + 1), 0), params)
		body = {
			velocity = Frame.kit(root.AssemblyLinearVelocity),
			feet_z = Frame.r2(feetY * s),
			on_ground = humanoid.FloorMaterial ~= Enum.Material.Air,
			falling = humanoid:GetState() == Enum.HumanoidStateType.Freefall,
			crouched = false,
			standing_on = standing and Paths.of(standing.Instance) or Json.null,
		}
	end
	local result: { [string]: any } = {
		visible = visible,
		camera = { location = Frame.kit(camPos), yaw = yaw, pitch = pitch, fov = Frame.r2(hfov), vfov = Frame.r2(vfov) },
		crosshair_tested = true,
		oriented = true,
		effects_play = true,
		body = body,
		omitted = Sight.OMITTED,
	}
	if type(args.only) == "table" then
		result.only = args.only
	end
	return true, result
end

-- `watch`: {"objects": [paths], "seconds"?, "moved"? (kit cm), "turned"? (degrees), "resized"? (fraction)} ->
-- {"changes": [{"t", "object", "what"}]}: moved, turned, changed size, part shown / hidden (Transparency), gone.
-- Yields for `seconds`.
function Sight.watch(args: { [string]: any }, now: () -> number): { [string]: any }
	local seconds = math.clamp(tonumber(args.seconds) or 0.6, 0, 30)
	local moved = (tonumber(args.moved) or 2) / Frame.STUD_CM
	local turned = tonumber(args.turned) or 1
	local resized = tonumber(args.resized) or 0.02
	local entries = {}
	for _, path in (type(args.objects) == "table" and args.objects or {}) do
		local inst = Paths.resolve(path)
		if inst then
			local pivot, _, size = World.placement(inst)
			if pivot then
				table.insert(entries, {
					inst = inst,
					path = tostring(path),
					pivot = pivot,
					size = size or Vector3.zero,
					shown = not (inst:IsA("BasePart") and inst.Transparency >= 1),
					found = {},
				})
			end
		end
	end
	local function sample()
		local t = now()
		for _, e in entries do
			if e.found.gone then
				continue
			end
			if e.inst.Parent == nil then
				e.found.gone = t
				continue
			end
			local pivot, _, size = World.placement(e.inst)
			if pivot == nil then
				continue
			end
			if not e.found.moved and (pivot.Position - e.pivot.Position).Magnitude > moved then
				e.found.moved = t
			end
			if not e.found.turned then
				local _, angle = (e.pivot.Rotation:Inverse() * pivot.Rotation):ToAxisAngle()
				if math.deg(math.abs(angle)) > turned then
					e.found.turned = t
				end
			end
			if not e.found["changed size"] and size then
				local base = e.size
				local d = (size - base)
				local rel = math.max(
					math.abs(d.X) / math.max(base.X, 0.001),
					math.abs(d.Y) / math.max(base.Y, 0.001),
					math.abs(d.Z) / math.max(base.Z, 0.001)
				)
				if rel > resized then
					e.found["changed size"] = t
				end
			end
			if e.inst:IsA("BasePart") then
				local shown = e.inst.Transparency < 1
				if shown ~= e.shown then
					e.found[shown and "part shown" or "part hidden"] = t
					e.shown = shown
				end
			end
		end
	end
	local started = os.clock()
	sample()
	while os.clock() - started < seconds do
		RunService.Heartbeat:Wait()
		sample()
	end
	local changes = Json.array()
	for _, e in entries do
		for what, t in e.found do
			table.insert(changes, { t = Frame.r2(t), object = e.path, what = what })
		end
	end
	table.sort(changes, function(a, b)
		return a.t < b.t
	end)
	return { changes = changes }
end

return Sight
