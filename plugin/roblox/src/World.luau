--!strict
-- The engine-neutral reads (docs/protocol.md, Engines), in the kit frame: the player, `find`, `transforms`, `pose`,
-- `movement`. Works in the play test's server and in its client (the client agent): the player is the client's
-- LocalPlayer there, the first player on the server.

local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local Frame = require(script.Parent.Frame)
local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local World = {}

function World.player(): Player?
	if RunService:IsClient() and Players.LocalPlayer then
		return Players.LocalPlayer
	end
	return Players:GetPlayers()[1]
end

-- (character, humanoid, root part) of the player, any of them nil.
function World.body(): (Model?, Humanoid?, BasePart?)
	local p = World.player()
	local character = p and p.Character
	if character == nil then
		return nil, nil, nil
	end
	local humanoid = character:FindFirstChildOfClass("Humanoid")
	local root = (humanoid and humanoid.RootPart) or character:FindFirstChild("HumanoidRootPart")
	return character, humanoid, (root and root:IsA("BasePart")) and root or nil
end

local function isCharacter(inst: Instance): boolean
	return inst:IsA("Model") and Players:GetPlayerFromCharacter(inst) ~= nil
end

-- The place's "things", as Unreal's actors: Workspace's direct children (but its Camera and Terrain), and the Models
-- and parts in the Folders under it (through nested Folders).
function World.things(): { Instance }
	local out = {}
	local function walk(parent: Instance)
		for _, c in parent:GetChildren() do
			if c:IsA("Folder") then
				walk(c)
			elseif (c:IsA("Model") or c:IsA("BasePart")) and not c:IsA("Terrain") then
				table.insert(out, c)
			end
		end
	end
	for _, c in Workspace:GetChildren() do
		if c:IsA("Folder") then
			walk(c)
		elseif not (c:IsA("Camera") or c:IsA("Terrain")) then
			table.insert(out, c)
		end
	end
	return out
end

-- `find`: {"class"?, "name"?, "limit"?, "things"?} -> {"objects": [paths]}.
function World.find(args: { [string]: any }): { [string]: any }
	local limit = math.clamp(tonumber(args.limit) or 2000, 1, 20000)
	local cls = type(args.class) == "string" and args.class or nil
	local name = type(args.name) == "string" and string.lower(args.name) or nil
	local pool: { Instance }
	if args.things then
		pool = World.things()
	elseif cls and string.sub(cls, 1, 4) == "tag:" then
		pool = CollectionService:GetTagged(string.sub(cls, 5))
		cls = nil
	elseif cls == "Player" then
		pool = Players:GetPlayers()
	else
		pool = Workspace:GetDescendants()
	end
	local out = Json.array()
	for _, inst in pool do
		if #out >= limit then
			break
		end
		local ok = true
		if cls then -- (a class, subclasses too; else an exact name)
			local isA = false
			pcall(function()
				isA = inst:IsA(cls :: string)
			end)
			ok = isA or inst.Name == cls
		end
		if ok and name then
			ok = string.find(string.lower(inst.Name), name, 1, true) ~= nil
		end
		if ok then
			table.insert(out, Paths.of(inst))
		end
	end
	return { objects = out }
end

-- (pivot CFrame, box CFrame, box size, velocity) of an instance with a place in the world, else nil.
function World.placement(inst: Instance): (CFrame?, CFrame?, Vector3?, Vector3?)
	if inst:IsA("BasePart") then
		return inst.CFrame, inst.CFrame, inst.Size, inst.AssemblyLinearVelocity
	elseif inst:IsA("Model") then
		local ok, cf, size = pcall(function()
			return inst:GetBoundingBox()
		end)
		local pivot = inst:GetPivot()
		local primary = inst.PrimaryPart
		local velocity = primary and primary.AssemblyLinearVelocity or Vector3.zero
		if ok then
			return pivot, cf, size, velocity
		end
		return pivot, pivot, Vector3.zero, velocity
	elseif inst:IsA("Attachment") then
		return inst.WorldCFrame, inst.WorldCFrame, Vector3.zero, Vector3.zero
	elseif inst:IsA("Player") then
		local character = inst.Character
		if character then
			return World.placement(character)
		end
	elseif inst:IsA("Humanoid") and inst.RootPart then
		return World.placement(inst.RootPart)
	end
	return nil, nil, nil, nil
