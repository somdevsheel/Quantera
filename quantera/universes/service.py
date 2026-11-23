"""Universe and watchlist management, resolution against the lake, CSV import.

Every lookup is scoped to the caller's workspace: another workspace's universe answers 404
(never 403), so its existence is not revealed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantera.core import audit
from quantera.core.errors import Conflict, DomainError, NotFound
from quantera.identity.service import Principal
from quantera.lake.client import LakeClient
from quantera.universes.models import (
    Universe,
    UniverseKind,
    UniverseSnapshot,
    Watchlist,
    WatchlistItem,
)

MAX_MEMBERS = 3000
MAX_CSV_BYTES = 1_000_000
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&.\-]{0,29}$")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
EXCHANGES = {"NSE": "XNSE", "BSE": "XBOM", "XNSE": "XNSE", "XBOM": "XBOM"}


# --------------------------------------------------------------------------- schemas
class UniverseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: UniverseKind
    index: str | None = Field(default=None, max_length=80)
    industry: str | None = Field(default=None, max_length=120)
    symbols: list[str] | None = Field(default=None, max_length=MAX_MEMBERS)
    exchange: str = "NSE"
    watchlist_id: uuid.UUID | None = None
    portfolio_id: uuid.UUID | None = None  # HOLDINGS
    reference_index: str | None = Field(default="NIFTY 500", max_length=80)  # HOLDINGS ranks

    @model_validator(mode="after")
    def _params(self) -> UniverseIn:
        need = {UniverseKind.INDEX: self.index, UniverseKind.SECTOR: self.industry,
                UniverseKind.MANUAL: self.symbols, UniverseKind.WATCHLIST: self.watchlist_id,
                UniverseKind.HOLDINGS: self.portfolio_id}
        if not need[self.kind]:
            field = {UniverseKind.INDEX: "index", UniverseKind.SECTOR: "industry",
                     UniverseKind.MANUAL: "symbols", UniverseKind.WATCHLIST: "watchlist_id",
                     UniverseKind.HOLDINGS: "portfolio_id"}
            raise ValueError(f"{self.kind.value} universes need `{field[self.kind]}`")
        if self.exchange.upper() not in EXCHANGES:
            raise ValueError("exchange must be NSE or BSE")
        return self


class SnapshotIn(BaseModel):
    as_of: date | None = None


def _clean_symbol(raw: str) -> tuple[str | None, str | None]:
    """(symbol, rejection reason)."""
    s = raw.strip()
    if not s:
        return None, None
    if s.startswith(FORMULA_PREFIXES):
        return None, "rejected: looks like a spreadsheet formula"
    s = s.upper()
    if not SYMBOL_RE.match(s):
        return None, "rejected: not a valid exchange symbol"
    return s, None


# --------------------------------------------------------------------------- universes
def _owned(session: Session, p: Principal, uid: uuid.UUID) -> Universe:
    u = session.get(Universe, uid)
    if u is None or u.workspace_id != p.workspace_id:
        raise NotFound("Universe not found")
    return u


def _watchlist(session: Session, p: Principal, wid: uuid.UUID) -> Watchlist:
    w = session.get(Watchlist, wid)
    if w is None or w.workspace_id != p.workspace_id:
        raise NotFound("Watchlist not found")
    return w


def create(session: Session, p: Principal, body: UniverseIn,
           request_id: str | None = None) -> Universe:
    params: dict[str, Any]
    if body.kind is UniverseKind.INDEX:
        params = {"index": body.index}
    elif body.kind is UniverseKind.SECTOR:
        params = {"industry": body.industry}
    elif body.kind is UniverseKind.WATCHLIST:
        assert body.watchlist_id is not None
        _watchlist(session, p, body.watchlist_id)
        params = {"watchlist_id": str(body.watchlist_id)}
    elif body.kind is UniverseKind.HOLDINGS:
        from quantera.portfolios.service import get as get_portfolio

        assert body.portfolio_id is not None
        get_portfolio(session, p, body.portfolio_id)
        params = {"portfolio_id": str(body.portfolio_id), "reference_index": body.reference_index}
    else:
        assert body.symbols is not None
        params = {"symbols": list(dict.fromkeys(s.strip() for s in body.symbols if s.strip())),
                  "exchange": EXCHANGES[body.exchange.upper()], "source": "manual"}
    u = Universe(workspace_id=p.workspace_id, created_by=p.user_id, name=body.name,
                 kind=body.kind.value, params=params)
    session.add(u)
    session.flush()
    audit.record(session, "universe.create", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="universe", resource_id=u.id, request_id=request_id,
                 details={"kind": u.kind})
    return u


def import_csv(session: Session, p: Principal, name: str, data: bytes, exchange: str = "NSE",
               request_id: str | None = None) -> Universe:
    if len(data) > MAX_CSV_BYTES:
        raise DomainError(f"CSV is larger than {MAX_CSV_BYTES // 1000} KB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DomainError("CSV must be UTF-8 text") from exc
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise DomainError("CSV is empty")
    header = [h.strip().lower() for h in rows[0]]
    col = header.index("symbol") if "symbol" in header else 0
    body_rows = rows[1:] if ("symbol" in header or not _clean_symbol(rows[0][0])[0]) else rows
    if len(body_rows) > MAX_MEMBERS:
        raise DomainError(f"CSV has more than {MAX_MEMBERS} rows")
    symbols: list[str] = []
    rejected: list[dict[str, Any]] = []
    for i, r in enumerate(body_rows, start=2 if body_rows is not rows else 1):
        if col >= len(r):
            continue
        s, why = _clean_symbol(r[col])
        if s:
            symbols.append(s)
        elif why:
            rejected.append({"row": i, "input": r[col][:40], "reason": why})
    if not symbols:
        raise DomainError("No valid symbols found in the CSV", {"rejected": rejected[:50]})
    u = create(session, p, UniverseIn(name=name, kind=UniverseKind.MANUAL, symbols=symbols,
                                      exchange=exchange), request_id)
    u.params = {**u.params, "source": "csv", "rejected": rejected[:200]}
    return u


def delete(session: Session, p: Principal, uid: uuid.UUID, request_id: str | None = None) -> None:
    u = _owned(session, p, uid)
    session.delete(u)
    audit.record(session, "universe.delete", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="universe", resource_id=uid, request_id=request_id)


def _member(row: dict[str, Any], exchange: str = "XNSE") -> dict[str, Any]:
    return {"instrument_id": row.get("instrument_id"), "symbol": row.get("symbol"),
            "exchange": row.get("exchange_mic") or exchange,
            "name": row.get("company_name") or row.get("name"),
            "industry": row.get("industry")}


def resolve(session: Session, p: Principal, lake: LakeClient, uid: uuid.UUID, body: SnapshotIn,
            request_id: str | None = None) -> UniverseSnapshot:
    u = _owned(session, p, uid)
    kind = UniverseKind(u.kind)
    members: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = list(u.params.get("rejected", []))
    notes: list[str] = []
    release, snap_date = "", None
    if kind is UniverseKind.INDEX:
        m = lake.index_members(u.params["index"], body.as_of)
        release, snap_date, notes = m.release, m.snapshot_date, m.notes
        members = [_member(r) for r in m.rows]
        if body.as_of and snap_date and snap_date != body.as_of.isoformat():
            notes.append(f"latest constituent list on or before {body.as_of} is {snap_date}")
    elif kind is UniverseKind.SECTOR:
        if body.as_of:
            raise DomainError("Sector membership has one current snapshot only; historical "
                              "as_of is not available (it would add look-ahead bias)")
        m = lake.industry_members(u.params["industry"])
        release, snap_date, notes = m.release, m.snapshot_date, m.notes
        members = [_member(r) for r in m.rows]
    elif kind is UniverseKind.HOLDINGS:
        from quantera.portfolios.service import book
        from quantera.portfolios.service import get as get_portfolio

        pf = get_portfolio(session, p, uuid.UUID(u.params["portfolio_id"]))
        held = [pos for pos in book(session, lake, pf).positions.values() if pos.quantity > 0]
        members = [{"instrument_id": h.instrument_id, "symbol": h.symbol, "exchange": h.exchange,
                    "name": None, "industry": None, "held": True} for h in held]
        if u.params.get("reference_index"):
            ref = lake.index_members(u.params["reference_index"], body.as_of)
            release, snap_date = ref.release, ref.snapshot_date
            members += [_member(r) for r in ref.rows]
            notes.append(f"holdings ranked against {u.params['reference_index']} members")
        else:
            release = lake.health().get("release", "")
            notes.append("ranked within the holdings only (no reference index)")
        if not held:
            raise DomainError("The portfolio has no open positions")
    else:
        if kind is UniverseKind.WATCHLIST:
            w = _watchlist(session, p, uuid.UUID(u.params["watchlist_id"]))
            items = session.scalars(select(WatchlistItem).where(
                WatchlistItem.watchlist_id == w.id).order_by(WatchlistItem.symbol))
            members = [{"instrument_id": i.instrument_id, "symbol": i.symbol,
                        "exchange": i.exchange, "name": i.name, "industry": None} for i in items]
            release = lake.health().get("release", "")
        else:
            ex = u.params.get("exchange", "XNSE")
            for s in u.params["symbols"]:
                try:
                    hits = lake.resolve(s, ex)
                except DomainError as exc:  # e.g. ambiguous identifier
                    unresolved.append({"input": s, "reason": exc.message})
                    continue
                hits = [h for h in hits if h.get("exchange_mic") == ex] or hits
                if not hits:
                    unresolved.append({"input": s, "reason": "not found in the lake"})
                elif len(hits) > 1:
                    unresolved.append({"input": s, "reason": "ambiguous",
                                       "candidates": [h.get("instrument_id") for h in hits][:5]})
                else:
                    members.append(_member(hits[0], ex))
            release = lake.health().get("release", "")
        if body.as_of:
            notes.append("manual and watchlist universes are fixed lists: as_of is recorded, "
                         "membership does not change with it")
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in members:  # one row per instrument (an index list never repeats, typed lists may)
        if row["instrument_id"] and row["instrument_id"] not in seen:
            seen.add(row["instrument_id"])
            unique.append(row)
    members = unique
    if len(members) > MAX_MEMBERS:
        raise DomainError(f"Universe resolves to {len(members)} members; "
                          f"the limit is {MAX_MEMBERS}")
    digest = hashlib.sha256(json.dumps(
        {"release": release, "snapshot_date": snap_date,
         "members": sorted(m["instrument_id"] for m in members)}, sort_keys=True).encode()
    ).hexdigest()
    snap = UniverseSnapshot(
        workspace_id=p.workspace_id, universe_id=u.id, created_by=p.user_id,
        requested_as_of=body.as_of,
        source_snapshot_date=date.fromisoformat(snap_date) if snap_date else None,
        lake_release=release, definition={"kind": u.kind, "params": u.params},
        members=members, unresolved=unresolved, member_count=len(members),
        content_hash=digest, notes=notes)
    session.add(snap)
    session.flush()
    audit.record(session, "universe.snapshot", actor=p.user_id, workspace=p.workspace_id,
                 resource_type="universe_snapshot", resource_id=snap.id, request_id=request_id,
                 details={"members": len(members), "unresolved": len(unresolved)})
    return snap


def get_snapshot(session: Session, p: Principal, sid: uuid.UUID) -> UniverseSnapshot:
    s = session.get(UniverseSnapshot, sid)
    if s is None or s.workspace_id != p.workspace_id:
        raise NotFound("Universe snapshot not found")
    return s


# --------------------------------------------------------------------------- watchlists
def create_watchlist(session: Session, p: Principal, name: str) -> Watchlist:
    w = Watchlist(workspace_id=p.workspace_id, created_by=p.user_id, name=name.strip())
    session.add(w)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"A watchlist named {name!r} already exists") from exc
    return w


def add_item(session: Session, p: Principal, lake: LakeClient, wid: uuid.UUID, symbol: str,
             exchange: str = "NSE") -> WatchlistItem:
    w = _watchlist(session, p, wid)
    s, why = _clean_symbol(symbol)
    if s is None:
        raise DomainError(why or "Empty symbol")
    ex = EXCHANGES.get(exchange.upper())
    if ex is None:
        raise DomainError("exchange must be NSE or BSE")
    hits = [h for h in lake.resolve(s, ex) if h.get("exchange_mic") == ex]
    if len(hits) != 1:
        raise NotFound(f"{s} is not a single listing on {ex} in the lake")
    h = hits[0]
    if session.scalar(select(WatchlistItem.id).where(
            WatchlistItem.watchlist_id == w.id,
            WatchlistItem.instrument_id == h["instrument_id"])) is not None:
        raise Conflict(f"{s} is already in this watchlist")
    item = WatchlistItem(watchlist_id=w.id, instrument_id=h["instrument_id"], symbol=h["symbol"],
                         exchange=ex, name=h.get("name"))
    session.add(item)
    session.flush()
    return item


def remove_item(session: Session, p: Principal, wid: uuid.UUID, item_id: uuid.UUID) -> None:
    w = _watchlist(session, p, wid)
    item = session.get(WatchlistItem, item_id)
    if item is None or item.watchlist_id != w.id:
        raise NotFound("Item not found")
    session.delete(item)


def delete_watchlist(session: Session, p: Principal, wid: uuid.UUID) -> None:
    session.delete(_watchlist(session, p, wid))
