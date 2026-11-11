"""NSE option implied volatility and Greeks (Black-76), derived from Silver F&O.

Input : data/silver/india/derivatives/nse_fo/year=YYYY/*.parquet   (schema nse_fo_v2)
        data/silver/india/rates/fred_series.parquet                (INR money-market rates)
Output: data/gold/india/derivatives/nse_option_greeks/year=YYYY/nse_option_greeks_YYYYMMDD.parquet
        data/gold/india/derivatives/nse_option_forwards/year=YYYY/nse_option_forwards_YYYYMMDD.parquet
        data/validation/india/nse_option_greeks_ledger.csv        (per day x iv_status counts)

Method
  * Model: Black-76 on the forward of the option's own expiry (European exercise).
  * Forward: put-call parity at the strike where |C - P| is smallest among strikes with
    BOTH call and put traded:  F = K + (C - P) / DF.  This is model-free and absorbs
    dividends and carry. Fallback: same-expiry future close if traded (forward_source).
  * Rate: India 3-month interbank (INDIR3TIB01STM) from Nov-2011, else call money
    (IRSTCI01INM156N); monthly value of the trade month, as-of forward-filled;
    continuous rate = ln(1 + r). DF = exp(-r T).  (Monthly averages are published after
    month end, so the rate carries a small look-ahead; its effect on IV is negligible.)
  * T = calendar days to expiry / 365.
  * Price: close, only for options that traded on the day (contracts or quantity > 0).
    NSE settlement prices for untraded options are themselves model output -> no IV.
  * American options (CA/PA, stock options before 2011) are not priced with Black-76.

iv_status: OK | NO_TRADE (ledger only) | AMERICAN | EXPIRY_DAY | NO_FORWARD |
           BELOW_INTRINSIC | ABOVE_MAX | NO_CONVERGENCE
Greeks (per 1 unit of underlying, Black-76):
  delta = DF*N(d1) (call), DF*(N(d1)-1) (put)   [w.r.t. the forward]
  gamma = DF*n(d1) / (F sigma sqrt(T))
  vega  = DF*F*n(d1)*sqrt(T) / 100              [per 1 vol point]
  theta = per calendar day, Black-76 with r held fixed

Run:  python pipelines/india_nse_option_greeks.py [--start YYYY-MM-DD] [--end ...] [--rebuild]
Restartable: a day is skipped when its output is newer than its Silver input.
"""
import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "data/silver/india/derivatives/nse_fo"
RATES = ROOT / "data/silver/india/rates/fred_series.parquet"
OUT = ROOT / "data/gold/india/derivatives/nse_option_greeks"
FWD_OUT = ROOT / "data/gold/india/derivatives/nse_option_forwards"
LEDGER = ROOT / "data/validation/india/nse_option_greeks_ledger.csv"
SCHEMA = "nse_option_greeks_v1"
SQRT2PI = math.sqrt(2 * math.pi)


def npdf(x):
    return np.exp(-0.5 * x * x) / SQRT2PI


def black76(F, K, T, DF, sigma, is_call):
    sd = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    call = DF * (F * ndtr(d1) - K * ndtr(d2))
    put = DF * (K * ndtr(-d2) - F * ndtr(-d1))
    return np.where(is_call, call, put), d1, d2


def implied_vol(price, F, K, T, DF, is_call, lo=1e-4, hi=10.0, iters=80):
    """Vectorised bisection (monotone in sigma, always converges inside bounds)."""
    lo = np.full_like(price, lo)
    hi = np.full_like(price, hi)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        p, _, _ = black76(F, K, T, DF, mid, is_call)
        up = p > price
        hi = np.where(up, mid, hi)
        lo = np.where(up, lo, mid)
    sigma = 0.5 * (lo + hi)
    p, _, _ = black76(F, K, T, DF, sigma, is_call)
    ok = np.abs(p - price) <= np.maximum(1e-4 * price, 1e-3)
    return sigma, ok


def load_rates():
    r = pd.read_parquet(RATES, columns=["series_id", "observation_date", "value"])
    r["observation_date"] = pd.to_datetime(r.observation_date)
    tib = r[r.series_id == "INDIR3TIB01STM"].set_index("observation_date").value
    call = r[r.series_id == "IRSTCI01INM156N"].set_index("observation_date").value
    s = call.copy()
    s.loc[tib.index] = tib
    src = pd.Series("IRSTCI01INM156N", index=s.index)
    src.loc[tib.index] = "INDIR3TIB01STM"
    df = pd.DataFrame({"rate_pct": s, "rate_series": src}).sort_index()
    return df.dropna()


