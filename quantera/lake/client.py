"""Client for the read-only lake API (api/, port 8001). The platform never reads Parquet files
directly: the lake API owns release pinning, drift checks and usage-rights metadata.

If QUANTERA_LAKE_RELEASE is set, every response must come from that release; anything else is
refused, so a decision can never silently mix data from two releases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from quantera.core.errors import DomainError, NotFound


class LakeUnavailable(DomainError):
    status, code = 503, "lake_unavailable"


class LakeReleaseMismatch(DomainError):
    status, code = 503, "lake_release_mismatch"


@dataclass(frozen=True)
class Members:
    release: str
    snapshot_date: str | None
    rows: list[dict[str, Any]]
    notes: list[str]
    usage_rights: str | None


class LakeClient:
    def __init__(self, base_url: str, release: str | None = None, timeout: float = 60.0,
                 http: httpx.Client | None = None) -> None:
        """``http`` replaces the network client (tests pass the lake app's in-process client)."""
        self.release = release
        self.http = http or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        try:
            r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            raise LakeUnavailable(f"Data lake API unreachable ({type(exc).__name__}); "
                                  "start it with `python -m api`") from exc
        try:
            body: dict[str, Any] = r.json()
        except ValueError as exc:
            raise LakeUnavailable(
                f"Data lake API returned non-JSON (HTTP {r.status_code})") from exc
        if r.status_code == 404:
            raise NotFound(body.get("error", {}).get("message", "No data in the lake for this"))
        if r.status_code >= 400:
            msg = body.get("error", {}).get("message", f"HTTP {r.status_code}")
            raise (DomainError if r.status_code < 500 else LakeUnavailable)(f"Data lake: {msg}")
        got = body.get("release")
        if self.release and got and got != self.release:
            raise LakeReleaseMismatch(f"Lake serves release {got}, but {self.release} is pinned")
        return body

    def health(self) -> dict[str, Any]:
        return self._get("/v1/health")

    def indices(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = self._get("/v1/universes")["indices"]
        return out

    def index_members(self, index: str, as_of: date | None = None) -> Members:
        b = self._get("/v1/universes", index=index, as_of=as_of.isoformat() if as_of else None)
        return Members(b["release"], b.get("snapshot_date"), b["data"], b.get("notes", []),
                       b.get("usage_rights"))

    def industries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = self._get("/v1/sectors")["industries"]
        return out

    def industry_members(self, industry: str) -> Members:
        b = self._get("/v1/sectors", industry=industry)
        return Members(b["release"], b.get("snapshot_date"), b["data"], b.get("notes", []),
                       b.get("usage_rights"))

    def resolve(self, symbol: str, exchange: str = "NSE") -> list[dict[str, Any]]:
        try:
            out: list[dict[str, Any]] = self._get("/v1/instruments/resolve", symbol=symbol,
                                                  exchange=exchange)["data"]
        except NotFound:
            return []
        return out

    # ------------------------------------------------------------------ paged data
    def _all(self, path: str, **params: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Follow next_cursor until the last page. Returns (first-page metadata, all rows)."""
        rows: list[dict[str, Any]] = []
        meta: dict[str, Any] | None = None
        cursor = None
        while True:
            b = self._get(path, limit=10000, cursor=cursor, **params)
            meta = meta or {k: v for k, v in b.items() if k != "data"}
            rows += b.get("data", [])
            cursor = b.get("next_cursor")
            if not cursor:
                return meta, rows

    def prices_daily(self, *, instrument_id: str | None = None, symbol: str | None = None,
                     exchange: str | None = None, start: date | None = None,
                     end: date | None = None, adjust: str = "total",
                     as_of: date | None = None, bse_code: str | None = None
                     ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Daily bars. ``as_of`` = only corporate actions known by then are applied.

        On BSE one ISIN can carry several scrip codes, so a BSE listing is addressed by its
        ``bse_code`` (the lake refuses an ambiguous ISIN + exchange rather than guess)."""
        if bse_code:
            instrument_id = symbol = None
        return self._all("/v1/prices/daily", instrument_id=instrument_id, symbol=symbol,
                         bse_code=bse_code,
                         exchange=exchange, start=start.isoformat() if start else None,
                         end=end.isoformat() if end else None, adjust=adjust,
                         as_of=as_of.isoformat() if as_of else None)

    def index_daily(self, index_id: str, start: date | None = None,
                    end: date | None = None) -> list[dict[str, Any]]:
        return self._all("/v1/indices/daily", index_id=index_id,
                         start=start.isoformat() if start else None,
                         end=end.isoformat() if end else None)[1]

    def nse_statements(self, symbol: str, as_of: date | None = None,
                       latest_only: bool = True) -> list[dict[str, Any]]:
        """NSE results filed on or before ``as_of``. ``latest_only=False`` returns every filing
        (revisions too), for point-in-time selection at several dates."""
        try:
            return self._all("/v1/fundamentals/statements", source="nse", symbol=symbol,
                             as_of=as_of.isoformat() if as_of else None,
                             latest_only=str(latest_only).lower())[1]
        except NotFound:
            return []

    def corporate_actions(self, instrument_id: str, exchange: str,
                          as_of: date | None = None, bse_code: str | None = None
                          ) -> list[dict[str, Any]]:
        try:
            return self._all("/v1/corporate-actions",
                             instrument_id=None if bse_code else instrument_id,
                             bse_code=bse_code, exchange=exchange,
                             as_of=as_of.isoformat() if as_of else None)[1]
        except NotFound:
            return []

    def events(self, symbol: str, start: date | None = None,
               end: date | None = None) -> list[dict[str, Any]]:
        try:
            return self._all("/v1/calendar/events", symbol=symbol,
                             start=start.isoformat() if start else None,
                             end=end.isoformat() if end else None)[1]
        except NotFound:
            return []

    def search(self, query: str, exchange: str | None = None,
               limit: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = self._get("/v1/instruments", query=query, exchange=exchange,
                                              limit=limit)["data"]
        return out

    def liquid_universe(self, as_of: date, top: int = 200, window: int = 63) -> Members:
        """Point-in-time top-N NSE EQ stocks by median traded value (delisted included)."""
        b = self._get("/v1/universes/liquid", as_of=as_of.isoformat(), top=top, window=window)
        return Members(b["release"], as_of.isoformat(), b["data"], b.get("notes", []),
                       b.get("usage_rights"))
