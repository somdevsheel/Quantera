"""NSE security identity history: link symbol renames and ISIN changes into one security.

Input : data/silver/india/equity_prices/nse_*equity_prices_YYYYMMDD.parquet
Gold  : data/gold/india/reference/nse_security_id_history.parquet   (one row per segment)
Report: data/validation/india/nse_security_id_history_summary.json

Why: an NSE company's ISIN changes after most face-value splits (HDFCBANK: INE040A01018 ->
...026 in 2011 -> ...034 in 2019; 570 symbols have more than one ISIN), symbols get renamed
(CADILAHC -> ZYDUSLIFE, same ISIN; 461 ISINs have more than one symbol), and bhavcopies
before mid-2011 carry no ISIN at all. Keyed only by today's ISIN, a 20-year history silently
loses years, and corporate actions stamped with today's ISIN/symbol miss their prices.

Segment = (instrument_id or NULL, symbol) with its first/last trade date (equity series
EQ/BE/BZ/SM/ST/IL). Segments are linked into one security when:
  SAME_ISIN          they share a non-null ISIN (symbol rename), or
  SYMBOL_CHANGE_FILE NSE symbolchange.csv old -> new within 31 days (needed before 2011), or
  SYMBOL_CONTINUITY  same symbol, the later segment starts after the earlier one ends,
                     no other segment of that symbol in between, gap <= 31 calendar days
                     (ISIN change on split; pre-ISIN -> ISIN era in June 2011).
Not linked (flagged): same symbol overlapping in time (two securities at once), or a gap
> 31 days (symbol recycled by another company, or long suspension - kept separate).
security_key = 'IN:<ISIN of the segment that traded last>', so today's instrument_id is the
key of its own full history; securities that never had an ISIN get 'NSESYM:<symbol>:<first date>'.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRICES = str(ROOT / "data/silver/india/equity_prices/nse_*equity_prices_*.parquet")
GOLD = ROOT / "data/gold/india/reference/nse_security_id_history.parquet"
SUMMARY = ROOT / "data/validation/india/nse_security_id_history_summary.json"
SYMCHG = ROOT / "data/silver/india/reference/nse_symbol_changes.parquet"  # india_nse_reference_lists.py
SCHEMA_VERSION = "nse_security_id_history_v1"
EQUITY_SERIES = ("EQ", "BE", "BZ", "SM", "ST", "IL")
MAX_GAP_DAYS = 31


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def build():
    con = duckdb.connect(config={"threads": 8, "memory_limit": "12GB"})
    seg = con.execute(f"""
        SELECT instrument_id, symbol, min(CAST(trade_date AS DATE)) AS first_date,
               max(CAST(trade_date AS DATE)) AS last_date, count(*) AS rows
        FROM read_parquet('{PRICES}', union_by_name=true)
        WHERE security_series IN {EQUITY_SERIES} AND symbol IS NOT NULL
        GROUP BY 1, 2 ORDER BY symbol, first_date""").fetch_df()
    seg["first_date"] = pd.to_datetime(seg.first_date)
    seg["last_date"] = pd.to_datetime(seg.last_date)
    n = len(seg)
    dsu = DSU(n)
    reason = ["ROOT"] * n
    gap = [None] * n
    flags = [None] * n

    # SAME_ISIN: symbol renames.
    for _, idx in seg[seg.instrument_id.notna()].groupby("instrument_id").groups.items():
        idx = sorted(idx, key=lambda i: seg.first_date[i])
        for a, b in zip(idx, idx[1:]):
            dsu.union(a, b)
            reason[b] = "SAME_ISIN"

    # SYMBOL_CONTINUITY: consecutive segments of one symbol (ISIN change / pre-ISIN era).
    for _, idx in seg.groupby("symbol").groups.items():
        idx = sorted(idx, key=lambda i: (seg.first_date[i], seg.last_date[i]))
        for a, b in zip(idx, idx[1:]):
            if seg.instrument_id[a] == seg.instrument_id[b] and pd.notna(seg.instrument_id[a]):
                continue
            g = (seg.first_date[b] - seg.last_date[a]).days
            if g <= 0:
                flags[b] = f"OVERLAPS_SAME_SYMBOL({seg.instrument_id[a]})"
            elif g > MAX_GAP_DAYS:
                flags[b] = f"GAP_{g}D_NOT_LINKED"
            else:
                dsu.union(a, b)
                if reason[b] == "ROOT":
                    reason[b] = "SYMBOL_CONTINUITY"
                gap[b] = g

    # SYMBOL_CHANGE_FILE: NSE's official old -> new symbol list. Needed for renames before
    # mid-2011, when bhavcopies had no ISIN to link on (ADANIEXPO -> ADANIENT, 2006).
    # Linked only if the old symbol stops and the new one starts within MAX_GAP_DAYS of the
    # effective date; agreement with links already found via ISIN is reported.
    changes = pd.read_parquet(SYMCHG) if SYMCHG.exists() else pd.DataFrame(columns=["old_symbol", "new_symbol", "effective_date"])
    by_sym = {s: list(i) for s, i in seg.groupby("symbol").groups.items()}
    agree = added = unmatched = 0
    for ch in changes.dropna(subset=["old_symbol", "new_symbol", "effective_date"]).itertuples():
        eff = pd.Timestamp(ch.effective_date)
        olds = [i for i in by_sym.get(ch.old_symbol, []) if abs((seg.last_date[i] - eff).days) <= MAX_GAP_DAYS]
        news = [i for i in by_sym.get(ch.new_symbol, []) if abs((seg.first_date[i] - eff).days) <= MAX_GAP_DAYS]
        if not olds or not news:
            unmatched += 1
            continue
        a = max(olds, key=lambda i: seg.last_date[i])
        b = min(news, key=lambda i: seg.first_date[i])
        if dsu.find(a) == dsu.find(b):
            agree += 1
        else:
            dsu.union(a, b)
            if reason[b] == "ROOT":
                reason[b] = "SYMBOL_CHANGE_FILE"
            gap[b] = (seg.first_date[b] - seg.last_date[a]).days
            added += 1
    symchg_stats = {"changes_in_file": int(len(changes)), "already_linked_via_isin": agree,
                    "new_links": added, "not_matched_to_price_segments": unmatched}

    seg["component"] = [dsu.find(i) for i in range(n)]
    seg["link_reason"] = reason
    seg["link_gap_days"] = pd.array(gap, dtype="Int64")
    seg["flag"] = pd.array(flags, dtype="string")
    # security_key: ISIN of the segment that traded last within the component.
    keys = {}
    for comp, g in seg.groupby("component"):
        with_isin = g[g.instrument_id.notna()]
        if len(with_isin):
            keys[comp] = with_isin.sort_values(["last_date", "first_date"]).instrument_id.iloc[-1]
        else:
            first = g.sort_values("first_date").iloc[0]
            keys[comp] = f"NSESYM:{first.symbol}:{first.first_date.date()}"
    seg["security_key"] = seg.component.map(keys)
    # A component whose segments overlap in time under different ISINs would mean two
    # securities were merged; report it (should be none).
    seg["first_date"] = seg.first_date.dt.date
    seg["last_date"] = seg.last_date.dt.date
    out = seg.drop(columns="component")
    out["schema_version"] = SCHEMA_VERSION
    out["computed_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = out.sort_values(["security_key", "first_date"])
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    tmp = GOLD.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(GOLD)

    conflicts = con.execute(f"""
        SELECT count(DISTINCT a.security_key) FROM read_parquet('{GOLD}') a JOIN read_parquet('{GOLD}') b
          ON a.security_key = b.security_key AND a.instrument_id <> b.instrument_id
         AND a.first_date <= b.last_date AND b.first_date <= a.last_date""").fetchone()[0]
    summary = {
        "computed_utc": out.computed_utc.iloc[0], "segments": int(n),
        "securities": int(out.security_key.nunique()),
        "securities_with_history_links": int(out[out.link_reason != "ROOT"].security_key.nunique()),
        "link_reason": out.link_reason.value_counts().to_dict(),
        "flags": out.flag.dropna().str.replace(r"\(.*|\d+D", "", regex=True).value_counts().to_dict(),
        "securities_with_overlapping_isins": int(conflicts),
        "symbol_change_file": symchg_stats,
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    build()
