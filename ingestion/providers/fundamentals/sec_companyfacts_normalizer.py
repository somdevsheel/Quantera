import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

MANIFEST = Path("data/validation/fundamentals/sec_companyfacts_manifest.csv")
BRONZE = Path("data/bronze/fundamentals/sec_companyfacts")
SILVER = Path("data/silver/fundamentals")
VALIDATION = Path("data/validation/fundamentals")

STATEMENT_MAP = {
    "income_statement": [
        "Revenue", "Revenues", "SalesRevenue", "CostOfRevenue",
        "CostOfGoodsAndServicesSold", "GrossProfit", "OperatingIncome",
        "OperatingExpenses", "ResearchAndDevelopment", "SellingGeneralAndAdministrative",
        "AllocatedShareBasedCompensationExpense",
        "DepreciationDepletionAndAmortization", "InterestExpense",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
        "IncomeTaxExpenseBenefit", "NetIncomeLoss", "EarningsPerShare",
        "WeightedAverageNumberOfSharesOutstanding"
    ],
    "balance_sheet": [
        "Assets", "AssetsCurrent", "CashAndCashEquivalents",
        "AccountsReceivable", "Inventory", "PropertyPlantAndEquipment",
        "Goodwill", "IntangibleAssets", "Liabilities", "LiabilitiesCurrent",
        "AccountsPayable", "LongTermDebt", "ShortTermBorrowings",
        "StockholdersEquity", "RetainedEarnings",
        "MinorityInterest",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "LiabilitiesAndStockholdersEquity"
    ],
    "cash_flow_statement": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInFinancingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "ShareBasedCompensation", "DepreciationDepletionAndAmortization",
        "PaymentsOfDividends", "PaymentsForRepurchaseOfCommonStock",
        "PaymentsRelatedToTaxWithholdingForShareBasedCompensation",
        "ExcessTaxBenefitFromShareBasedCompensationFinancingActivities",
        "ExcessTaxBenefitFromShareBasedCompensationOperatingActivities",
        "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions"
    ]
}


IFRS_MAP = {
    "income_statement": {
        "Revenue", "RevenueFromContractsWithCustomers", "RevenueFromSaleOfGoods",
        "CostOfSales", "GrossProfit", "OperatingExpenseExcludingCostOfSales",
        "ResearchAndDevelopmentExpense", "GeneralAndAdministrativeExpense",
        "SalesAndMarketingExpense", "OtherOperatingIncomeExpense",
        "ProfitLossFromOperatingActivities", "FinanceCosts", "FinanceIncome",
        "ProfitLossBeforeTax", "IncomeTaxExpenseContinuingOperations",
        "ProfitLoss", "AccountingProfit", "BasicEarningsLossPerShare",
        "DilutedEarningsLossPerShare", "WeightedAverageShares",
        "DepreciationExpense", "AmortisationExpense"
    },
    "balance_sheet": {
        "Assets", "CurrentAssets", "NoncurrentAssets", "CashAndCashEquivalents",
        "Inventories", "FinishedGoods", "RawMaterials", "WorkInProgress",
        "CurrentTradeReceivables", "PropertyPlantAndEquipment",
        "IntangibleAssetsAndGoodwill", "RightofuseAssets", "OtherAssets",
        "OtherCurrentAssets", "OtherNoncurrentAssets", "Liabilities",
        "CurrentLiabilities", "NoncurrentLiabilities", "Equity",
        "EquityAndLiabilities", "EquityAttributableToOwnersOfParent",
        "RetainedEarnings", "IssuedCapital", "AdditionalPaidinCapital",
        "ShorttermBorrowings", "LongtermBorrowings", "LeaseLiabilities",
        "CurrentLeaseLiabilities", "NoncurrentLeaseLiabilities",
        "DeferredTaxAssets", "DeferredTaxLiabilities",
        "CurrentTaxLiabilitiesCurrent",
        "TradeAndOtherCurrentPayablesToTradeSuppliers"
    },
    "cash_flow_statement": {
        "CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromUsedInInvestingActivities",
        "CashFlowsFromUsedInFinancingActivities",
        "CashFlowsFromUsedInOperations",
        "CashFlowsFromUsedInIncreaseDecreaseInCurrentBorrowings",
        "CashAdvancesAndLoansMadeToOtherPartiesClassifiedAsInvestingActivities",
        "CashReceiptsFromRepaymentOfAdvancesAndLoansMadeToOtherPartiesClassifiedAsInvestingActivities",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
        "ProceedsFromSalesOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "ProceedsFromCurrentBorrowings", "ProceedsFromNoncurrentBorrowings",
        "RepaymentsOfNoncurrentBorrowings", "DividendsPaid",
        "DividendsPaidClassifiedAsFinancingActivities",
        "InterestPaidClassifiedAsFinancingActivities",
        "InterestReceivedClassifiedAsInvestingActivities",
        "IncomeTaxesPaidClassifiedAsOperatingActivities",
        "EffectOfExchangeRateChangesOnCashAndCashEquivalents",
        "IncreaseDecreaseInCashAndCashEquivalents",
        "PaymentsOfFinanceLeaseLiabilitiesClassifiedAsFinancingActivities",
        "PaymentsOfLeaseLiabilitiesClassifiedAsFinancingActivities",
        "ProceedsFromIssuingShares", "ProceedsFromIssueOfBondsNotesAndDebentures"
    }
}

