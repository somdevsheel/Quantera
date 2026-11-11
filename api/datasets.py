"""Datasets the API serves: where they live, how files are keyed, and their usage rights.

Each dataset's file list comes from the release manifest (never from a directory listing),
so files written after the freeze are invisible. Files are keyed by the date in their name
(per-day / per-month datasets) or by their entity partition (symbol=, cik=), which lets an
endpoint open only the files it needs.
"""
import re
from dataclasses import dataclass, field

import pandas as pd
import pyarrow.parquet as pq

from .settings import ROOT

# Usage-rights text that blocks serving in `redistribution` mode, and text that allows it.
# Anything not clearly public is blocked (conservative default).
_BLOCK = re.compile(r"licen[cs]e|research use|internal research|no redistribution|same constraints|inherits|personal", re.I)
_ALLOW = re.compile(r"public domain|AMFI public|FRED", re.I)


@dataclass
class Dataset:
    name: str
    pattern: str          # regex on the repo-relative path; group `key` = date/month/entity
    key_kind: str         # date | month | entity | single
    description: str
    files: list = field(default_factory=list)   # [(key, absolute path)], sorted by key
    schema_version: str | None = None
    usage_rights: str = ""
    redistributable: bool = False

    def select(self, lo=None, hi=None, keys=None):
        """Absolute paths whose key is in [lo, hi] (dates as YYYYMMDD / months as YYYYMM)
        or in `keys` (entities)."""
        out = []
        for k, p in self.files:
            if keys is not None and k not in keys:
                continue
            if lo is not None and k < lo:
                continue
            if hi is not None and k > hi:
                continue
            out.append(p)
        return out

    @property
    def keys(self):
        return [k for k, _ in self.files]


DATASETS = [
    Dataset("nse_equity_daily", r"^data/silver/india/equity_prices/nse_(?:legacy_)?equity_prices_(?P<key>\d{8})\.parquet$",
            "date", "NSE cash-market daily OHLCV, all series (legacy bhavcopy to 2024-07-05, UDiFF after)"),
    Dataset("bse_equity_daily", r"^data/silver/india/equity_prices/bse_bhavcopy/year=\d{4}/month=\d{2}/bse_bhavcopy_(?P<key>\d{8})\.parquet$",
            "date", "BSE cash-market daily OHLCV, all scrips"),
    Dataset("global_equity_daily", r"^data/silver/market/symbol=(?P<key>[^/]+)/[^/]+\.parquet$",
            "entity", "Global equities daily OHLCV (Yahoo Finance)"),
    Dataset("nse_indices_daily", r"^data/silver/india/indices/nse/year=\d{4}/nse_indices_(?P<key>\d{8})\.parquet$",
            "date", "NSE indices daily (incl. India VIX)"),
    Dataset("nse_fo_daily", r"^data/silver/india/derivatives/nse_fo/year=\d{4}/nse_fo_(?P<key>\d{8})\.parquet$",
            "date", "NSE futures & options daily bhavcopy"),
    Dataset("nse_option_greeks", r"^data/gold/india/derivatives/nse_option_greeks/year=\d{4}/nse_option_greeks_(?P<key>\d{8})\.parquet$",
            "date", "NSE option implied vol + Black-76 Greeks (traded European options)"),
    Dataset("amfi_nav", r"^data/silver/india/mutual_funds/nav/year=\d{4}/amfi_nav_(?P<key>\d{6})\.parquet$",
            "month", "AMFI mutual fund NAV, all schemes"),
    Dataset("etf_holdings", r"^data/gold/funds/(?P<key>etf_holdings)\.parquet$",
            "single", "US ETF holdings from SEC N-PORT"),
    Dataset("sec_statements", r"^data/gold/fundamentals/(?P<key>sec_statements)\.parquet$",
            "single", "SEC point-in-time statement summary + balance-sheet reconciliation, one row per filing"),
    Dataset("sec_filing_facts", r"^data/silver/fundamentals/filing_facts/cik=(?P<key>\d+)/[^/]+\.parquet$",
            "entity", "SEC XBRL facts per filing, unsummed, with contexts and dimensions"),
    Dataset("nse_key_financials", r"^data/gold/india/fundamentals/(?P<key>nse_key_financials)\.parquet$",
            "single", "NSE listed-company financial results, key lines, every filing version"),
    Dataset("nse_xbrl_facts", r"^data/silver/india/fundamentals/xbrl_facts/symbol=(?P<key>[^/]+)/[^/]+\.parquet$",
            "entity", "NSE financial-results XBRL facts, unsummed, duplicates flagged"),
    Dataset("india_corporate_actions", r"^data/gold/india/corporate_actions/(?P<key>nse_price_adjustment_factors)\.parquet$",
            "single", "NSE split/bonus events with price adjustment factors"),
    Dataset("global_corporate_actions", r"^data/gold/global/corporate_actions/(?P<key>yahoo_split_factors)\.parquet$",
            "single", "Global split factors (Yahoo Finance)"),
    Dataset("india_trading_calendar", r"^data/gold/india/calendar/(?P<key>india_trading_calendar)\.parquet$",
            "single", "India exchange trading days / holidays per segment"),
    Dataset("nse_event_calendar", r"^data/silver/india/calendar/(?P<key>nse_event_calendar)\.parquet$",
            "single", "NSE corporate event calendar (results, board meetings, AGMs)"),
    Dataset("fred_series", r"^data/silver/(?P<key>(?:global|india)/[a-z_]+)/fred_series\.parquet$",
            "entity", "FRED series (India + global macro, rates, FX, commodities, volatility)"),
]
BY_NAME = {d.name: d for d in DATASETS}


def _rights_table():
    cad = pd.read_csv(ROOT / "reference/dataset_cadence.csv")
    return sorted(zip(cad.path_prefix, cad.usage_rights), key=lambda r: -len(r[0]))


def _schema_version(path):
    schema = pq.read_schema(path)
    if "schema_version" not in schema.names:
        return None
    vals = pq.read_table(path, columns=["schema_version"]).column(0).unique().to_pylist()
    return ",".join(sorted(str(v) for v in vals if v is not None)) or None


def load(paths):
    """Bind each dataset to the release's files. `paths` = repo-relative manifest paths."""
    rights = _rights_table()
    compiled = [(d, re.compile(d.pattern)) for d in DATASETS]
    for d in DATASETS:
        d.files = []
    for p in paths:
        if not p.startswith("data/silver/") and not p.startswith("data/gold/"):
            continue
        for d, rx in compiled:
            m = rx.match(p)
            if m:
                d.files.append((m.group("key"), str(ROOT / p)))
                break
    for d in DATASETS:
        d.files.sort()
        if d.files:
            sample = d.files[0][1].removeprefix(str(ROOT) + "/data/")
            d.usage_rights = next((r for prefix, r in rights if sample.startswith(prefix)), "unknown")
            d.redistributable = bool(_ALLOW.search(d.usage_rights)) and not _BLOCK.search(d.usage_rights)
            d.schema_version = _schema_version(d.files[-1][1])
    return BY_NAME
