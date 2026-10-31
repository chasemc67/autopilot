from autopilot.kit import *


class ExplorePlaceHarness(Harness):
    goal = ("Try everything in the place to find what is wrong: coins (walk into them), the door in the wall (opens at "
            "5+ coins), the green goal pad behind it, the lever (E), the sign, waving (F), the edges of the wall. "
            "Prefer what hasn't been tried yet; already found: the goal pad can be reached around the wall with "
            "no coins, so now collect all the coins and check the door and the HUD; once the door is open, walk through it")
    tempo = 0.0
    stall_seconds = 0
    step_seconds = 0

    def setup(self, game):
        self.coins = game.call("/Workspace/Coins", "GetChildren")["ReturnValue"]
        self.where = game.locs(self.coins)
        self.value = {c: (game.get(c, "Value") or 1) for c in self.coins}
        self.player = "/Players/%s" % game.name_of(game.pawn)
        self.stats = self.player + "/leaderstats"
        self.door_at = game.loc("/Workspace/Door")
        self.goal_at = game.loc("/Workspace/GoalPad")
        self.lever_at = game.loc("/Workspace/Lever")
        self.sign_at = game.loc("/Workspace/Sign")
        self.tried = {}
        self.events = []

    def observe(self, game):
        reads = game.batch([
            lambda: game.pose(), lambda: game.get(self.stats, "Coins"),
            lambda: game.get(self.player, "ReachedGoal"), lambda: game.get("/Workspace/Door", "CanCollide"),
            lambda: game.get("/Workspace/Door", "Transparency"), lambda: game.get("/Workspace/Lever", "Pulled"),
            lambda: game.get("/Workspace/Lever", "Color"), lambda: game.get(self.player, "Waved"),
            lambda: game.screen(),
        ] + [(lambda c=c: game.exists(c)) for c in self.coins])
        pose, count, reached, door_solid, door_tr, pulled, lever_col, waved, screen = reads[:9]
        alive = reads[9:]
        left = [c for c, a in zip(self.coins, alive) if a is True]
        screen = screen if isinstance(screen, dict) else {}
        return {"_pose": pose, "_left": left, "coins": count, "coins_left": len(left),
                "door_open": door_solid is False, "door_transparency": door_tr,
                "reached_goal": reached is True, "lever_pulled": pulled is True,
                "lever_color": lever_col, "waved": waved if isinstance(waved, int) else 0, "hud_status": screen.get("Hud.Status"),
                "player_height": round(pose["at"].z) if isinstance(pose, dict) else None,
                "door": where(pose, self.door_at), "goal_pad": where(pose, self.goal_at),
                "lever": where(pose, self.lever_at), "sign": where(pose, self.sign_at),
                "tried": dict(self.tried)}

    def _count(self, name):
        self.tried[name] = self.tried.get(name, 0) + 1

    def _collect(self, coin):
        def run(game, obs):
            self._count("collect_coin")
            r = yield from walk_to(game, coin, within=20, timeout=12)
            yield from wait(game, 0.4)
            gone = not game.exists(coin)
            return Result(gone, "coin gone" if gone else "coin still there: %s" % r.why)
        return run

    def pull_lever(self, game, obs):
        self._count("pull_lever")
        r = yield from walk_to(game, "/Workspace/Lever", within=200, timeout=10)
        yield from look_at(game, "/Workspace/Lever", timeout=1.0)
        before = game.get("/Workspace/Lever", "Pulled")
        game.press("E")
        yield from wait(game, 0.6)
        after = game.get("/Workspace/Lever", "Pulled")
        return Result(bool(before) != bool(after), "Pulled %s -> %s, color %s" % (
            before, after, game.get("/Workspace/Lever", "Color")))

    def read_sign(self, game, obs):
        self._count("read_sign")
        r = yield from walk_to(game, "/Workspace/Sign", within=200, timeout=10)
        yield from look_at(game, "/Workspace/Sign", timeout=1.0)
        kids = game.call("/Workspace/Sign", "GetChildren")["ReturnValue"]
        return Result(True, "sign children %s; screen %s" % (kids, game.screen()))

    def _waved(self, game):
        try:
            return game.get(self.player, "Waved") or 0
        except Exception:
            return 0

    def wave(self, game, obs):
        self._count("wave")
        before = self._waved(game)
        game.press("Wave")
        yield from wait(game, 0.5)
        after = self._waved(game)
        return Result(after > before, "Waved %s -> %s" % (before, after))

    def try_door(self, game, obs):
        self._count("walk_through_door")
        r = yield from walk_to(game, "/Workspace/GoalPad", within=40, timeout=15)
        return Result(r.ok if hasattr(r, "ok") else bool(r), "goal walk: %s; at %s" % (r.why, game.pose()["at"].rounded()))

    def around_wall(self, side):
        def run(game, obs):
            self._count("around_wall_" + side)
            x = 1400 if side == "right" else -1400
            r = yield from walk_to(game, Vec(x, 2000, 112), within=150, timeout=15)
            r2 = yield from walk_to(game, Vec(x, 2600, 112), within=150, timeout=10)
            return Result(bool(r2), "end of wall: %s / behind: %s at %s" % (r.why, r2.why, game.pose()["at"].rounded()))
        return run

    def back_around(self, game, obs):
        self._count("return_around_wall")
        yield from walk_to(game, Vec(1400, 2600, 112), within=150, timeout=12)
        yield from walk_to(game, Vec(1400, 1900, 112), within=150, timeout=10)
        r = yield from walk_to(game, Vec(0, 1000, 112), within=200, timeout=12)
        return Result(game.pose()["at"].y < 2000, "back at %s" % game.pose()["at"].rounded())

    def jump_on_coin_spot(self, game, obs):
        self._count("jump_around")
        for _ in range(3):
            game.press("SpaceBar")
            yield from wait(game, 0.8)
        return Result(True, "z %s" % round(game.pose()["at"].z))

    def options(self, obs):
        at = obs["_pose"]["at"]
        ranked = sorted(obs["_left"], key=lambda c: self.where[c].dist(at))
        gained = lambda a, b: (b["coins"] or 0) > (a["coins"] or 0)
        opts = []
        if ranked:
            c = ranked[0]
            opts.append(Option("collect_nearest_coin", "Walk into the nearest coin to pick it up", self._collect(c),
                               effect=gained, facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c]}))
        opts.append(Option("pull_lever", "Walk to the lever and pull it with E (it toggles)", self.pull_lever,
                           facts={"lever": obs["lever"], "pulled": obs["lever_pulled"]},
                           effect=lambda a, b: a["lever_pulled"] != b["lever_pulled"]))
        opts.append(Option("read_sign", "Walk up to the sign and look at it", self.read_sign,
                           facts={"sign": obs["sign"]}, effect=lambda a, b: b["sign"]["distance"] < 300))
        opts.append(Option("wave", "Wave (F)", self.wave, facts={"waved": obs["waved"]},
                           effect=lambda a, b: b["waved"] > a["waved"]))
        opts.append(Option("walk_through_door_to_goal",
                           "Walk through the doorway in the wall onto the goal pad (tests whether the door blocks)",
                           self.try_door, facts={"door_open": obs["door_open"], "goal_pad": obs["goal_pad"]},
                           effect=lambda a, b: b["reached_goal"]))
        if at.y > 2240:
            opts.append(Option("return_around_wall", "Walk back around the right end of the wall to the coin field",
                               self.back_around, effect=lambda a, b: b["_pose"]["at"].y < 2000))
        elif not obs["reached_goal"]:
            opts.append(Option("go_around_wall_left", "Walk past the left end of the wall towards the goal pad",
                               self.around_wall("left"), effect=lambda a, b: b["goal_pad"]["distance"] < 700))
            opts.append(Option("go_around_wall_right", "Walk past the right end of the wall towards the goal pad",
                               self.around_wall("right"), effect=lambda a, b: b["goal_pad"]["distance"] < 700))
        opts.append(Option("jump_in_place", "Jump three times where the player stands", self.jump_on_coin_spot,
                           effect=None))
        return opts

    def status(self, obs):
        ev = {k: obs[k] for k in ("coins", "coins_left", "door_open", "door_transparency", "reached_goal",
                                  "lever_pulled", "lever_color", "waved", "hud_status")}
        return Status(evidence=ev)