def classify_ifrs(concept):
    for statement, concepts in IFRS_MAP.items():
        if concept in concepts:
            return statement
    return "unclassified"

EQUITY_CONCEPTS = {
    "StockIssuedDuringPeriodValueShareBasedCompensation",
    "StockIssuedDuringPeriodSharesShareBasedCompensation",
    "StockIssuedDuringPeriodValueShareBasedCompensationGross",
    "StockIssuedDuringPeriodSharesShareBasedCompensationGross",
    "StockGrantedDuringPeriodValueSharebasedCompensation",
    "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans",
    "ProceedsFromIssuingShares",
}

def classify(concept):
    for statement, concepts in STATEMENT_MAP.items():
        if concept in concepts:
            return statement

    if concept in EQUITY_CONCEPTS:
        return "equity_statement"

    if "ShareBasedCompensation" in concept or "SharebasedCompensation" in concept:
        return "supplementary_disclosures"

    return "unclassified"

def main():
    manifest = pd.read_csv(MANIFEST, dtype={"cik": "string"})
    manifest = manifest[manifest["status"] == "SUCCESS"]
    manifest = manifest.drop_duplicates("ticker", keep="last")

    outputs = {
        "income_statement": [],
        "balance_sheet": [],
        "cash_flow_statement": [],
        "equity_statement": [],
        "supplementary_disclosures": [],
        "unclassified": []
    }
    failures = []

    for index, item in enumerate(manifest.itertuples(), 1):
        try:
            path = Path(item.local_file)
            if not path.exists():
                matches = list(BRONZE.glob(f"*/{item.ticker}_*.json"))
                if not matches:
                    raise FileNotFoundError(f"No local Bronze JSON for {item.ticker}")
                path = max(matches, key=lambda x: x.stat().st_mtime)

            with path.open() as f:
                company = json.load(f)

            cik = str(company["cik"]).zfill(10)
            entity = company.get("entityName", item.company)
            source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

            for taxonomy, concepts in company.get("facts", {}).items():
                for concept, details in concepts.items():
                    category = classify(concept) if taxonomy == "us-gaap" else classify_ifrs(concept) if taxonomy == "ifrs-full" else "unclassified"
                    for unit, observations in details.get("units", {}).items():
                        for obs in observations:
                            outputs[category].append({
                                "ticker": item.ticker,
                                "cik": cik,
                                "company_name": entity,
                                "taxonomy": taxonomy,
                                "concept": concept,
                                "label": details.get("label"),
                                "description": details.get("description"),
                                "unit": unit,
                                "value": obs.get("val"),
                                "period_start": obs.get("start"),
                                "period_end": obs.get("end"),
                                "fact_type": "DURATION" if obs.get("start") else "INSTANT",
                                "period_days": (pd.Timestamp(obs["end"]) - pd.Timestamp(obs["start"])).days if obs.get("start") and obs.get("end") else None,
                                "filed": obs.get("filed"),
                                "form": obs.get("form"),
                                "fy": obs.get("fy"),
                                "fp": obs.get("fp"),
                                "frame": obs.get("frame"),
                                "accn": obs.get("accn"),
                                "source_file": str(path),
                                "source_sha256": source_sha256
                            })
            print(f"Processed {index}/{len(manifest)}: {item.ticker}", flush=True)
        except Exception as exc:
            failures.append({"ticker": item.ticker, "error": str(exc)})
            print(f"FAILED {index}/{len(manifest)}: {item.ticker}: {exc}", flush=True)

    SILVER.mkdir(parents=True, exist_ok=True)
    VALIDATION.mkdir(parents=True, exist_ok=True)

    for category, records in outputs.items():
        frame = pd.DataFrame(records)
        if not frame.empty:
            frame = frame.drop_duplicates(
                subset=["ticker", "taxonomy", "concept", "unit", "value",
                        "period_start", "period_end", "fact_type", "period_days", "filed", "form", "accn"]
            )
            target = SILVER / f"{category}.parquet"
            frame.to_parquet(target, index=False, compression="zstd")
            print(f"{category}: {len(frame):,} records -> {target}")
        else:
            print(f"{category}: 0 records")

    pd.DataFrame(failures).to_csv(
        VALIDATION / "sec_normalization_failures.csv", index=False
    )
    print(f"Companies processed: {len(manifest)-len(failures)}/{len(manifest)}")
    print(f"Failures: {len(failures)}")

if __name__ == "__main__":
    main()
