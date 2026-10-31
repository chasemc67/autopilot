from autopilot.kit import *


class CoinsAndGoalHarness(Harness):
    goal = "Pick up every coin (walk into it), then walk through the door (open at 5+ coins) onto the green goal pad"
    tempo = 0.0           # decide only when a walk ends: coin ranking shifts as the player walks (notes.md)
    stall_seconds = 30

    def setup(self, game):
        self.coins = game.call("/Workspace/Coins", "GetChildren")["ReturnValue"]
        self.where = game.locs(self.coins)
        self.value = {c: (game.get(c, "Value") or 1) for c in self.coins}
        self.total = sum(self.value.values())
        self.player = "/Players/%s" % game.name_of(game.pawn)
        self.stats = self.player + "/leaderstats"
        self.door_at = game.loc("/Workspace/Door")
        self.goal_at = game.loc("/Workspace/GoalPad")
        self.plan = [
            Step("collect_coins", "Pick up all %d coins" % len(self.coins), lambda o: o["coins_left"] == 0,
                 options=["collect_nearest_coin", "collect_second_nearest_coin", "collect_most_valuable_coin"]),
            Step("reach_goal", "Stand on the goal pad past the door", lambda o: o["reached_goal"],
                 options=["walk_to_door", "walk_to_goal_pad"]),
        ]

    def observe(self, game):
        reads = game.batch([lambda: game.pose(), lambda: game.get(self.stats, "Coins"),
                            lambda: game.get(self.player, "ReachedGoal"),
                            lambda: game.get("/Workspace/Door", "CanCollide")]
                           + [(lambda c=c: game.exists(c)) for c in self.coins])
        pose, count, reached, door_solid, alive = reads[0], reads[1], reads[2], reads[3], reads[4:]
        left = [c for c, a in zip(self.coins, alive) if a is True]
        return {"_pose": pose, "_left": left, "coins": count, "coins_total_value": self.total,
                "coins_left": len(left), "door_open": door_solid is False, "reached_goal": reached is True,
                "door": where(pose, self.door_at), "goal_pad": where(pose, self.goal_at)}

    def _collect(self, coin):
        def run(game, obs):
            r = yield from walk_to(game, coin, within=20, timeout=12)
            yield from wait(game, 0.4)
            gone = not game.exists(coin)
            return Result(gone, "coin gone" if gone else "coin still there: %s" % r.why)
        return run

    def options(self, obs):
        at = obs["_pose"]["at"]
        ranked = sorted(obs["_left"], key=lambda c: self.where[c].dist(at))
        gained = lambda a, b: (b["coins"] or 0) > (a["coins"] or 0)
        opts = []
        def coin_opt(name, desc, c):
            opts.append(Option(name, desc, self._collect(c), effect=gained,
                               facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c]}))
        if ranked:
            coin_opt("collect_nearest_coin", "Walk into the nearest coin to pick it up", ranked[0])
            c = max(ranked, key=lambda x: self.value[x])
            coin_opt("collect_most_valuable_coin", "Walk into the coin worth the most to pick it up", c)
        if len(ranked) > 1:
            coin_opt("collect_second_nearest_coin", "Walk into the second nearest coin to pick it up", ranked[1])
        if not ranked:
            opts.append(Option("walk_to_door", "Walk up to the doorway in the wall (passable once open)",
                               lambda g, o: walk_to(g, self.door_at, within=150, timeout=15),
                               facts={"door": obs["door"], "door_open": obs["door_open"]},
                               effect=lambda a, b: b["door"]["distance"] < a["door"]["distance"]))
            opts.append(Option("walk_to_goal_pad", "Walk through the open door onto the goal pad",
                               lambda g, o: walk_to(g, "/Workspace/GoalPad", within=40, timeout=20),
                               facts={"goal_pad": obs["goal_pad"], "door_open": obs["door_open"]},
                               effect=lambda a, b: b["reached_goal"]))
        return opts

    def status(self, obs):
        ev = {"coins": obs["coins"], "coins_left": obs["coins_left"], "door_open": obs["door_open"],
              "reached_goal": obs["reached_goal"]}
        if obs["reached_goal"] and obs["coins_left"] == 0:
            return Status("done", "all coins picked up and the goal pad reached", ev)
        return Status(evidence=ev)
