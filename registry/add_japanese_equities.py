import csv
from pathlib import Path
from registry.instrument_master import InstrumentMaster

rows = [
("6758","6758.T","Sony Group"),
("8306","8306.T","Mitsubishi UFJ Financial"),
("9984","9984.T","SoftBank Group"),
("6861","6861.T","Keyence"),
("6501","6501.T","Hitachi"),
("8058","8058.T","Mitsubishi Corporation"),
("8035","8035.T","Tokyo Electron"),
("9432","9432.T","NTT"),
("9433","9433.T","KDDI"),
("4502","4502.T","Takeda Pharmaceutical"),
("6098","6098.T","Recruit Holdings"),
("7974","7974.T","Nintendo"),
("4063","4063.T","Shin-Etsu Chemical"),
("8766","8766.T","Tokio Marine"),
("8001","8001.T","Itochu"),
("8316","8316.T","Sumitomo Mitsui Financial"),
("8411","8411.T","Mizuho Financial"),
("7011","7011.T","Mitsubishi Heavy Industries"),
("7267","7267.T","Honda Motor"),
("7751","7751.T","Canon")
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
            symbol, provider_symbol, name, "JP",
            "XTKS", "JPY", "EQUITY", "ACTIVE"
        ])
        master.add_instrument(
            canonical_symbol=symbol,
            provider_symbol=provider_symbol,
            instrument_name=name,
            asset_class="EQUITY",
            country="JP",
            exchange_name="Tokyo Stock Exchange",
            exchange_mic="XTKS",
            currency="JPY",
            provider="yahoo_finance"
        )
        added += 1

print("Japanese equities added:", added)
print("Total universe:", len(existing) + added)
