"""API validation before Quantera integration (docs/api_design.md §8).

Run:  pytest tests/api -q
Uses the frozen release on this machine (no fixtures copied); every expected value is
either computed directly from the release files with DuckDB or a documented known answer.
"""
import json
import math

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from api import lake as lake_mod
from api.main import create_app
from api.release import Release
from api.settings import Settings


def _reject(token):
    raise ValueError(f"invalid JSON constant {token}")


def get(client, url, status=200):
    r = client.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:500])
    return json.loads(r.text, parse_constant=_reject)   # strict: NaN/Infinity are not JSON


def all_pages(client, url, limit=5000):
    rows, cursor = [], None
    while True:
        u = f"{url}&limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        b = get(client, u)
        rows += b["data"]
        cursor = b["next_cursor"]
        if not cursor:
            return rows


# --------------------------------------------------------------------------- service
def test_health_pins_release_without_drift(client, lake):
    b = get(client, "/v1/health")
    assert b["release"] == lake.release.tag
    assert b["drift_check_at_startup"]["missing"] == 0 and b["drift_check_at_startup"]["changed"] == 0
    assert get(client, "/v1/releases")["serving"] == lake.release.tag


def test_catalog_lists_every_dataset_with_rights(client):
    ds = {d["dataset"]: d for d in get(client, "/v1/catalog")["datasets"]}
    assert len(ds) >= 17 and all(d["files"] > 0 for d in ds.values())
    assert ds["sec_statements"]["redistributable"] and not ds["nse_equity_daily"]["redistributable"]


def test_only_release_files_are_served(lake):
    listed = set(lake.release.paths)
    for d in lake.datasets.values():
        for _, p in d.files:
            assert p.split("/Quantera/", 1)[1] in listed


# --------------------------------------------------------------------------- contract
def test_contract_nse_prices_match_direct_query(client, duck, lake):
    rows = all_pages(client, "/v1/prices/daily?symbol=RELIANCE&exchange=NSE&start=2020-01-01&end=2020-12-31")
    files = lake.datasets["nse_equity_daily"].select("20200101", "20201231")
    direct = duck.execute("SELECT CAST(trade_date AS DATE)::VARCHAR, security_series, close FROM read_parquet($f, union_by_name=true) "
                          "WHERE instrument_id = 'IN:INE002A01018' ORDER BY 1, 2", {"f": files}).fetchall()
    assert [(r["trade_date"], r["security_series"], r["close"]) for r in rows] == direct
    assert len(direct) > 240


def test_contract_bse_prices_include_unlinked_rows(client, duck, lake):
    rows = all_pages(client, "/v1/prices/daily?bse_code=500325&start=2010-01-01&end=2010-12-31")
    files = lake.datasets["bse_equity_daily"].select("20100101", "20101231")
    n = duck.execute("SELECT count(*) FROM read_parquet($f, union_by_name=true) WHERE bse_scrip_code = '500325'",
                     {"f": files}).fetchone()[0]
    assert len(rows) == n > 240


def test_contract_fo_chain_and_paging(client, duck, lake):
    rows = all_pages(client, "/v1/derivatives/fo/daily?symbol=NIFTY&start=2025-06-05&end=2025-06-05", limit=700)
    files = lake.datasets["nse_fo_daily"].select("20250605", "20250605")
    n = duck.execute("SELECT count(*) FROM read_parquet($f) WHERE symbol = 'NIFTY'", {"f": files}).fetchone()[0]
    assert len(rows) == n and len({r["contract_id"] for r in rows}) == n


def test_contract_greeks_ok_only(client, duck, lake):
    rows = all_pages(client, "/v1/derivatives/options/greeks?symbol=NIFTY&start=2025-06-02&end=2025-06-05&quality=ok_only")
    files = lake.datasets["nse_option_greeks"].select("20250602", "20250605")
    n = duck.execute("SELECT count(*) FROM read_parquet($f) WHERE symbol = 'NIFTY' AND iv_status = 'OK'",
                     {"f": files}).fetchone()[0]
    assert len(rows) == n > 0 and all(r["iv_status"] == "OK" for r in rows)


