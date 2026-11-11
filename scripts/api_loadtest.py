"""Load test for the data API (docs/api_design.md §6, §8 item 6).

Start the server first:   python -m api
Then:                     python scripts/api_loadtest.py [--base http://127.0.0.1:8000] [--clients 8] [--requests 200]

Each scenario sends --requests requests from --clients concurrent clients, spread across
many instruments/dates (so it is not just a cache test), and reports p50/p95/max against the target.
"""
import argparse
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

NSE = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT", "AXISBANK", "KOTAKBANK",
       "HINDUNILVR", "BHARTIARTL", "MARUTI", "SUNPHARMA", "TATASTEEL", "WIPRO", "ONGC", "NTPC", "BAJFINANCE", "ACC"]
FO = ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "SBIN", "HDFCBANK"]
DAYS = ["2025-06-05", "2025-03-13", "2024-11-14", "2024-08-22", "2023-12-07", "2022-06-16", "2021-09-23", "2020-03-19"]

SCENARIOS = {
    "prices_20y_single_instrument": (150, lambda: f"/v1/prices/daily?symbol={random.choice(NSE)}&exchange=NSE"
                                               f"&start=2006-01-01&end=2026-11-06&series=EQ"),
    "prices_20y_split_adjusted": (150, lambda: f"/v1/prices/daily?symbol={random.choice(NSE)}&exchange=NSE"
                                            f"&start=2006-01-01&end=2026-11-06&series=EQ&adjust=split"),
    "fo_chain_one_day": (300, lambda: f"/v1/derivatives/fo/daily?symbol={random.choice(FO)}"
                                    f"&start={(d := random.choice(DAYS))}&end={d}&limit=100000"),
    "greeks_chain_one_day": (300, lambda: f"/v1/derivatives/options/greeks?symbol={random.choice(FO)}"
                                        f"&start={(d := random.choice(DAYS))}&end={d}&limit=100000"),
    "sec_statements": (150, lambda: f"/v1/fundamentals/statements?source=sec&ticker="
                                  f"{random.choice(['AAPL', 'MSFT', 'BLK', 'JPM', 'NVDA', 'GOOGL'])}"),
}


def run(client, base, scenario, n, clients):
    target, make = SCENARIOS[scenario]
    urls = [make() for _ in range(n)]

    def one(u):
        t = time.perf_counter()
        r = client.get(base + u)
        dt = (time.perf_counter() - t) * 1000
        return dt, r.status_code, len(r.content)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(clients) as ex:
        res = list(ex.map(one, urls))
    wall = time.perf_counter() - t0
    lat = sorted(r[0] for r in res)
    bad = [r for r in res if r[1] != 200]
    p95 = lat[int(len(lat) * 0.95) - 1]
    ok = p95 <= target and not bad
    print(f"{scenario:30s} p50 {statistics.median(lat):6.0f} ms | p95 {p95:6.0f} ms | max {lat[-1]:6.0f} ms | "
          f"target p95<{target} ms | {len(res) / wall:5.1f} req/s | non-200: {len(bad)} | {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--clients", type=int, default=8)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    random.seed(a.seed)
    with httpx.Client(timeout=60, limits=httpx.Limits(max_connections=a.clients)) as c:
        print("serving", c.get(a.base + "/v1/health").json()["release"], f"| {a.clients} clients x {a.requests} requests")
        for s in SCENARIOS:
            run(c, a.base, s, 16, a.clients)  # warm-up
        results = [run(c, a.base, s, a.requests, a.clients) for s in SCENARIOS]
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
