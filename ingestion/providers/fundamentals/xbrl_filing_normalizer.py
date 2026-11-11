"""Filing-level XBRL fact extractor (SEC instance documents, incl. SEC-extracted
inline-XBRL instances). Usable as a CLI or via `extract()`.

Preserves concept, namespace, context, entity identifier, period, dimensions,
unit, raw value, numeric value, scale, sign, decimals, nil flag, source file.
"""
from pathlib import Path
from decimal import Decimal, InvalidOperation
import argparse
import json

from lxml import etree
import pandas as pd

XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def extract(source, ticker, cik, accession):
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(source)
    root = etree.parse(str(source), etree.XMLParser(huge_tree=True))

    # Extract contexts and dimensions
    contexts = {}

    for c in root.xpath('//*[local-name()="context"]'):
        cid = c.get("id")

        instant = c.xpath('./*[local-name()="period"]/*[local-name()="instant"]/text()')
        start = c.xpath('./*[local-name()="period"]/*[local-name()="startDate"]/text()')
        end = c.xpath('./*[local-name()="period"]/*[local-name()="endDate"]/text()')

        identifiers = c.xpath('.//*[local-name()="identifier"]/text()')

        dimensions = []
        for m in c.xpath(
            './/*[local-name()="explicitMember" or local-name()="typedMember"]'
        ):
            dimensions.append({
                "dimension": m.get("dimension"),
                "member": "".join(m.itertext()).strip()
            })

        contexts[cid] = {
            "entity_identifier": identifiers[0] if identifiers else None,
            "period_start": start[0] if start else None,
            "period_end": end[0] if end else (
                instant[0] if instant else None
            ),
            "fact_type": (
                "DURATION" if start else
                "INSTANT" if instant else None
            ),
            "dimensions": json.dumps(dimensions, sort_keys=True)
        }

    # Extract units
    units = {}

    for u in root.xpath('//*[local-name()="unit"]'):
        uid = u.get("id")
        units[uid] = "|".join(
            x.strip()
            for x in u.xpath('.//*[local-name()="measure"]/text()')
        )

    # Extract facts
    rows = []

    for e in root.iter():
        context_id = e.get("contextRef")

        if not context_id or context_id not in contexts:
            continue

        qname = etree.QName(e)
        concept = qname.localname
        namespace = qname.namespace
        raw = "".join(e.itertext()).strip()

        is_nil = (e.get(XSI_NIL) or e.get("nil")) == "true"
        unit_id = e.get("unitRef")
        scale = int(e.get("scale") or 0)
        sign = -1 if e.get("sign") == "-" else 1

        numeric_value = None

        if unit_id and raw and not is_nil:
            try:
                numeric_value = float(
                    Decimal(raw.replace(",", ""))
                    * (Decimal(10) ** scale)
                    * sign
                )
            except (InvalidOperation, ValueError):
                pass

        rows.append({
            "ticker": ticker.upper(),
            "cik": cik.zfill(10),
            "accession_number": accession,
            "concept": concept,
            "namespace": namespace,
            "context_id": context_id,
            **contexts[context_id],
            "unit": units.get(unit_id),
            "unit_id": unit_id,
            "raw_value": raw,
            "value": numeric_value,
            "scale": scale,
            "sign": e.get("sign"),
            "decimals": e.get("decimals"),
            "is_nil": is_nil,
            "source_file": str(source)
        })

    return pd.DataFrame(rows)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--accession", required=True)
    args = parser.parse_args()
    OUTPUT = Path(args.output)
    df = extract(args.source, args.ticker, args.cik, args.accession)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT, index=False)

    print("Ticker:", args.ticker.upper())
    print("Accession:", args.accession)
    print("Facts extracted:", len(df))
    print("Unique concepts:", df.concept.nunique())
    print("Unique contexts:", df.context_id.nunique())
    print("Dimension-bearing facts:", df.dimensions.ne("[]").sum())
    print("Saved:", OUTPUT)


if __name__ == "__main__":
    main()
