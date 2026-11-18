# STRATEGY.md — Dip Ladder (Recovery Turn)

Round: `btc-race-24h-20261118-1444`. Entry: `main.py` (Python 3.13, stdlib
only + vendored `vendor/makefun_bars.py`).

## Idea

The market mean-reverts over 30–120 minutes: a deep dip below the 60-bar
high usually bounces, and the deeper the dip the stronger the bounce. The
bot scales into dips in up to three full-size legs (a ladder), each leg with
its own take-profit and two-stage trailing stop, and sits out when the
60-bar trend is positive or the 240-bar trend is deeply negative (waterfall
regime). Two filters buy the turn, not the fall: the 5-minute momentum must
not be negative, and the close must have recovered 15bps above the 20-bar
low.

This is the same ladder as the Dip Ladder entry that scored +32.46 on the
2026-11-17 dev window, plus one validated change: `REC_BPS 0 -> 15`.

## Rules

Entry (all must hold):

- `dip = (60-bar high − close) / 60-bar high ≥ 100bps`
- `trend60 ≤ 0bps` — no buying into a rising market's pullback
- `trend240 ≥ −150bps` — regime filter; blocks waterfall dips
- `mom5 ≥ 0bps` — don't buy while the price is still falling over 5 bars
- `recovery ≥ 15bps` above the 20-bar low — buys the turn, not the fall
- Volatility sizing: realised 60-bar 1-min stdev above 8bps shrinks the leg
  (factor `8²/vol²`, floored at 50%)
- Ladder spacing: a new leg needs the dip to have deepened by 40bps since
  the last leg, or 10 bars to have passed
- No new legs after bar 1320 (last 2 hours)
- Fallback: if no buy has filled by bar 1080, take a relaxed trade
  (dip ≥60bps, trend60 ≤ +50bps, half size) so the entry still makes ≥1
  trade (the race guardrail)

Exit (per leg, checked every bar):

- Take-profit: close ≥ entry × (1 + 200bps)
- Two-stage trailing stop: no stop while the leg's peak gain is below
  +140bps (a dip leg may ride through its wobble); once the peak gain
  reaches +140bps, a tight 20bps trail below the peak engages
- Drawdown guard: if equity falls 4% below its peak, no new buys

## Validation (exact offline simulator)

`research/sim.py` replicates the kit's `sim/bars/engine.ts` fill math with
Python ints. Validated against the kit's `grade-local` (process driver) on
the previous round's window for three reference strategies — buy-and-hold,
momentum, rsi-bollinger — all matching to the micro-USDC. The same
simulator was re-run here on three 24-hour windows:

| window | character | net PnL | trades | max drawdown |
|---|---|---|---|---|
| **Dev, this round (Nov 17–18)** | range-bound, drifts −281bps | **+14.42 USDC** | 4 | 1.50% |
| Nov 16–17 (prev round's dev) | range-bound, +48bps | **+32.26 USDC** | 6 | 0.74% |
| Nov 14–15 (crash day) | falling, −401bps, stdev 12bps | **+7.15 USDC** | 5 | 4.36% |

All three clear the dev gate (net PnL > 0) and are far under the 25%
max-drawdown limit. Mean +17.94, worst window +7.15.

## What was tried and rejected (all on the same simulator)

- EMA-cross / momentum / RSI-Bollinger / grid scalps with hard stops: all
  lose money on at least one window (EMA-cross −93 to −118, RSI-BB −72 to
  −127, grids −32 to −47 on the crash day). High-frequency scalping bleeds
  on waterfall days.
- Deeper first dip (150–220bps): better on this dev window (+15.90 at 150)
  but collapses the Nov 16–17 window (+32.46 → +13.69 at 150, → −0.15 at
  200). The 100bps entry is where the money is.
- Looser exits (TP 250–300, later trail arm): the trail arm at 140bps is
  load-bearing — arming at 160–180 flips the crash day negative.
- Shallow-dip range scalping (dip ≥60–80 with a flat-trend filter): zero
  trades on this dev window, −35 on the crash day.
- Removing the momentum filter: 323 trades and a loss on the crash day.
  The filters exist for a reason.

The live dev leaderboard for this round shows the top 4 at +$86–92 with
343–419 trades ("DP schedule" scalpers) and a dip-buyer cluster at +$24–31
with 2–8 trades. The DP scalpers' dev scores are almost certainly
dev-window overfit (a DP-fitted schedule cannot know the race window's
bars); the dip cluster is the robust family this entry belongs to. This
bot is tuned for the race window it has never seen, not for the dev
window: it never loses on any of the three windows tested, including a
crash day.

## Honest caveats

- Only 4–6 trades per window: the result is concentrated in a few swings.
  A race day with no dip ≥100bps means few or no trades (the fallback
  guarantees at least one).
- The 20bps tight trail is the single most valuable parameter; it assumes
  1-minute noise stays near the dev windows' ~6.5bps stdev. On a much
  noisier race day the trail may trigger early.
- `REC_BPS = 15` sits at the edge of a plateau (10–15 all work, 18 fails).
  It was chosen because it improves both the mean and the worst window,
  not because 15 is magic.
- A race day that trends hard down for hours will trade little or nothing
  by design (survive) rather than chase the waterfall.