def test_arrow_format_matches_json(client):
    url = "/v1/indices/daily?index_id=NSEIDX:NIFTY_50&start=2025-01-01&end=2025-03-31"
    j = get(client, url)["data"]
    r = client.get(url + "&format=arrow")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.apache.arrow.stream"
    t = pa.ipc.open_stream(r.content).read_all()
    assert t.num_rows == len(j) and t.column("close").to_pylist() == [x["close"] for x in j]
    assert r.headers["x-quantera-release"]


@pytest.mark.parametrize("url", [
    "/v1/prices/daily?symbol=RELIANCE&exchange=NSE&start=2008-01-01&end=2013-12-31&adjust=split",
    "/v1/prices/daily?bse_code=500325&start=2009-01-01&end=2011-12-31",
])
def test_api_cache_matches_per_day_files(client, lake, url):
    assert lake.cache, "api cache not built: python -m api.cache --build"
    cached = all_pages(client, url)
    saved, lake.cache = lake.cache, {}
    try:
        raw = all_pages(client, url)
    finally:
        lake.cache = saved
    assert cached == raw and len(raw) > 700


def test_pre_isin_nse_rows_are_returned_and_flagged(client):
    rows = get(client, "/v1/prices/daily?symbol=RELIANCE&exchange=NSE&start=2005-01-01&end=2005-01-31")["data"]
    assert rows and all(r["id_match"] == "LINKED_PRE_ISIN" for r in rows)


# --------------------------------------------------------------------------- identity history
def test_isin_changes_do_not_cut_history(client):
    """HDFCBANK changed ISIN in 2011 and 2019 (face-value splits). Keyed only by today's ISIN,
    2011-2019 used to vanish silently."""
    b = get(client, "/v1/prices/daily?symbol=HDFCBANK&exchange=NSE&series=EQ&limit=100000")
    years = {r["trade_date"][:4] for r in b["data"]}
    assert {str(y) for y in range(2000, 2026)} <= years
    assert {r["id_match"] for r in b["data"]} == {"INSTRUMENT_ID", "LINKED_OTHER_ISIN", "LINKED_PRE_ISIN"}
    assert len(b["security"]["isins"]) == 3
    mid = [r for r in b["data"] if "2012-01-01" <= r["trade_date"] <= "2018-12-31"]
    assert len(mid) > 1700


def test_renamed_symbol_resolves_and_keeps_history(client):
    b = get(client, "/v1/prices/daily?symbol=CADILAHC&exchange=NSE&series=EQ&start=2012-01-01&end=2012-01-31")
    assert b["instrument"]["instrument_id"] == "IN:INE010B01027" and b["data"][0]["symbol"] == "CADILAHC"
    pre = get(client, "/v1/prices/daily?symbol=ADANIENT&exchange=NSE&series=EQ&start=2005-01-03&end=2005-01-07")
    assert {r["symbol"] for r in pre["data"]} == {"ADANIEXPO"}     # renamed 2006, linked via symbolchange.csv
    h = get(client, "/v1/instruments/history?symbol=ZYDUSLIFE")
    assert [s["symbol"] for s in h["segments"]][-1] == "ZYDUSLIFE" and len(h["segments"]) >= 3


def test_resolve_by_current_and_former_isin(client):
    cur = get(client, "/v1/instruments/resolve?isin=INE467B01029&exchange=XNSE")["data"]
    assert [(r["symbol"], r["exchange_mic"]) for r in cur] == [("TCS", "XNSE")]
    old = get(client, "/v1/instruments/resolve?isin=INE040A01018&exchange=XNSE")["data"]   # HDFCBANK pre-2011
    assert old[0]["instrument_id"] == "IN:INE040A01034"
    rows = get(client, "/v1/prices/daily?isin=INE040A01026&exchange=NSE&start=2015-01-01&end=2015-01-10")["data"]
    assert rows and all(r["id_match"] == "LINKED_OTHER_ISIN" for r in rows)


def test_nse_statements_resolve_former_symbol(client):
    """NSE stamps a company's current symbol on all its filings; a query by a former symbol
    (CADILAHC, renamed ZYDUSLIFE 2022-03-07) must still return the company's statements."""
    url = ("/v1/fundamentals/statements?source=nse&period_type=Quarterly&consolidated=Consolidated"
           "&limit=100000&symbol=")
    new, old = get(client, url + "ZYDUSLIFE")["data"], get(client, url + "CADILAHC")["data"]
    assert new and new == old and min(r["to_date"] for r in new) < "2022-01-01"


