"""Stop-loss, take-profit and trailing-stop engine (pure, deterministic).

One implementation serves the price-level panel, saved trade plans (one bar at a time) and
backtests (a loop over the same ``step``), so they can never disagree.

Direction: LONG stops below / targets above the reference; SHORT the reverse. A level on the
wrong side is rejected, not flipped.
Tick rounding never adds risk or promises more: stops and targets both round TOWARDS the
reference price.
Bar order on daily data (high/low order is unknown):
  1. a stop-limit gapped through earlier rests as a limit order;
  2. open through the stop: stop-market fills at the OPEN (gap loss recorded); stop-limit
     fills at the open only inside its limit, otherwise rests (no fill);
  3. open through targets: filled at the TARGET price (not the better open);
  4. intrabar: if the stop AND a target are both touched, the STOP is assumed first and the
     bar is flagged AMBIGUOUS;
  5. after the bar: breakeven-after-first-target, trailing stop and time stop update and take
     effect from the NEXT bar (no look-ahead).
Linked legs are one-cancels-other: one remaining quantity; a stop fill cancels the targets; a
target fill reduces what the stop protects; the last fill closes the plan.
A stop is a trigger, never a guaranteed price: gaps, circuit limits, halts and illiquidity can
make fills worse or impossible.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from quant import indicators as ind

VERSION = "protection_v1"
DEFAULT_TICK = 0.05  # NSE cash equities


def sign(side: str) -> int:
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    return 1 if side == "LONG" else -1


def round_level(price: float, tick: float, side: str, kind: str) -> float:
    """kind 'stop' or 'target'; both round towards the reference price."""
    if tick <= 0:
        raise ValueError("tick must be positive")
    up = (side == "LONG") == (kind == "stop")
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 10)


def check_side(side: str, ref: float, price: float, kind: str) -> None:
    s = sign(side)
    bad = s * (ref - price) <= 0 if kind == "stop" else s * (price - ref) <= 0
    if bad:
        where = (("below" if s > 0 else "above") if kind == "stop"
                 else ("above" if s > 0 else "below"))
        raise ValueError(f"a {side} {kind} must be {where} the reference price {ref}")


def initial_stop(side: str, ref: float, o: ArrayLike, h: ArrayLike,
                 lo: ArrayLike, c: ArrayLike, method: str, value: float,
                 atr_period: int = 14, swing_lookback: int = 20, buffer_atr: float = 0.25,
                 vol_window: int = 20, tick: float = DEFAULT_TICK) -> dict[str, Any]:
    s = sign(side)
    detail: dict[str, Any] = {"method": method, "reference_price": ref, "timeframe": "1d"}

    def last_atr() -> float:
        a = ind.atr(h, lo, c, atr_period)
        v = float(a[-1]) if len(a) else float("nan")
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"ATR({atr_period}) needs at least {atr_period + 1} bars")
        return v

    if method == "FIXED_PRICE":
        raw = value
    elif method == "PERCENT":
        if not 0 < value < 1:
            raise ValueError("percent stop must be between 0 and 1")
        raw = ref * (1 - s * value)
    elif method == "ATR":
        a = last_atr()
        raw = ref - s * value * a
        detail |= {"atr": a, "atr_period": atr_period, "multiple": value}
    elif method == "SWING":
        a = last_atr()
        win = slice(-swing_lookback, None)
        swing = (float(np.min(np.asarray(lo)[win])) if s > 0
                 else float(np.max(np.asarray(h)[win])))
        raw = swing - s * buffer_atr * a
        detail |= {"swing_level": swing, "lookback": swing_lookback, "buffer_atr": buffer_atr}
    elif method == "VOLATILITY":
        cc = np.asarray(c, dtype=float)[-(vol_window + 1):]
        if len(cc) < vol_window + 1:
            raise ValueError(f"volatility stop needs {vol_window + 1} closes")
        dv = float(np.diff(np.log(cc)).std(ddof=1))
        raw = ref * (1 - s * value * dv)
        detail |= {"daily_volatility": dv, "window": vol_window, "multiple": value}
    else:
        raise ValueError(f"unknown stop method {method}")
    check_side(side, ref, raw, "stop")
    price = round_level(raw, tick, side, "stop")
    check_side(side, ref, price, "stop")
    return {"price": price, "unrounded": raw, "tick": tick, **detail}


def targets(side: str, ref: float, stop: float, cfgs: Sequence[dict[str, Any]],
            atr_value: float | None = None, tick: float = DEFAULT_TICK,
            min_reward_risk: float | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    s = sign(side)
    risk = s * (ref - stop)
    if risk <= 0:
        raise ValueError("stop is on the wrong side of the reference")
    if sum(float(c.get("fraction", 1.0)) for c in cfgs) > 1 + 1e-9:
        raise ValueError("target fractions add up to more than the position")
    out, warn = [], []
    for cfg in cfgs:
        m, v, frac = cfg["method"], float(cfg["value"]), float(cfg.get("fraction", 1.0))
        if m == "FIXED_PRICE":
            raw = v
        elif m == "PERCENT":
            raw = ref * (1 + s * v)
        elif m == "ATR":
            if atr_value is None:
                raise ValueError("ATR target needs an ATR value")
            raw = ref + s * v * atr_value
        elif m == "RISK_REWARD":
            raw = ref + s * v * risk
        else:
            raise ValueError(f"unknown target method {m}")
        check_side(side, ref, raw, "target")
        price = round_level(raw, tick, side, "target")
        rr = s * (price - ref) / risk
        if min_reward_risk is not None and rr < min_reward_risk:
            warn.append(f"target {price} is {rr:.2f}R, below the minimum {min_reward_risk}R")
        out.append({"price": price, "fraction": frac, "method": m, "reward_risk": round(rr, 4)})
    out.sort(key=lambda t: s * t["price"])
    return out, warn


def breakeven_price(side: str, entry: float, fee_per_share: float, slip_per_share: float,
                    tick: float = DEFAULT_TICK) -> float:
    """Exit price where the round trip (two fees, two slippages) nets to zero."""
    s = sign(side)
    raw = entry + s * 2 * (fee_per_share + slip_per_share)
    n = math.ceil(raw / tick - 1e-9) if s > 0 else math.floor(raw / tick + 1e-9)
    return round(n * tick, 10)


# --------------------------------------------------------------------------- plan state
@dataclass
class Leg:
    price: float
    fraction: float
    state: str = "ACTIVE"  # ACTIVE | TRIGGERED | CANCELLED


@dataclass
class PlanState:
    side: str
    entry: float
    quantity: float
    initial_stop: float
    stop: float
    targets: list[Leg]
    remaining: float = -1.0
    stop_state: str = "ACTIVE"  # ACTIVE | PENDING | TRIGGERED | CANCELLED
    stop_limit_offset: float | None = None
    resting_limit: float | None = None
    trail: dict[str, Any] | None = None  # method AMOUNT/PERCENT/ATR, value, activate_after_r
    trail_extreme: float | None = None
    trail_level: float | None = None
    breakeven_after_first_target: bool = False
    fee_per_share: float = 0.0
    slippage_bps: float = 0.0
    time_stop_bars: int | None = None
    bars_held: int = 0
    targets_filled: int = 0
    tick: float = DEFAULT_TICK
    approach_fraction: float = 0.01
    closed: bool = False

    def __post_init__(self) -> None:
        sign(self.side)
        if self.remaining < 0:
            self.remaining = self.quantity
        if self.trail_extreme is None:
            self.trail_extreme = self.entry

    @property
    def s(self) -> int:
        return sign(self.side)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> PlanState:
        d = dict(d)
        d["targets"] = [Leg(**t) for t in d.get("targets", [])]
        return cls(**d)


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class Event:
    kind: str  # FILL | STOP_LIMIT_NOT_FILLED | STOP_MOVED | AMBIGUOUS_BAR | APPROACHING | CLOSED
    rule: str
    day: date
    price: float | None = None
    quantity: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.rule}:{self.day.isoformat()}"


def _touch(st: PlanState, high: float, low: float, price: float, favourable: bool) -> bool:
    up = (st.side == "LONG") == favourable
    return high >= price if up else low <= price


def _fill(st: PlanState, ev: list[Event], bar: Bar, rule: str, px: float, qty: float,
          trigger: float | None, market: bool) -> None:
    fill = px * (1 - st.s * st.slippage_bps / 1e4) if market else px
    det: dict[str, Any] = {"trigger": trigger, "slippage": abs(fill - px) * qty}
    if rule == "STOP_GAP" and trigger is not None:
        det["gap_loss"] = st.s * (trigger - bar.open) * qty
    ev.append(Event("FILL", rule, bar.day, round(fill, 6), qty, det))
    st.remaining -= qty


def _close(st: PlanState, ev: list[Event], day: date, why: str) -> None:
    st.closed = True
    if st.stop_state in ("ACTIVE", "PENDING") and why != "stop":
        st.stop_state = "CANCELLED"
    for t in st.targets:
        if t.state == "ACTIVE":
            t.state = "CANCELLED"
    ev.append(Event("CLOSED", "PLAN", day, details={"reason": why}))


def _trail_update(st: PlanState, high: float, low: float, atr_value: float | None) -> float | None:
    t = st.trail
    if not t:
        return None
    s = st.s
    st.trail_extreme = (max(st.trail_extreme or st.entry, high) if s > 0
                        else min(st.trail_extreme or st.entry, low))
    risk = abs(st.entry - st.initial_stop)
    if s * (st.trail_extreme - st.entry) < float(t.get("activate_after_r", 0.0)) * risk:
        return st.trail_level
    m, v = t["method"], float(t["value"])
    if m == "AMOUNT":
        dist = v
    elif m == "PERCENT":
        dist = v * st.trail_extreme
    else:  # ATR: waits until an ATR value exists, never guesses
        if atr_value is None or not math.isfinite(atr_value):
            return st.trail_level
        dist = v * atr_value
    cand = round_level(st.trail_extreme - s * dist, st.tick, st.side, "stop")
    if st.trail_level is None or s * (cand - st.trail_level) > 0:
        st.trail_level = cand  # only tightens
    return st.trail_level


def step(st: PlanState, bar: Bar, atr_value: float | None = None) -> list[Event]:
    """Advance one bar. Mutates ``st``; returns what happened on this bar."""
    ev: list[Event] = []
    if st.closed or st.remaining <= 1e-12:
        return ev
    s = st.s
    o, h, lo, c = bar.open, bar.high, bar.low, bar.close
    st.bars_held += 1
    if st.resting_limit is not None:
        if _touch(st, h, lo, st.resting_limit, favourable=True):
            _fill(st, ev, bar, "STOP_LIMIT_RESTING_FILL", st.resting_limit, st.remaining,
                  st.stop, False)
            st.resting_limit, st.stop_state = None, "TRIGGERED"
            _close(st, ev, bar.day, "stop")
        else:
            ev.append(Event("STOP_LIMIT_NOT_FILLED", "STOP", bar.day,
                            details={"limit": st.resting_limit}))
        return ev
    if s * (st.stop - o) >= 0:  # opened through the stop
        if st.stop_limit_offset is None:
            _fill(st, ev, bar, "STOP_GAP", o, st.remaining, st.stop, True)
            st.stop_state = "TRIGGERED"
            _close(st, ev, bar.day, "stop")
            return ev
        limit = st.stop - s * st.stop_limit_offset
        if s * (o - limit) >= 0:
            _fill(st, ev, bar, "STOP_GAP", o, st.remaining, st.stop, False)
            st.stop_state = "TRIGGERED"
            _close(st, ev, bar.day, "stop")
            return ev
        st.resting_limit, st.stop_state = limit, "PENDING"
        ev.append(Event("STOP_LIMIT_NOT_FILLED", "STOP", bar.day,
                        details={"limit": limit, "open": o,
                                 "note": "opened through the stop-limit; resting as a limit"}))
        return ev
    active = [t for t in st.targets if t.state == "ACTIVE"]

    def take(t: Leg) -> None:
        q = min(st.remaining, st.quantity * t.fraction)
        st.targets_filled += 1
        _fill(st, ev, bar, f"TARGET_{st.targets.index(t) + 1}", t.price, q, t.price, False)
        t.state = "TRIGGERED"
        active.remove(t)

    while active and s * (o - active[0].price) >= 0:  # gap through targets
        take(active[0])
    if st.remaining <= 1e-12:
        _close(st, ev, bar.day, "targets")
        return ev
    stop_hit = _touch(st, h, lo, st.stop, favourable=False)
    target_hit = bool(active) and _touch(st, h, lo, active[0].price, favourable=True)
    if stop_hit and target_hit:
        ev.append(Event("AMBIGUOUS_BAR", "STOP", bar.day,
                        details={"note": "stop and target both touched; stop assumed first"}))
    if stop_hit:
        rule = "TRAIL" if st.trail_level is not None and st.stop == st.trail_level else "STOP"
        px = st.stop if st.stop_limit_offset is None else st.stop - s * st.stop_limit_offset
        _fill(st, ev, bar, rule, px, st.remaining, st.stop, st.stop_limit_offset is None)
        st.stop_state = "TRIGGERED"
        _close(st, ev, bar.day, "stop")
        return ev
    while active and _touch(st, h, lo, active[0].price, favourable=True):
        take(active[0])
        if st.breakeven_after_first_target and st.targets_filled == 1:
            be = breakeven_price(st.side, st.entry, st.fee_per_share,
                                 st.entry * st.slippage_bps / 1e4, st.tick)
            if s * (be - st.stop) > 0:
                ev.append(Event("STOP_MOVED", "STOP", bar.day, be,
                                details={"from": st.stop, "reason": "breakeven after target 1",
                                         "effective": "next bar"}))
                st.stop = be
    if st.remaining <= 1e-12:
        _close(st, ev, bar.day, "targets")
        return ev
    lvl = _trail_update(st, h, lo, atr_value)
    if lvl is not None and s * (lvl - st.stop) > 0:
        ev.append(Event("STOP_MOVED", "TRAIL", bar.day, lvl,
                        details={"from": st.stop, "effective": "next bar"}))
        st.stop = lvl
    if st.time_stop_bars is not None and st.bars_held >= st.time_stop_bars:
        _fill(st, ev, bar, "TIME", c, st.remaining, None, True)
        st.stop_state = "CANCELLED"
        _close(st, ev, bar.day, "time stop")
        return ev
    if st.approach_fraction > 0:
        if abs(c - st.stop) <= st.approach_fraction * c:
            ev.append(Event("APPROACHING", "STOP", bar.day, st.stop, details={"close": c}))
        if active and abs(active[0].price - c) <= st.approach_fraction * c:
            ev.append(Event("APPROACHING", f"TARGET_{st.targets.index(active[0]) + 1}", bar.day,
                            active[0].price, details={"close": c}))
    return ev


def is_risk_increase(st: PlanState, new_stop: float | None = None,
                     new_trail: dict[str, Any] | None = None) -> list[str]:
    out = []
    if new_stop is not None and st.s * (st.stop - new_stop) > 1e-12:
        out.append(f"stop moves farther away ({st.stop} -> {new_stop})")
    if new_trail is not None and st.trail is not None:
        if not new_trail:
            out.append("trailing stop removed")
        elif new_trail.get("method") == st.trail.get("method") and \
                float(new_trail["value"]) > float(st.trail["value"]):
            out.append(f"trailing distance widens ({st.trail['value']} -> {new_trail['value']})")
    return out


def validate_levels(st: PlanState, price: float) -> None:
    if st.s * (price - st.stop) <= 0:
        raise ValueError(f"stop {st.stop} is not on the protective side of price {price}")
    for t in st.targets:
        if t.state == "ACTIVE" and st.s * (t.price - price) <= 0:
            raise ValueError(f"target {t.price} is not beyond price {price}")
    if sum(t.fraction for t in st.targets) > 1 + 1e-9:
        raise ValueError("target fractions add up to more than the position")


def price_panel(current: float, st: PlanState, draft: bool = False,
                paused: bool = False) -> dict[str, Any]:
    """Exactly four fields. No quantity, capital, margin or money figures."""
    def state(x: str) -> str:
        if draft and x == "ACTIVE":
            return "SUGGESTED"
        if paused and x in ("ACTIVE", "PENDING"):
            return "PAUSED"
        return x
    nxt = next((t for t in st.targets if t.state == "ACTIVE"),
               st.targets[-1] if st.targets else None)
    trail = (({"price": st.trail_level, "state": state("ACTIVE")} if st.trail_level is not None
              else {"price": None, "state": "PENDING"}) if st.trail
             else {"price": None, "state": "CANCELLED"})
    return {"current_price": current,
            "stop_loss": {"price": st.stop, "state": state(st.stop_state)},
            "target_price": {"price": nxt.price if nxt else None,
                             "state": state(nxt.state) if nxt else "CANCELLED"},
            "trailing_stop_loss": trail}


def simulate(st: PlanState, bars: Sequence[Bar], atrs: Sequence[float | None] | None = None
             ) -> list[Event]:
    """Backtest one protected position: the same ``step`` the live plans use."""
    out: list[Event] = []
    for i, b in enumerate(bars):
        out += step(st, b, atrs[i] if atrs is not None else None)
        if st.closed:
            break
    return out