def rate_for(rates, day):
    row = rates.loc[:day].iloc[-1]
    return float(row.rate_pct), row.rate_series


def forwards(opts, futs, DF_by_exp):
    """One forward per (symbol, expiry): put-call parity, fallback to traded future."""
    traded = opts[opts.traded & opts.option_type.isin(["CE", "PE"])]
    piv = traded.pivot_table(index=["symbol", "expiry_date", "strike_price"], columns="option_type",
                             values="close", aggfunc="first").dropna()
    rows = []
    if not piv.empty:
        piv = piv.reset_index()
        piv["gap"] = (piv["CE"] - piv["PE"]).abs()
        best = piv.loc[piv.groupby(["symbol", "expiry_date"]).gap.idxmin()]
        df = best.expiry_date.map(DF_by_exp)
        best = best.assign(forward=best.strike_price + (best["CE"] - best["PE"]) / df,
                           forward_source="PUT_CALL_PARITY", parity_strike=best.strike_price)
        rows.append(best[["symbol", "expiry_date", "forward", "forward_source", "parity_strike"]])
    f = futs[futs.traded & (futs.close > 0)][["symbol", "expiry_date", "close"]].rename(columns={"close": "forward"})
    f = f.assign(forward_source="FUTURE_CLOSE", parity_strike=np.nan)
    rows.append(f)
    allf = pd.concat(rows, ignore_index=True)
    allf = allf[allf.forward > 0]
    # parity first, future fallback
    allf["prio"] = (allf.forward_source != "PUT_CALL_PARITY").astype(int)
    return allf.sort_values("prio").drop_duplicates(["symbol", "expiry_date"]).drop(columns="prio")


def process_day(path, rates, con):
    day_df = con.execute(f"""
        SELECT trade_date, symbol, underlying_type, instrument_class, expiry_date, strike_price,
               option_type, close, settlement_price, contracts, traded_quantity, open_interest,
               lot_size, contract_id
        FROM read_parquet('{path}')""").df()
    if day_df.empty:
        return None
    day = pd.Timestamp(day_df.trade_date.iloc[0]).normalize()
    day_df["traded"] = (day_df.contracts.fillna(0) > 0) | (day_df.traded_quantity.fillna(0) > 0)
    opts = day_df[day_df.instrument_class == "OPT"].copy()
    futs = day_df[day_df.instrument_class == "FUT"]
    counts = {"NO_TRADE": int((~opts.traded).sum())}
    opts = opts[opts.traded].copy()
    if opts.empty:
        return day, None, None, counts

    rate_pct, rate_series = rate_for(rates, day)
    r = math.log1p(rate_pct / 100.0)
    opts["days_to_expiry"] = (pd.to_datetime(opts.expiry_date).dt.normalize() - day).dt.days
    opts["T"] = opts.days_to_expiry / 365.0
    exps = pd.to_datetime(pd.concat([opts.expiry_date, futs.expiry_date]).unique())
    DF_by_exp = {e: math.exp(-r * max((pd.Timestamp(e).normalize() - day).days, 0) / 365.0) for e in exps}
    fwd = forwards(opts, futs, DF_by_exp)
    opts = opts.merge(fwd, on=["symbol", "expiry_date"], how="left")
    opts["discount_factor"] = np.exp(-r * opts["T"])
    opts["rate_pct"] = rate_pct
    opts["rate_series"] = rate_series

    status = pd.Series("PENDING", index=opts.index)
    status[opts.option_type.isin(["CA", "PA"])] = "AMERICAN"
    status[(status == "PENDING") & (opts.days_to_expiry <= 0)] = "EXPIRY_DAY"
    status[(status == "PENDING") & opts.forward.isna()] = "NO_FORWARD"
    is_call = (opts.option_type == "CE").to_numpy()
    F, K, DF, P = opts.forward.to_numpy(), opts.strike_price.to_numpy(), opts.discount_factor.to_numpy(), opts.close.to_numpy()
    intrinsic = DF * np.where(is_call, np.maximum(F - K, 0), np.maximum(K - F, 0))
    upper = DF * np.where(is_call, F, K)
    pend = (status == "PENDING").to_numpy()
    status[pend & (P <= intrinsic + 1e-9)] = "BELOW_INTRINSIC"
    status[(status == "PENDING").to_numpy() & (P >= upper)] = "ABOVE_MAX"

    for c in ["implied_vol", "delta", "gamma", "vega", "theta"]:
        opts[c] = np.nan
    m = (status == "PENDING").to_numpy()
    if m.any():
        Fm, Km, Tm, DFm, Pm, cm = F[m], K[m], opts["T"].to_numpy()[m], DF[m], P[m], is_call[m]
        sig, ok = implied_vol(Pm, Fm, Km, Tm, DFm, cm)
        price, d1, d2 = black76(Fm, Km, Tm, DFm, sig, cm)
        sq = np.sqrt(Tm)
        delta = np.where(cm, DFm * ndtr(d1), DFm * (ndtr(d1) - 1))
        gamma = DFm * npdf(d1) / (Fm * sig * sq)
        vega = DFm * Fm * npdf(d1) * sq / 100.0
        theta_yr = -DFm * Fm * npdf(d1) * sig / (2 * sq) + r * price
        idx = opts.index[m]
        opts.loc[idx, "implied_vol"] = np.where(ok, sig, np.nan)
        opts.loc[idx, "delta"] = np.where(ok, delta, np.nan)
        opts.loc[idx, "gamma"] = np.where(ok, gamma, np.nan)
        opts.loc[idx, "vega"] = np.where(ok, vega, np.nan)
        opts.loc[idx, "theta"] = np.where(ok, theta_yr / 365.0, np.nan)
        status[idx] = np.where(ok, "OK", "NO_CONVERGENCE")
    opts["iv_status"] = status.values
    opts["moneyness"] = np.log(opts.strike_price / opts.forward)
    opts["model"] = "BLACK76"
    opts["schema_version"] = SCHEMA
    opts["computed_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = ["trade_date", "contract_id", "symbol", "underlying_type", "expiry_date", "strike_price",
            "option_type", "close", "settlement_price", "open_interest", "lot_size", "days_to_expiry",
            "forward", "forward_source", "parity_strike", "rate_pct", "rate_series", "discount_factor",
            "moneyness", "implied_vol", "delta", "gamma", "vega", "theta", "iv_status", "model",
            "schema_version", "computed_utc"]
    counts.update(opts.iv_status.value_counts().to_dict())
    fwd = fwd.assign(trade_date=day, rate_pct=rate_pct, rate_series=rate_series, schema_version=SCHEMA)
    return day, opts[cols], fwd, counts