# --------------------------------------------------------------------------- dividends + total return
def test_dividend_events_summed_and_factor_formula(client):
    rows = get(client, "/v1/corporate-actions/dividends?symbol=LT&exchange=NSE")["data"]
    lt = next(r for r in rows if r["ex_date"] == "2023-08-02")        # Rs 24 final + Rs 6 special
    assert lt["dividend_cash_inr"] == pytest.approx(30.0) and lt["apply"]
    assert lt["dividend_factor"] == pytest.approx((lt["prior_close"] - 30.0) / lt["prior_close"])


def test_total_return_applies_dividends_point_in_time(client):
    base = "/v1/prices/daily?symbol=LT&exchange=NSE&series=EQ&start=2023-07-31&end=2023-07-31"
    split = get(client, base + "&adjust=split&as_of=2023-08-02")["data"][0]
    before = get(client, base + "&adjust=total&as_of=2023-08-01")["data"][0]    # ex-date 2023-08-02 not yet known
    after = get(client, base + "&adjust=total&as_of=2023-08-02")["data"][0]
    divs = get(client, "/v1/corporate-actions/dividends?symbol=LT&exchange=NSE")["data"]
    f = next(r["dividend_factor"] for r in divs if r["ex_date"] == "2023-08-02")
    assert before["close"] == pytest.approx(split["close"])
    assert after["close"] == pytest.approx(split["close"] * f)


def test_total_return_refused_without_dividend_factors(client):
    assert get(client, "/v1/prices/daily?symbol=AAPL&adjust=total", 400)["error"]["code"] == "INVALID_PARAMETER"


# --------------------------------------------------------------------------- universes
def test_universe_snapshot_and_no_backfill(client):
    b = get(client, "/v1/universes?index=NIFTY 50")
    assert b["count"] == 50 and all(r["industry"] for r in b["data"])
    e = get(client, "/v1/universes?index=NIFTY 50&as_of=2020-01-01", 404)
    assert "survivorship" in e["error"]["message"]