end

function World.transform(inst: Instance): { [string]: any }?
	local pivot, box, size, velocity = World.placement(inst)
	if pivot == nil or box == nil or size == nil then
		return nil
	end
	return {
		location = Frame.kit(pivot.Position),
		rotation = Frame.rot(pivot),
		velocity = Frame.kit(velocity or Vector3.zero),
		center = Frame.kit(box.Position),
		extent = Frame.kitSize(Frame.aabbHalf(box, size)),
	}
end

-- `transforms`: {"objects": [paths]} -> {"objects": {path: {...} | null}}.
function World.transforms(args: { [string]: any }): { [string]: any }
	local out = {}
	for _, path in (type(args.objects) == "table" and args.objects or {}) do
		local inst = Paths.resolve(path)
		out[tostring(path)] = (inst and World.transform(inst)) or Json.null
	end
	return { objects = out }
end

-- Distance from the root part's middle down to the feet (studs).
function World.halfHeight(humanoid: Humanoid?, root: BasePart?): number
	if root == nil then
		return 0
	end
	return (humanoid and humanoid.HipHeight or 0) + root.Size.Y / 2
end

-- `pose`: the player's {"at", "feet", "eye", "yaw", "pitch", "velocity", "on_ground", "path"}. With the client's camera
-- (the client agent) the eye and view are the camera's; on the server, the head's and the root part's.
function World.pose(camera: Camera?): (boolean, any)
	local character, humanoid, root = World.body()
	if character == nil or root == nil then
		return false, "No player"
	end
	local hh = World.halfHeight(humanoid, root)
	local eye, view
	if camera then
		eye, view = camera.CFrame.Position, camera.CFrame
	else
		local head = character:FindFirstChild("Head")
		eye = (head and head:IsA("BasePart")) and head.Position or root.Position
		view = root.CFrame
	end
	local yaw, pitch = Frame.yawPitch(view)
	return true,
		{
			path = Paths.of(character),
			at = Frame.kit(root.Position),
			feet = Frame.kit(root.Position - Vector3.new(0, hh, 0)),
			eye = Frame.kit(eye),
			yaw = yaw,
			pitch = pitch,
			velocity = Frame.kit(root.AssemblyLinearVelocity),
			on_ground = humanoid ~= nil and humanoid.FloorMaterial ~= Enum.Material.Air,
		}
end

-- `movement`: the player's numbers in kit units, and where each came from.
function World.movement(): (boolean, any)
	local _, humanoid, root = World.body()
	if humanoid == nil or root == nil then
		return false, "No player"
	end
	local s = Frame.STUD_CM
	local g = Workspace.Gravity
	local jump
	local jumpSource
	if humanoid.UseJumpPower then
		jump, jumpSource = humanoid.JumpPower, "Humanoid.JumpPower"
	else
		jump, jumpSource = math.sqrt(2 * g * humanoid.JumpHeight), "Humanoid.JumpHeight (as sqrt(2 g h))"
	end
	return true,
		{
			walk_speed = Frame.r2(humanoid.WalkSpeed * s),
			jump_z = Frame.r2(jump * s),
			gravity = Frame.r2(g * s),
			half_height = Frame.r2(World.halfHeight(humanoid, root) * s),
			radius = Frame.r2(math.max(root.Size.X, root.Size.Z) / 2 * s),
			step = Json.null,
			max_slope = Frame.r2(humanoid.MaxSlopeAngle),
			sources = {
				walk_speed = "Humanoid.WalkSpeed",
				jump_z = jumpSource,
				gravity = "Workspace.Gravity",
				half_height = "Humanoid.HipHeight + HumanoidRootPart.Size.Y / 2",
				step = "a Humanoid steps up small ledges by itself (step is null)",
			},
		}
end

World.isCharacter = isCharacter

return World
