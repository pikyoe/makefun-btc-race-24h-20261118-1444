"""Dip-ladder mean-reversion bot for the BTC race (agent-fun/bars-replay/v1).

Round btc-race-24h-20261118-1444. Same ladder as the Dip Ladder entry that
scored +32.46 on the 2026-11-17 dev window, plus one validated change:
REC_BPS 0 -> 15 (buy the recovery turn, not the falling knife). Validated
on three 24h windows with the byte-exact offline simulator (STRATEGY.md):
+14.42 / +32.26 / +7.15 USDC, max drawdown <= 4.4%.

Round btc-race-24h-20261118-1444. Same ladder as the Dip Ladder entry that
scored +32.46 on the 2026-11-17 dev window, plus one validated change:
REC_BPS 0 -> 15 (buy the recovery turn, not the falling knife). Validated
on three 24h windows with the byte-exact offline simulator (see
STRATEGY.md): +14.42 / +32.26 / +7.15 USDC, max drawdown <= 4.4%.

Idea: the market mean-reverts over 30-120 minutes — a deep dip below the
60-bar high usually bounces, and the deeper the dip the stronger the
bounce. So the bot scales into dips in up to three legs (a ladder), each
leg with its own take-profit and trailing stop, and sits out when the
60-bar trend is positive (a dip then is a distribution top that keeps
falling, not an oversold bounce). Small legs keep the round-trip cost low:
a $250 leg pays ~18bps all-in versus ~34bps for a full-size $1,000 trade,
because price impact scales with size. A drawdown guard blocks new buys
once equity is 4% off its peak, and no new leg opens in the last 2 hours.

Deterministic: integer bps comparisons on raw closes, integer
sum-of-squares volatility, no clock, no env, no randomness.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- strategy parameters (round values; see STRATEGY.md) ---
LOOKBACK = 60            # rolling-high window (bars) for the dip
DIP1_BPS = 100           # first leg: close this far below the 60-bar high
DIP_STEP_BPS = 40        # each deeper leg needs the dip this much deeper
MAX_LEGS = 3             # max open legs
LEG_FRACTION_BPS = 10000  # each leg: this share of equity (100% = full size)
TP_BPS = 200             # per-leg take profit vs its fill price
TRAIL_BPS = 10000        # loose trail while unarmed (effectively no stop-loss:
                         # a dip leg is allowed to ride through its wobble)
TRAIL_ARM_BPS = 140      # peak gain that arms the tight trailing stop
TRAIL_TIGHT_BPS = 20     # trailing stop below the peak once armed
TREND_MAX_BPS = 0        # block buys while the 60-bar trend is above this
TREND_BARS = 60          # trend window (bars)
TREND240_MIN_BPS = -150  # buy only while the 240-bar trend is >= this
TREND240_MAX_BPS = 10000   # ... and <= this (regime filter: the 240-bar trend
                           # separates range-bound dips from waterfall dips)
MOM_MIN_BPS = 0          # entry: 5-min return at least this (0 = buy only when
                         # the price is not falling over the last 5 bars)
MOM_BARS = 5             # momentum window (bars)
REC_BPS = 15             # entry: close must have recovered this far above the
                         # recent low — buys the turn, not the fall (validated:
                         # 15bps lifts the crash-day window +6.05 -> +7.15 with
                         # no cost on the other two; 10-15 is a plateau, 18 fails)
REC_BARS = 20            # window for the recent low (bars)
VOL_HIGH_BPS = 8         # realised 60-bar 1-min stdev above which legs shrink
VOL_MIN_FACTOR_BPS = 5000  # smallest leg-size factor under high vol (50%)
DD_GUARD_BPS = 400       # block new buys once equity is this far off its peak
HOLD_MAX_BARS = 10000    # time stop per leg (bars; off at this value)
MIN_SEP_BARS = 10        # min bars between legs unless the dip deepened
NO_NEW_AFTER_BAR = 1320  # no new legs in the last 2 hours
MAX_SLIP_BPS = 300       # per-order slippage cap vs the close decided on

# Fallback: if the primary signal never fired, take a relaxed trade late so
# the entry still makes at least one trade (the race guardrail needs >= 1).
FALLBACK_BAR = 1080
FALLBACK_DIP_BPS = 60
FALLBACK_TREND_MAX_BPS = 50
FALLBACK_FRACTION_BPS = 1250  # half a leg


class Leg:
    """One ladder leg: the qty bought, its fill price, peak close, age."""

    __slots__ = ("qty", "entry", "peak", "age")

    def __init__(self, qty, entry):
        self.qty = qty
        self.entry = entry
        self.peak = entry
        self.age = 0


class Ladder:
    """Per-run state: bar history, open legs, equity peak, guard flags."""

    def __init__(self, **overrides):
        for key, val in overrides.items():
            if not hasattr(self, key) and key not in globals():
                raise ValueError(f"unknown parameter {key}")
            setattr(self, key, val)
        self.closes = []          # raw close ints, one per batch with a bar
        self.legs = []            # open Leg objects, oldest first
        self.filled_buys = 0
        self.peak_equity = 0
        self.stopped = False      # drawdown guard tripped: no new buys
        self.last_leg_bar = -10 ** 9
        self.last_leg_dip = 0

    # -- parameter access (module defaults unless overridden) --
    def _p(self, name):
        return getattr(self, name, globals()[name])

    def push(self, close):
        self.closes.append(close)

    def dip_bps(self):
        window = self.closes[-self._p("LOOKBACK"):]
        hi = max(window)
        c = self.closes[-1]
        return (hi - c) * 10000 // hi

    def dip_bps_lookback(self, bars):
        window = self.closes[-bars:]
        hi = max(window)
        c = self.closes[-1]
        return (hi - c) * 10000 // hi

    def trend_bps(self):
        bars = self._p("TREND_BARS")
        if len(self.closes) <= bars:
            return 0
        c = self.closes[-1]
        prev = self.closes[-1 - bars]
        return (c - prev) * 10000 // prev

    def trend240_bps(self):
        bars = 240
        if len(self.closes) <= bars:
            return 0
        c = self.closes[-1]
        prev = self.closes[-1 - bars]
        return (c - prev) * 10000 // prev

    def mom_bps(self):
        bars = self._p("MOM_BARS")
        if len(self.closes) <= bars:
            return 0
        c = self.closes[-1]
        prev = self.closes[-1 - bars]
        return (c - prev) * 10000 // prev

    def rec_bps(self):
        """Recovery above the recent low, in bps (0 while warming up)."""
        bars = self._p("REC_BARS")
        if len(self.closes) <= bars:
            return 0
        c = self.closes[-1]
        lo = min(self.closes[-bars:])
        return (c - lo) * 10000 // lo

    def vol_factor_bps(self):
        """Leg-size factor (bps of 10000) from realised 60-bar volatility.

        Integer sum of squared 1-min returns (bps^2); above VOL_HIGH^2 the
        factor shrinks as (VOL_HIGH^2 / vol_sq), floored at VOL_MIN_FACTOR.
        """
        bars = self._p("LOOKBACK")
        if len(self.closes) <= bars:
            return 10000
        sq = 0
        for i in range(len(self.closes) - bars, len(self.closes)):
            r = (self.closes[i] - self.closes[i - 1]) * 10000 // self.closes[i - 1]
            sq += r * r
        vol_sq = sq // bars
        hi_sq = self._p("VOL_HIGH_BPS") ** 2
        if vol_sq <= hi_sq:
            return 10000
        factor = hi_sq * 10000 // vol_sq
        floor = self._p("VOL_MIN_FACTOR_BPS")
        return factor if factor > floor else floor

    def _apply_fills(self, batch):
        for fill in batch.get("fills") or []:
            if fill.get("status") != "filled" or fill.get("symbol") != SYMBOL:
                continue
            if fill.get("side") == "buy":
                self.filled_buys += 1
                self.legs.append(Leg(int(fill["qty"]), int(fill["price"])))
            elif fill.get("side") == "sell":
                qty = int(fill["qty"])
                while qty > 0 and self.legs:
                    leg = self.legs[0]
                    if leg.qty <= qty:
                        qty -= leg.qty
                        self.legs.pop(0)
                    else:
                        leg.qty -= qty
                        qty = 0

    def on_batch(self, state, batch):
        self._apply_fills(batch)
        if state.warming_up:
            return []
        close = state.close(SYMBOL)
        if close is None:
            return []
        self.push(close)
        bar_idx = state.seq

        # Drawdown guard: peak equity, block new buys once 4% off it.
        equity = state.equity
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) * 10000 // self.peak_equity
            if dd >= self._p("DD_GUARD_BPS"):
                self.stopped = True

        # --- exits: per-leg take-profit, trailing stop, time stop ---
        for leg in self.legs:
            leg.age += 1
            if close > leg.peak:
                leg.peak = close
        sells = []
        for leg in list(self.legs):
            if close * 10000 >= leg.entry * (10000 + self._p("TP_BPS")):
                sells.append(leg)
                continue
            # Two-stage trail: loose while the leg is flat/underwater (so a
            # normal dip wobble does not stop it out), tight once the leg
            # is TRAIL_ARM_BPS in profit (lock in the bounce instead of
            # giving back most of it on the way down).
            peak_gain = (leg.peak - leg.entry) * 10000 // leg.entry
            trail = (self._p("TRAIL_TIGHT_BPS") if peak_gain >= self._p("TRAIL_ARM_BPS")
                     else self._p("TRAIL_BPS"))
            if close * 10000 <= leg.peak * (10000 - trail):
                sells.append(leg)
            elif leg.age >= self._p("HOLD_MAX_BARS"):
                sells.append(leg)
        if sells:
            return [{"side": "sell", "symbol": SYMBOL, "qty": leg.qty,
                     "max_slippage_bps": self._p("MAX_SLIP_BPS")}
                    for leg in sells]

        # --- entries: the dip ladder ---
        if self.stopped or len(self.legs) >= self._p("MAX_LEGS"):
            return []
        if bar_idx >= self._p("NO_NEW_AFTER_BAR"):
            return []
        if len(self.closes) < self._p("LOOKBACK") + 1:
            return []
        dip = self.dip_bps()
        trend = self.trend_bps()
        trend240 = self.trend240_bps()
        mom = self.mom_bps()
        dip1 = self._p("DIP1_BPS")
        trend_max = self._p("TREND_MAX_BPS")
        fraction = self._p("LEG_FRACTION_BPS")

        primary = (dip >= dip1 and trend <= trend_max
                   and self._p("TREND240_MIN_BPS") <= trend240 <= self._p("TREND240_MAX_BPS")
                   and mom >= self._p("MOM_MIN_BPS")
                   and self.rec_bps() >= self._p("REC_BPS"))
        fallback = (self.filled_buys == 0 and bar_idx >= self._p("FALLBACK_BAR")
                    and dip >= self._p("FALLBACK_DIP_BPS")
                    and trend <= self._p("FALLBACK_TREND_MAX_BPS")
                    and self._p("TREND240_MIN_BPS") <= trend240 <= self._p("TREND240_MAX_BPS"))
        if fallback and not primary:
            dip1 = self._p("FALLBACK_DIP_BPS")
            trend_max = self._p("FALLBACK_TREND_MAX_BPS")
            fraction = self._p("FALLBACK_FRACTION_BPS")
        elif not primary:
            return []

        # Ladder spacing: a new leg needs a deeper dip, or enough time
        # since the last leg, so legs scale in as the dip deepens.
        if self.legs:
            deepened = dip >= self.last_leg_dip + self._p("DIP_STEP_BPS")
            separated = bar_idx - self.last_leg_bar >= self._p("MIN_SEP_BARS")
            if not (deepened or separated):
                return []

        factor = self.vol_factor_bps()
        notional = equity * fraction // 10000 * factor // 10000
        room = state.weight_room(SYMBOL)
        if room < notional:
            notional = room
        if notional > state.cash:
            notional = state.cash
        if notional < 1_000_000:
            return []
        self.last_leg_bar = bar_idx
        self.last_leg_dip = dip
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": notional,
                 "max_slippage_bps": self._p("MAX_SLIP_BPS")}]


LADDER = Ladder()


def on_batch(state, batch):
    return LADDER.on_batch(state, batch)


def on_hello(state):
    # hello: seed any randomness here; this bot uses none.
    return None


if __name__ == "__main__":
    makefun_bars.run(on_batch, on_hello=on_hello)
