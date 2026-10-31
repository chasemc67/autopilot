--!strict
-- The plugin's link to the brain over HTTP (autopilot/roblox_bridge.py): a Studio plugin can't hold a WebSocket or
-- listen, so it long-polls. One loop keeps a poll waiting at the bridge for the brain's frames; another sends what the
-- plugin has to say (answers, events) as soon as there is some, and takes any frames the bridge has meanwhile. Every
-- request carries the token (X-Autopilot-Token). The edit instance says hello; a play test's server attaches to its
-- session.

local HttpService = game:GetService("HttpService")

local Config = require(script.Parent.Config)
local Json = require(script.Parent.Json)

local Link = {}
Link.__index = Link

export type Options = {
	plugin: Plugin?,
	context: string, -- "edit" | "server" | "client"
	session: string?, -- (server, client: the session to attach to; nil for the newest)
	hello: (() -> { [string]: any })?, -- (edit: the hello frame)
	onFrame: (frame: { [string]: any }) -> (),
	onState: ((state: string, detail: string?) -> ())?, -- "connecting" | "connected" | "rejected" | "lost" | "closed"
}

function Link.new(options: Options)
	local self = setmetatable({}, Link)
	self.options = options
	self.context = options.context
	self.session = options.session
	self.outbox = {} :: { string } -- frames as JSON text
	self.connected = false
	self.closed = false
	self.problem = nil :: string?
	self._next = 0 -- (os.clock() before which no request goes: the rate limit)
	self._wake = Instance.new("BindableEvent")
	return self
end

function Link:_state(state: string, detail: string?)
	if self.options.onState then
		task.spawn(self.options.onState, state, detail)
	end
end

-- POST to an endpoint: (ok, status code, decoded body or nil).
function Link:_post(name: string, bodyText: string): (boolean, number, any)
	local gap = 1 / Config.MAX_REQUESTS_PER_SECOND
	local t = os.clock()
	if t < self._next then
		self._next += gap
		task.wait(self._next - gap - t)
	else
		self._next = t + gap
	end
	local ok, res = pcall(function()
		return HttpService:RequestAsync({
			Url = Config.url(self.options.plugin) .. "/" .. name,
			Method = "POST",
			Headers = { ["X-Autopilot-Token"] = Config.token(self.options.plugin) },
			Body = bodyText,
		})
	end)
	if not ok then
		return false, 0, tostring(res)
	end
	local decoded = nil
	pcall(function()
		decoded = HttpService:JSONDecode(res.Body)
	end)
	return res.Success, res.StatusCode, decoded
end

-- Say hello (edit) or attach (play): whether the bridge took it.
function Link:_join(): boolean
	local ok, status, body
	if self.context == "edit" then
		local hello = (self.options.hello :: () -> { [string]: any })()
		if self.session then
			hello.resume = self.session
		end
		ok, status, body = self:_post("hello", Json.encode(hello))
		if ok and type(body) == "table" and body.session then
			self.session = body.session
		end
	else
		ok, status, body = self:_post("attach", Json.encode({ session = self.session, context = self.context }))
		if ok and type(body) == "table" and body.session then
			self.session = body.session
		end
	end
	if ok then
		self.connected, self.problem = true, nil
		self:_state("connected", self.session)
		return true
	end
	local why = (type(body) == "table" and (body.error or body.message)) or (type(body) == "string" and body) or ""
	self.problem = string.format("%s (%s)", tostring(why), tostring(status))
	if status == 401 or status == 403 then
		self:_state("rejected", tostring(why))
	else
		self:_state("lost", self.problem)
	end
	return false
end

-- Exchange frames with the bridge: hand over `frames` (JSON texts), get the brain's, waiting up to `wait` seconds.
function Link:_exchange(frames: { string }, wait: number): boolean
	local body = string.format(
		'{"session":%s,"context":%s,"wait":%s,"frames":[%s]}',
		Json.encode(self.session),
		Json.encode(self.context),
		Json.encode(wait),
		table.concat(frames, ",")
	)
	local ok, status, answer = self:_post("exchange", body)
	if not ok then
		if status == 404 or status == 409 then -- (the bridge forgot this session or channel: join again)
			self.connected = false
		end
		self.problem = string.format("exchange failed (%s)", tostring(status))
		return false
	end
	if type(answer) == "table" and type(answer.frames) == "table" then
		for _, frame in answer.frames do
			task.spawn(self.options.onFrame, frame)
		end
	end
	return true
end

-- Queue a frame for the brain.
function Link:send(frame: { [string]: any })
	self:sendText(Json.encode(frame))
end

-- Queue a frame already written as JSON.
function Link:sendText(text: string)
	if self.closed then
		return
	end
	table.insert(self.outbox, text)
	self._wake:Fire()
end

function Link:_pollLoop()
	local backoff = 1
	while not self.closed do
		if not self.connected then
			if not self:_join() then
				task.wait(backoff)
				backoff = math.min(backoff * 2, 15)
				continue
			end
			backoff = 1
		end
		if not self:_exchange({}, Config.POLL_WAIT) then
			self:_state("lost", self.problem)
			task.wait(backoff)
			backoff = math.min(backoff * 2, 15)
		else
			backoff = 1
		end
	end
end

function Link:_sendLoop()
	while not self.closed do
		if #self.outbox == 0 then
			self._wake.Event:Wait()
		end
		if self.closed then
			break
		end
		if not self.connected then
			task.wait(0.25)
			continue
		end
		task.wait() -- (a frame's worth of answers and events go together)
		local batch = self.outbox
		self.outbox = {}
		if not self:_exchange(batch, 0) then
			-- (put them back, first: answers must not be lost to a dropped request)
			for _, text in self.outbox do
				table.insert(batch, text)
			end
			self.outbox = batch
			task.wait(0.5)
		end
	end
end

function Link:start()
	self:_state("connecting")
	task.spawn(function()
		self:_pollLoop()
	end)
	task.spawn(function()
		self:_sendLoop()
	end)
end

function Link:close(why: string?)
	if self.closed then
		return
	end
	local outbox = self.outbox
	self.outbox = {}
	if self.session and #outbox > 0 then
		pcall(function()
			self:_exchange(outbox, 0)
		end)
	end
	self.closed = true
	self._wake:Fire()
	if self.session then
		pcall(function()
			self:_post("bye", Json.encode({ session = self.session, context = self.context, why = why or "closed" }))
		end)
	end
	self:_state("closed", why)
end

return Link
