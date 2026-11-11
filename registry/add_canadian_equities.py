import csv
from pathlib import Path
from registry.instrument_master import InstrumentMaster

rows = [
("RY","RY.TO","Royal Bank of Canada"),
("TD","TD.TO","Toronto-Dominion Bank"),
("SHOP","SHOP.TO","Shopify Inc."),
("ENB","ENB.TO","Enbridge Inc."),
("CNR","CNR.TO","Canadian National Railway"),
("CNQ","CNQ.TO","Canadian Natural Resources"),
("BMO","BMO.TO","Bank of Montreal"),
("BNS","BNS.TO","Bank of Nova Scotia"),
("CM","CM.TO","Canadian Imperial Bank of Commerce"),
("CP","CP.TO","Canadian Pacific Kansas City"),
("SU","SU.TO","Suncor Energy"),
("BCE","BCE.TO","BCE Inc."),
("TRP","TRP.TO","TC Energy"),
("MFC","MFC.TO","Manulife Financial"),
("SLF","SLF.TO","Sun Life Financial"),
("ATD","ATD.TO","Alimentation Couche-Tard"),
("WCN","WCN.TO","Waste Connections"),
("NTR","NTR.TO","Nutrien Ltd."),
("FTS","FTS.TO","Fortis Inc."),
("AEM","AEM.TO","Agnico Eagle Mines")
]

path = Path("reference/universes/global_equity_universe.csv")
with path.open(newline="") as f:
    existing = list(csv.DictReader(f))

known = {r["provider_symbol"] for r in existing}
master = InstrumentMaster()
added = 0

with path.open("a", newline="") as f:
    writer = csv.writer(f)
    for symbol, provider_symbol, name in rows:
        if provider_symbol in known:
            continue
        writer.writerow([
            symbol, provider_symbol, name, "CA",
            "XTSE", "CAD", "EQUITY", "ACTIVE"
        ])
        master.add_instrument(
            canonical_symbol=symbol,
            provider_symbol=provider_symbol,
            instrument_name=name,
            asset_class="EQUITY",
            country="CA",
            exchange_name="Toronto Stock Exchange",
            exchange_mic="XTSE",
            currency="CAD",
            provider="yahoo_finance"
        )
        added += 1

print("Canadian equities added:", added)
print("Total universe:", len(existing) + added)