def test_liquid_universe_is_point_in_time_and_keeps_delisted(client):
    b = get(client, "/v1/universes/liquid?as_of=2015-06-30&top=50")
    rows = b["data"]
    assert b["count"] == 50 and all(r["last_trade_date"] <= "2015-06-30" for r in rows)
    vals = [r["median_traded_value"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    syms = {r["symbol"] for r in rows}
    assert "HDFC" in syms  # merged into HDFCBANK and delisted in 2023: still in the 2015 universe


def test_delisted_security_prices_reachable_by_instrument_id(client):
    rows = get(client, "/v1/universes/liquid?as_of=2015-06-30&top=50")["data"]
    hdfc = next(r for r in rows if r["symbol"] == "HDFC")  # delisted 2023 (merged)
    b = get(client, f"/v1/prices/daily?instrument_id={hdfc['instrument_id']}&exchange=NSE"
                    "&start=2015-06-01&end=2015-06-30")
    assert b["count"] >= 15 and b["instrument"]["source"] == "nse_identity_history"


def test_sectors_catalogue_and_members(client):
    cat = get(client, "/v1/sectors")
    names = {i["industry"]: i["members"] for i in cat["industries"]}
    assert cat["snapshot_date"] and sum(names.values()) > 500
    it = next(n for n in names if "Information Technology" in n)
    b = get(client, f"/v1/sectors?industry={it.lower()}")  # case-insensitive
    assert b["count"] == names[it] and {r["industry"] for r in b["data"]} == {it}
    assert any(r["symbol"] == "TCS" for r in b["data"])
    get(client, "/v1/sectors?industry=No Such Industry", 404)


# --------------------------------------------------------------------------- point in time
def _close(client, url):
    rows = get(client, url)["data"]
    assert len(rows) == 1, rows
    return rows[0]["close"]


def test_pit_india_bonus_not_applied_before_ex_date(client):
    base = "/v1/prices/daily?symbol=RELIANCE&exchange=NSE&series=EQ&start=2024-10-25&end=2024-10-25"
    raw = _close(client, base)
    before = _close(client, base + "&adjust=split&as_of=2024-10-27")   # bonus ex-date 2024-10-28
    after = _close(client, base + "&adjust=split&as_of=2024-10-28")
    assert before == raw and after == pytest.approx(raw * 0.5)


def test_pit_global_split_reversal(client):
    base = "/v1/prices/daily?symbol=NVDA&start=2024-06-07&end=2024-06-07"
    raw = _close(client, base)                                        # NVDA 10:1 split ex 2024-06-10
    assert raw == pytest.approx(1208.88, rel=1e-4)                     # as traded
    assert _close(client, base + "&adjust=split") == pytest.approx(120.888, rel=1e-4)
    assert _close(client, base + "&adjust=split&as_of=2024-06-07") == pytest.approx(raw)


def test_pit_sec_statement_invisible_before_filing(client):
    url = "/v1/fundamentals/statements?source=sec&ticker=BLK&limit=100000&as_of="
    before = {r["period_end"] for r in get(client, url + "2026-08-05")["data"]}
    after = {r["period_end"] for r in get(client, url + "2026-08-06")["data"]}
    assert "2026-06-30" not in before and "2026-06-30" in after


def test_pit_nse_restated_filing_version(client):
    url = ("/v1/fundamentals/statements?source=nse&symbol=ACC&period_type=Quarterly&consolidated=Consolidated"
           "&limit=100000&as_of=")
    pick = lambda d: next(r for r in get(client, url + d)["data"] if r["to_date"] == "2024-12-31")["source_file"]
    assert "117817" in pick("2025-01-29")      # first filing (2025-01-28)
    assert "118350" in pick("2025-02-01")      # revised filing (2025-01-31)


def test_pit_etf_holdings_use_report_filed_by_as_of(client):
    b = get(client, "/v1/funds/etf-holdings?symbol=XLK&as_of=2023-01-01&limit=1")
    assert b["report"]["filing_date"] <= "2023-01-01"


def test_pit_corporate_actions_hide_future_events(client):
    rows = get(client, "/v1/corporate-actions?symbol=RELIANCE&exchange=NSE&as_of=2024-10-27")["data"]
    assert [r["ex_date"] for r in rows] == ["2009-11-26", "2017-09-07"]


# --------------------------------------------------------------------------- known answers
def test_known_blk_reconciles_2026_06_30(client):
    r = next(x for x in get(client, "/v1/fundamentals/statements?source=sec&ticker=BLK&limit=100000")["data"]
             if x["period_end"] == "2026-06-30")
    assert r["recon_status"].startswith("BALANCED") and r["recon_difference"] == 0


def test_known_aapl_latest_10k_balanced(client):
    rows = [r for r in get(client, "/v1/fundamentals/statements?source=sec&ticker=AAPL&limit=100000")["data"]
            if r["form"] == "10-K"]
    assert rows and rows[-1]["assets"] > 3e11 and rows[-1]["recon_status"] == "BALANCED"


def test_known_acc_fy24_quarters_sum_to_annual(client):
    q = get(client, "/v1/fundamentals/statements?source=nse&symbol=ACC&period_type=Quarterly"
                    "&consolidated=Consolidated&limit=100000")["data"]
    fy = [r["net_profit"] for r in q if "2023-04-01" <= r["from_date"] and r["to_date"] <= "2024-03-31"]
    facts = get(client, "/v1/fundamentals/facts?source=nse&symbol=ACC&concept=ProfitLossForPeriod"
                        "&accession=INDAS_104812_1109391_26042024060121&limit=1000")["data"]
    annual = next(f["numeric_value"] for f in facts
                  if f["period_start"] == "2023-04-01" and f["period_end"] == "2024-03-31" and not f["dimension_count"])
    assert len(fy) == 4 and sum(fy) == pytest.approx(annual, rel=1e-4)


def _atm30(client, day):
    """30-day ATM implied vol: ATM (strike nearest the forward, CE/PE average) per expiry,
    interpolated linearly in total variance to 30 calendar days."""
    rows = get(client, f"/v1/derivatives/options/surface?symbol=NIFTY&date={day}&limit=100000")["data"]
    by_exp = {}
    for r in rows:
        by_exp.setdefault((r["expiry_date"], r["days_to_expiry"], r["forward"]), []).append(r)
    atm = []
    for (_, dte, fwd), rs in sorted(by_exp.items(), key=lambda kv: kv[0][1]):
        k = min({r["strike_price"] for r in rs}, key=lambda s: abs(s - fwd))
        ivs = [r["implied_vol"] for r in rs if r["strike_price"] == k]
        atm.append((dte / 365, sum(ivs) / len(ivs)))
    lo = max((a for a in atm if a[0] <= 30 / 365), key=lambda a: a[0])
    hi = min((a for a in atm if a[0] >= 30 / 365), key=lambda a: a[0])
    t = 30 / 365
    if hi[0] == lo[0]:
        return lo[1]
    w = (t - lo[0]) / (hi[0] - lo[0])
    return math.sqrt(((1 - w) * lo[1] ** 2 * lo[0] + w * hi[1] ** 2 * hi[0]) / t)


def test_known_nifty_30d_atm_iv_tracks_india_vix(client):
    """India VIX is a model-free variance index (includes the skew premium), so it sits
    above ATM IV. Measured on 38 dates 2023-2026: VIX - ATM30 median +1.1 vol pts,
    p5 -0.6, p95 +3.6, max +4.8. Bounds below are that envelope, not a free tolerance."""
    gaps = []
    for day in ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05"]:
        vix = get(client, f"/v1/indices/daily?index_id=NSEIDX:INDIA_VIX&start={day}&end={day}")["data"][0]["close"]
        gaps.append(vix - _atm30(client, day) * 100)
    assert all(-2.0 < g < 5.0 for g in gaps), gaps
    assert 0.0 <= sum(gaps) / len(gaps) <= 3.0, gaps


def test_known_xlk_weights_sum_to_100(client):
    rows = get(client, "/v1/funds/etf-holdings?symbol=XLK&limit=100000")["data"]
    assert 95 <= sum(r["pct_of_nav"] for r in rows) <= 105


# --------------------------------------------------------------------------- drift + licensing + errors
def test_release_drift_refuses_to_start(monkeypatch):
    class Tampered(Release):
        def __init__(self, tag=None):
            super().__init__(tag)
            i = self.manifest.index[self.manifest.path.str.endswith("sec_statements.parquet")][0]
            self.manifest.loc[i, "bytes"] += 1        # the lake no longer matches this "release"

    monkeypatch.setattr(lake_mod, "Release", Tampered)
    with pytest.raises(RuntimeError, match="RELEASE_DRIFT"):
        with TestClient(create_app(Settings())):
            pass


@pytest.mark.parametrize("url", [
    "/v1/prices/daily?symbol=RELIANCE&exchange=NSE&start=2025-01-01&end=2025-01-10",
    "/v1/prices/daily?symbol=AAPL&start=2025-01-01&end=2025-01-10",
    "/v1/indices/daily?index_id=NSEIDX:NIFTY_50&start=2025-01-01&end=2025-01-10",
    "/v1/derivatives/fo/daily?symbol=NIFTY",
    "/v1/derivatives/options/greeks?symbol=NIFTY",
    "/v1/fundamentals/statements?source=nse&symbol=ACC",
    "/v1/calendar/events?symbol=RELIANCE",
])
def test_redistribution_mode_blocks_licensed_data(redistribution_client, url):
    b = get(redistribution_client, url, 403)
    assert b["error"]["code"] == "LICENCE_REQUIRED"


@pytest.mark.parametrize("url", [
    "/v1/fundamentals/statements?source=sec&ticker=AAPL&limit=1",
    "/v1/funds/etf-holdings?symbol=XLK&limit=1",
    "/v1/funds/nav?scheme_code=119551&start=2025-01-01&end=2025-01-03",
    "/v1/macro/series?series_id=INDIR3TIB01STM&limit=1",
])
def test_redistribution_mode_serves_public_data(redistribution_client, url):
    get(redistribution_client, url, 200)


def test_errors_use_envelope(client):
    assert get(client, "/v1/prices/daily?symbol=RELIANCE", 422)["error"]["code"] == "AMBIGUOUS_IDENTIFIER"
    assert get(client, "/v1/prices/daily?symbol=NOSUCHCO&exchange=NSE", 404)["error"]["code"] == "UNKNOWN_INSTRUMENT"
    assert get(client, "/v1/prices/daily?symbol=RELIANCE&exchange=NSE&start=bad", 400)["error"]["code"] == "INVALID_PARAMETER"
    assert get(client, "/v1/prices/daily?symbol=MSFT&adjust=total", 400)["error"]["code"] == "INVALID_PARAMETER"
