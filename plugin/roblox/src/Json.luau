--!strict
-- JSON for the bridge. HttpService:JSONEncode can't tell an empty object from an empty list (both are `{}` in Luau and
-- it writes `[]`), and it can't write null. Autopilot's brain reads `{}` where it wants an object, so this encoder
-- writes an empty table as `{}` unless it is marked with Json.array, and Json.null as null. Decoding is HttpService's.

local HttpService = game:GetService("HttpService")

local Json = {}

local ARRAY = setmetatable({}, {
	__tostring = function()
		return "Json.array"
	end,
})
Json.null = setmetatable({}, {
	__tostring = function()
		return "Json.null"
	end,
})

-- Mark a table as a list (so an empty one is written `[]`). Returns the table.
function Json.array(t: { any }?): { any }
	local list = t or {}
	return setmetatable(list :: any, ARRAY) :: any
end

local function isArray(t: { [any]: any }): (boolean, number)
	if getmetatable(t) == ARRAY then
		return true, #t
	end
	local n = 0
	for k in t do
		if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then
			return false, 0
		end
		n = math.max(n, k)
	end
	if n == 0 then
		return false, 0 -- (empty: an object)
	end
	for i = 1, n do
		if t[i] == nil then
			return false, 0
		end
	end
	return true, n
end

local ESCAPES = {
	['"'] = '\\"',
	["\\"] = "\\\\",
	["\b"] = "\\b",
	["\f"] = "\\f",
	["\n"] = "\\n",
	["\r"] = "\\r",
	["\t"] = "\\t",
}

local function encodeString(s: string): string
	local out = s:gsub('[%c"\\]', function(c)
		return ESCAPES[c] or string.format("\\u%04x", string.byte(c))
	end)
	if utf8.len(out) == nil then -- (not UTF-8: bytes above 127 as code points, so the JSON stays valid)
		out = out:gsub("[\128-\255]", function(c)
			return string.format("\\u%04x", string.byte(c))
		end)
	end
	return '"' .. out .. '"'
end

local function encodeNumber(n: number): string
	if n ~= n or n == math.huge or n == -math.huge then
		return "null"
	end
	if n % 1 == 0 and math.abs(n) < 2 ^ 53 then
		return string.format("%d", n)
	end
	return string.format("%.10g", n)
end

local function encode(value: any, depth: number, seen: { [any]: boolean }): string
	local kind = type(value)
	if value == nil or value == Json.null then
		return "null"
	elseif kind == "boolean" then
		return value and "true" or "false"
	elseif kind == "number" then
		return encodeNumber(value)
	elseif kind == "string" then
		return encodeString(value)
	elseif kind == "table" then
		if depth > 40 or seen[value] then
			return "null"
		end
		seen[value] = true
		local array, n = isArray(value)
		local parts = {}
		if array then
			for i = 1, n do
				parts[i] = encode(value[i], depth + 1, seen)
			end
			seen[value] = nil
			return "[" .. table.concat(parts, ",") .. "]"
		end
		for k, v in value do
			if type(k) == "string" or type(k) == "number" then
				table.insert(parts, encodeString(tostring(k)) .. ":" .. encode(v, depth + 1, seen))
			end
		end
		seen[value] = nil
		return "{" .. table.concat(parts, ",") .. "}"
	end
	return encodeString(tostring(value))
end

function Json.encode(value: any): string
	return encode(value, 0, {})
end

function Json.decode(text: string): any
	return HttpService:JSONDecode(text)
end

return Json
