# Dip Ladder (Recovery Turn) — BTC mean-reversion bot

[![make.fun bounty](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/pikyoe/makefun-btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/pikyoe/makefun-btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

A multi-leg mean-reversion dip ladder for the make.fun BTC race
(`btc-race-24h-20261118-1444`). It buys deep dips below the 60-bar high in
up to three full-size legs, exits each leg at +200bps or on a two-stage
trailing stop, and sits out waterfall regimes. See `STRATEGY.md` for the
full rules and the three-window validation (+14.42 / +32.26 / +7.15 USDC,
max drawdown ≤ 4.4%).

## Run

```bash
python3 main.py
```

The bot speaks the `agent-fun/bars-replay/v1` protocol on stdin/stdout
(one JSON line per minute bar, orders as JSON replies). Python 3.13,
standard library only plus the vendored `vendor/makefun_bars.py` shim.
Deterministic: integer bps comparisons, no clock, no network, no
randomness.
