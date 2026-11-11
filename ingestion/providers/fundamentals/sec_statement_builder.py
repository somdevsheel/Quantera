"""Point-in-time statements + balance-sheet reconciliation from SEC filing facts.

Input : data/silver/fundamentals/filing_facts/cik=*/<accession>.parquet
Gold  : data/gold/fundamentals/sec_statements.parquet       (one row per filing)
        data/validation/fundamentals/sec_balance_sheet_reconciliation.csv

Selection rules (no hard-coded company adjustments):
  * Primary context = facts with no dimensions ("[]") whose period ends on the
    filing's dei:DocumentPeriodEndDate (instant for balance sheet; for flows the
    duration ending then — quarterly preferred over YTD for 10-Q, annual for 10-K/20-F).
  * Dimensional facts are never summed into totals.
  * Point in time: each row carries filing_date (knowledge date); restated
    values in later filings are separate rows, never overwrites.
Reconciliation: Assets vs LiabilitiesAndStockholdersEquity when reported; else
Assets vs Liabilities + TemporaryEquity + equity-incl-NCI (or equity + NCI).
Temporary equity is taken from the undimensioned total if reported, otherwise
recorded as missing — never from summing members. Status = BALANCED (|diff| <=
0.5% of assets or <= 1e6), UNBALANCED, or INCOMPLETE.
"""
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
FACTS = ROOT / "data/silver/fundamentals/filing_facts"
GOLD = ROOT / "data/gold/fundamentals/sec_statements.parquet"
RECON = ROOT / "data/validation/fundamentals/sec_balance_sheet_reconciliation.csv"

INSTANT = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "liabilities_and_equity": ["LiabilitiesAndStockholdersEquity"],
    "equity_parent": ["StockholdersEquity"],
    "equity_incl_nci": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "nci": ["MinorityInterest"],
    "temporary_equity": ["TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
                         "TemporaryEquityCarryingAmountAttributableToParent", "RedeemableNoncontrollingInterestEquityCarryingAmount"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "shares_outstanding": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}
DURATION = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}


def pick(df, names):
    for n in names:
        hit = df[df.concept == n]
        if not hit.empty:
            return hit.value.iloc[0], n
    return None, None


def build_one(path):
    f = pd.read_parquet(path)
    meta = f.iloc[0]
    dei_end = f.loc[f.concept == "DocumentPeriodEndDate", "raw_value"]
    period_end = pd.to_datetime(dei_end.iloc[0], errors="coerce") if not dei_end.empty else pd.NaT
    prim = f[(f.dimensions.isin(["[]", None])) & f.value.notna()].copy()
    prim["end"] = pd.to_datetime(prim.period_end, errors="coerce")
    prim["start"] = pd.to_datetime(prim.period_start, errors="coerce")
    if pd.isna(period_end):
        period_end = prim.loc[prim.concept == "Assets", "end"].max()
    row = {"ticker": meta.ticker, "cik": meta.cik, "accession_number": meta.accession_number,
           "form": meta.get("form"), "filing_date": meta.get("filing_date"),
           "cik_role": meta.get("cik_role"), "period_end": period_end}
    inst = prim[(prim.fact_type == "INSTANT") & (prim.end == period_end)]
    dur = prim[(prim.fact_type == "DURATION") & (prim.end == period_end)].copy()
    dur["days"] = (dur.end - dur.start).dt.days
    annual = str(meta.get("form")) in ("10-K", "20-F", "40-F")
    want = dur[dur.days.between(340, 380)] if annual else dur[dur.days.between(80, 100)]
    row["flow_period_days"] = int(want.days.iloc[0]) if not want.empty else None
    for k, names in INSTANT.items():
        row[k], row[k + "_concept"] = pick(inst, names)
    # Dimensional temporary-equity facts at period end: kept as candidates only.
    d = f[(~f.dimensions.isin(["[]", None])) & f.value.notna() & (f.fact_type == "INSTANT")
          & (pd.to_datetime(f.period_end, errors="coerce") == period_end)
          & f.concept.str.startswith(("TemporaryEquity", "RedeemableNoncontrollingInterest"))]
    cands = []
    for r_ in d.itertuples():
        dims = json.loads(r_.dimensions)
        if len(dims) == 1:
            cands.append({"axis": dims[0]["dimension"], "member": dims[0]["member"],
                          "concept": r_.concept, "value": r_.value})
    row["temp_equity_dimensional_candidates"] = json.dumps(cands) if cands else None
    for k, names in DURATION.items():
        row[k], row[k + "_concept"] = pick(want if not want.empty else dur, names)
    return row


