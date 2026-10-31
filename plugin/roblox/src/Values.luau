--!strict
-- Roblox values to JSON and back, in Roblox's own units (for `rc`): a Vector3 as {"x","y","z"} (studs), a CFrame as
-- {"position", "orientation"} (degrees, YXZ), a Color3 as {"r","g","b"}, an instance as its path, an EnumItem as its
-- name, a BrickColor as its name, UDim2 as {"x": {"scale","offset"}, "y": ...}.

local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local Values = {}

local function v3(v: Vector3)
	return { x = v.X, y = v.Y, z = v.Z }
end

function Values.toJson(value: any, depth: number?): any
	local d = depth or 0
	local kind = typeof(value)
	if value == nil then
		return Json.null
	elseif kind == "number" or kind == "string" or kind == "boolean" then
		return value
	elseif kind == "Vector3" then
		return v3(value)
	elseif kind == "Vector2" then
		return { x = value.X, y = value.Y }
	elseif kind == "CFrame" then
		local rx, ry, rz = value:ToOrientation()
		return { position = v3(value.Position), orientation = { x = math.deg(rx), y = math.deg(ry), z = math.deg(rz) } }
	elseif kind == "Color3" then
		return { r = value.R, g = value.G, b = value.B }
	elseif kind == "BrickColor" then
		return value.Name
	elseif kind == "EnumItem" then
		return value.Name
	elseif kind == "Instance" then
		return Paths.of(value)
	elseif kind == "UDim2" then
		return {
			x = { scale = value.X.Scale, offset = value.X.Offset },
			y = { scale = value.Y.Scale, offset = value.Y.Offset },
		}
	elseif kind == "UDim" then
		return { scale = value.Scale, offset = value.Offset }
	elseif kind == "NumberRange" then
		return { min = value.Min, max = value.Max }
	elseif kind == "table" then
		if d > 8 then
			return "<table>"
		end
		local out = {}
		for k, v in value do
			out[k] = Values.toJson(v, d + 1)
		end
		return out
	end
	return tostring(value)
end

local function num(t: any, a: string, b: string): number
	return tonumber(t[a]) or tonumber(t[b]) or 0
end

-- A JSON value as the kind of value `current` is (the property's present value), or as it comes when there's none.
function Values.fromJson(value: any, current: any): any
	local kind = typeof(current)
	if type(value) == "table" then
		if kind == "Vector3" or (current == nil and value.x ~= nil and value.z ~= nil) then
			return Vector3.new(num(value, "x", "X"), num(value, "y", "Y"), num(value, "z", "Z"))
		elseif kind == "Vector2" then
			return Vector2.new(num(value, "x", "X"), num(value, "y", "Y"))
		elseif kind == "Color3" then
			return Color3.new(num(value, "r", "R"), num(value, "g", "G"), num(value, "b", "B"))
		elseif kind == "CFrame" then
			local p = value.position or value
			local o = value.orientation or {}
			return CFrame.new(num(p, "x", "X"), num(p, "y", "Y"), num(p, "z", "Z"))
				* CFrame.fromOrientation(
					math.rad(num(o, "x", "X")),
					math.rad(num(o, "y", "Y")),
					math.rad(num(o, "z", "Z"))
				)
		elseif kind == "UDim2" then
			local x, y = value.x or {}, value.y or {}
			return UDim2.new(
				num(x, "scale", "Scale"),
				num(x, "offset", "Offset"),
				num(y, "scale", "Scale"),
				num(y, "offset", "Offset")
			)
		end
		return value
	end
	if kind == "EnumItem" and (type(value) == "string" or type(value) == "number") then
		for _, item in (current :: EnumItem).EnumType:GetEnumItems() do
			if item.Name == value or item.Value == value then
				return item
			end
		end
		return current
	elseif kind == "BrickColor" and type(value) == "string" then
		return BrickColor.new(value)
	elseif (kind == "Instance" or current == nil) and type(value) == "string" and string.sub(value, 1, 1) == "/" then
		return Paths.resolve(value) or value
	elseif kind == "number" then
		return tonumber(value) or value
	end
	return value
end

return Values
