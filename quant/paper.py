"""Paper order simulation on daily bars (pure). There is no live execution anywhere.

An order placed on day d is first eligible on the next session (d's close has passed).
Per eligible bar:
  MARKET      fills at the open, adverse slippage applied
  LIMIT       BUY fills if low <= limit at min(open, limit); SELL if high >= limit at
              max(open, limit)
  STOP        BUY triggers if high >= stop (SELL: low <= stop); fills at the stop, or at the
              open when the bar opens beyond it (gap); then like MARKET
  STOP_LIMIT  after the trigger, behaves as LIMIT at its limit price (may never fill)
Quantity per bar is capped at ``max_participation`` of the bar's volume; the rest keeps
working (PARTIALLY_FILLED). DAY orders expire after their first eligible session, GTC after
``gtc_sessions``. Order states: NEW -> WORKING -> PARTIALLY_FILLED -> FILLED; or CANCELLED,
EXPIRED, REJECTED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

TRANSITIONS = {
    "NEW": {"WORKING", "REJECTED", "CANCELLED"},
    "WORKING": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED"},
}
FINAL = ("FILLED", "CANCELLED", "EXPIRED", "REJECTED")


def can_move(cur: str, new: str) -> bool:
    return new in TRANSITIONS.get(cur, set())


@dataclass
class SimOrder:
    side: str  # BUY | SELL
    quantity: float
    filled: float
    order_type: str
    limit: float | None = None
    stop: float | None = None
    tif: str = "DAY"
    triggered: bool = False
    sessions: int = 0

    @property
    def remaining(self) -> float:
        return self.quantity - self.filled


@dataclass(frozen=True)
class DayBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class SimFill:
    day: date
    quantity: float
    price: float
    note: str = ""


@dataclass
class StepResult:
    fill: SimFill | None = None
    triggered_now: bool = False
    expired: bool = False
    notes: list[str] = field(default_factory=list)


def step(o: SimOrder, bar: DayBar, slippage_bps: float = 5.0, max_participation: float = 0.10,
         gtc_sessions: int = 90, lot: float = 1.0) -> StepResult:
    res = StepResult()
    if o.remaining <= 0:
        return res
    o.sessions += 1
    buy = o.side == "BUY"
    slip = 1 + (slippage_bps / 1e4 if buy else -slippage_bps / 1e4)
    px: float | None = None
    if o.order_type in ("STOP", "STOP_LIMIT") and not o.triggered:
        assert o.stop is not None
        hit = bar.high >= o.stop if buy else bar.low <= o.stop
        if hit:
            o.triggered = res.triggered_now = True
            if o.order_type == "STOP":
                gap = bar.open >= o.stop if buy else bar.open <= o.stop
                px = (bar.open if gap else o.stop) * slip
                if gap:
                    res.notes.append("opened beyond the stop: filled at the open")
    if px is None:
        kind = o.order_type
        if kind == "STOP" and o.triggered and not res.triggered_now:
            kind = "MARKET"
        if kind == "STOP_LIMIT" and o.triggered:
            kind = "LIMIT"
        if kind == "MARKET":
            px = bar.open * slip
        elif kind == "LIMIT":
            assert o.limit is not None
            if buy and bar.low <= o.limit:
                px = min(bar.open, o.limit)
            elif not buy and bar.high >= o.limit:
                px = max(bar.open, o.limit)
    if px is not None and bar.volume > 0:
        cap = max_participation * bar.volume
        qty = min(o.remaining, cap)
        qty = (qty // lot) * lot
        if qty > 0:
            o.filled += qty
            res.fill = SimFill(bar.day, qty, round(px, 4),
                               "participation cap: rest keeps working" if o.remaining > 0 else "")
    if o.remaining > 0 and ((o.tif == "DAY" and o.sessions >= 1) or
                            (o.tif == "GTC" and o.sessions >= gtc_sessions)):
        res.expired = True
    return res