def reconcile(r):
    """Primary test: Assets = Liabilities + TemporaryEquity + Equity(incl. NCI).
    LiabilitiesAndStockholdersEquity is only a fallback (filers report it equal to
    Assets, so it cannot catch a mis-selected equity component)."""
    def has(v):
        return v is not None and pd.notna(v)
    a = r["assets"]
    if not has(a):
        return "INCOMPLETE", None, "no Assets"
    eq = r["equity_incl_nci"] if has(r["equity_incl_nci"]) else (
        r["equity_parent"] + (r["nci"] if has(r["nci"]) else 0) if has(r["equity_parent"]) else None)
    tol = max(1e6, 0.005 * abs(a))
    if has(r["liabilities"]) and eq is not None:
        temp = r["temporary_equity"] if has(r["temporary_equity"]) else 0
        diff = a - (r["liabilities"] + temp + eq)
        method = "COMPONENTS" + ("+TEMP_EQUITY" if temp else "")
        if abs(diff) <= tol:
            return "BALANCED", diff, method
        raw = r.get("temp_equity_dimensional_candidates")
        cands = json.loads(raw) if isinstance(raw, str) and raw else []  # NaN when no candidates
        if cands and not temp:
            # Candidates: each axis's member sum, and combinations of distinct axes.
            by_axis = {}
            for c in cands:
                by_axis.setdefault(c["axis"], []).append(c)
            axis_sums = {ax: sum(c["value"] for c in cs) for ax, cs in by_axis.items()}
            options = []
            for k in range(1, len(axis_sums) + 1):
                for combo in combinations(sorted(axis_sums), k):
                    options.append((combo, sum(axis_sums[a] for a in combo)))
            for combo, val in options:
                if abs(diff - val) <= tol:
                    members = [c["member"] for a in combo for c in by_axis[a]]
                    return ("BALANCED_DIMENSIONAL_TEMP_EQUITY", diff - val,
                            f"COMPONENTS+TEMP_EQUITY_FROM_DIMENSIONS axes={list(combo)} members={members}")
        # Many filers (e.g. AAPL) never tag a Liabilities total for some periods,
        # or carry temporary equity only as dimensional members: say so explicitly.
        return "UNBALANCED", diff, method + ("" if temp else " (no undimensioned temporary-equity total)")
    if has(r["liabilities_and_equity"]):
        diff = a - r["liabilities_and_equity"]
        return ("BALANCED" if abs(diff) <= tol else "UNBALANCED"), diff, "TOTAL_ONLY (components incomplete)"
    return "INCOMPLETE", None, "no Liabilities total or equity total"


def main():
    rows = []
    for p in sorted(FACTS.glob("cik=*/*.parquet")):
        try:
            rows.append(build_one(p))
        except Exception as exc:  # noqa: BLE001
            print("FAILED", p, exc)
    g = pd.DataFrame(rows)
    rec = g.apply(lambda r: pd.Series(reconcile(r), index=["recon_status", "recon_difference", "recon_method"]), axis=1)
    g = pd.concat([g, rec], axis=1)
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(GOLD, index=False, compression="zstd")
    g[["ticker", "cik", "accession_number", "form", "filing_date", "period_end", "assets", "liabilities",
       "liabilities_and_equity", "temporary_equity", "temp_equity_dimensional_candidates",
       "equity_parent", "nci", "equity_incl_nci",
       "recon_status", "recon_method", "recon_difference"]].to_csv(RECON, index=False)
    print("Filings:", len(g), "| companies:", g.ticker.nunique())
    print(g.recon_status.value_counts().to_string())
    print(g.groupby("form").recon_status.value_counts().unstack(fill_value=0).to_string())
    print("Revenue found:", round(g.revenue.notna().mean(), 3), "| net income:", round(g.net_income.notna().mean(), 3))


if __name__ == "__main__":
    main()