def write(df, base, prefix, day):
    d = base / f"year={day.year}"
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{prefix}_{day:%Y%m%d}.parquet"
    tmp = target.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(target)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--end", default="2100-01-01")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    rates = load_rates()
    con = duckdb.connect()
    files = sorted(SILVER.glob("year=*/*.parquet"))
    ledger = pd.read_csv(LEDGER, dtype={"trade_date": str}) if LEDGER.exists() else pd.DataFrame()
    done_rows = []
    n = 0
    for path in files:
        stamp = "".join(ch for ch in path.stem if ch.isdigit())[-8:]
        if not (a.start.replace("-", "") <= stamp <= a.end.replace("-", "")):
            continue
        day = pd.Timestamp(stamp)
        target = OUT / f"year={day.year}" / f"nse_option_greeks_{stamp}.parquet"
        if not a.rebuild and target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
            continue
        res = process_day(path, rates, con)
        if res is None:
            continue
        day, greeks, fwd, counts = res
        if greeks is not None:
            write(greeks, OUT, "nse_option_greeks", day)
            write(fwd, FWD_OUT, "nse_option_forwards", day)
        done_rows += [{"trade_date": f"{day:%Y-%m-%d}", "iv_status": k, "rows": v} for k, v in counts.items()]
        n += 1
        if n % 100 == 0:
            print(f"{day:%Y-%m-%d} days={n} last={counts}", flush=True)
    if done_rows:
        new = pd.DataFrame(done_rows)
        if not ledger.empty:
            ledger = ledger[~ledger.trade_date.isin(new.trade_date)]
        ledger = pd.concat([ledger, new]).sort_values(["trade_date", "iv_status"])
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        ledger.to_csv(LEDGER, index=False)
    print(f"\nNSE OPTION GREEKS SUMMARY  days processed: {n}")
    if not ledger.empty:
        print(ledger.groupby("iv_status").rows.sum().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
