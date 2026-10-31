from autopilot.kit import *


class CoinTouchHarness(Harness):
    goal = "Walk into coins to pick them up; each coin touched should raise the leaderstats Coins count by its value"
    tempo = 0.0           # decide only when a walk to a coin ends: the targets' ranking shifts as the player walks
    stall_seconds = 30

    def setup(self, game):
        self.coins = game.call("/Workspace/Coins", "GetChildren")["ReturnValue"]
        self.where = game.locs(self.coins)
        self.value = {c: (game.get(c, "Value") or 1) for c in self.coins}
        self.stats = "/Players/%s/leaderstats" % game.name_of(game.pawn)
        self.pickups = []          # each coin seen gone: its value and the Coins change at that moment
        self.last = None
        self.gone = set()

    def resume(self, game, progress):
        # Coins already gone before this episode were picked up in the last one (Coin1: 0 -> 1, from trace1)
        for c in self.coins:
            if not game.exists(c):
                self.gone.add(c)

    def observe(self, game):
        reads = game.batch([lambda: game.pose(), lambda: game.get(self.stats, "Coins")]
                           + [(lambda c=c: game.exists(c)) for c in self.coins])
        pose, count, alive = reads[0], reads[1], reads[2:]
        left = [c for c, a in zip(self.coins, alive) if a is True]
        if self.last is None:
            self.last = count
        for c, a in zip(self.coins, alive):
            if a is not True and c not in self.gone:
                self.gone.add(c)
                self.pickups.append({"coin": game.name_of(c), "value": self.value[c], "coins_before": self.last,
                                     "coins_after": count, "t": round(game.t, 2)})
        if isinstance(count, (int, float)):
            self.last = count
        return {"_pose": pose, "_left": left, "leaderstats_coins": count, "coins_left": len(left),
                "coins_picked_up": len(self.pickups)}

    def _ranked(self, obs):
        at = obs["_pose"]["at"]
        return sorted(obs["_left"], key=lambda c: self.where[c].dist(at))

    def _collect(self, coin):
        def run(game, obs):
            r = yield from walk_to(game, coin, within=20, timeout=12)
            yield from wait(game, 0.4)
            return Result(not game.exists(coin), "coin gone" if not game.exists(coin) else "coin still there: %s" % r.why)
        return run

    def options(self, obs):
        ranked = self._ranked(obs)
        opts = []
        gained = lambda a, b: (b["leaderstats_coins"] or 0) > (a["leaderstats_coins"] or 0)
        if ranked:
            c = ranked[0]
            opts.append(Option("collect_nearest_coin", "Walk into the nearest coin to pick it up", self._collect(c),
                               facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c]}, effect=gained))
        if len(ranked) > 1:
            c = ranked[1]
            opts.append(Option("collect_second_nearest_coin", "Walk into the second nearest coin to pick it up",
                               self._collect(c), facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c]},
                               effect=gained))
        if ranked:
            c = max(ranked, key=lambda x: self.value[x])
            opts.append(Option("collect_most_valuable_coin", "Walk into the coin worth the most to pick it up",
                               self._collect(c), facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c]},
                               effect=gained))
        return opts

    def status(self, obs):
        ev = {"pickups": list(self.pickups), "leaderstats_coins": obs["leaderstats_coins"]}
        bad = [p for p in self.pickups if (p["coins_after"] or 0) - (p["coins_before"] or 0) < p["value"]]
        if bad:
            return Status("done", "a coin was touched and Coins did not rise by its value", ev)
        if len(self.pickups) >= 4:
            return Status("done", "coins touched; Coins rose by each one's value", ev)
        return Status(evidence=ev)
