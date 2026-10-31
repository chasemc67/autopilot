--!strict
-- Roblox's frame and the kit's. Roblox: x right, y up, -z forward (a CFrame's LookVector), studs. The kit's (Unreal's):
-- X forward, Y right, Z up, centimetres, yaw turning right, pitch up. One stud is 28 cm (Roblox's own conversion).
--     kit (X, Y, Z) = 28 * (-z, x, y);  yaw = -deg(ry);  pitch = deg(rx)   (rx, ry, rz = CFrame:ToOrientation())
-- Must match autopilot/engines/roblox.py.

local Frame = {}

Frame.STUD_CM = 28

export type Kit = { X: number, Y: number, Z: number }

function Frame.r2(n: number): number
	return math.round(n * 100) / 100
end

-- A position or velocity (studs) in the kit's frame (cm).
function Frame.kit(v: Vector3): Kit
	local s = Frame.STUD_CM
	return { X = Frame.r2(-v.Z * s), Y = Frame.r2(v.X * s), Z = Frame.r2(v.Y * s) }
end

-- A size or half size (studs, along Roblox's axes) along the kit's axes (cm, never negative).
function Frame.kitSize(v: Vector3): Kit
	local s = Frame.STUD_CM
	return { X = Frame.r2(math.abs(v.Z) * s), Y = Frame.r2(math.abs(v.X) * s), Z = Frame.r2(math.abs(v.Y) * s) }
end

local function num(t: any, upper: string, lower: string): number
	if type(t) ~= "table" then
		return 0
	end
	return tonumber(t[upper]) or tonumber(t[lower]) or 0
end

-- A kit point ({"X","Y","Z"} in cm) as a Roblox position (studs).
function Frame.native(k: any): Vector3
	local s = Frame.STUD_CM
	return Vector3.new(num(k, "Y", "y") / s, num(k, "Z", "z") / s, -num(k, "X", "x") / s)
end

-- A kit direction as a Roblox direction (not scaled).
function Frame.nativeDir(k: any): Vector3
	return Vector3.new(num(k, "Y", "y"), num(k, "Z", "z"), -num(k, "X", "x"))
end

-- (yaw, pitch) in the kit's degrees of a CFrame's rotation.
function Frame.yawPitch(cf: CFrame): (number, number)
	local rx, ry = cf:ToOrientation()
	return Frame.r2(-math.deg(ry)), Frame.r2(math.deg(rx))
end

-- {"Pitch", "Yaw", "Roll"} of a CFrame, as Unreal's rotators are.
function Frame.rot(cf: CFrame): { Pitch: number, Yaw: number, Roll: number }
	local rx, ry, rz = cf:ToOrientation()
	return { Pitch = Frame.r2(math.deg(rx)), Yaw = Frame.r2(-math.deg(ry)), Roll = Frame.r2(-math.deg(rz)) }
end

-- The axis-aligned half size of a box (cframe, size), in studs on Roblox's axes.
function Frame.aabbHalf(cf: CFrame, size: Vector3): Vector3
	local half = size / 2
	local r, u, b = cf.RightVector, cf.UpVector, -cf.LookVector
	return Vector3.new(
		math.abs(r.X) * half.X + math.abs(u.X) * half.Y + math.abs(b.X) * half.Z,
		math.abs(r.Y) * half.X + math.abs(u.Y) * half.Y + math.abs(b.Y) * half.Z,
		math.abs(r.Z) * half.X + math.abs(u.Z) * half.Y + math.abs(b.Z) * half.Z
	)
end

return Frame
