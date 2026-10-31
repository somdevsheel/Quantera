import sqlite3
from pathlib import Path

DB_PATH = Path("data/registry/quantera_registry.db")


def link_ingestions():
    connection = sqlite3.connect(DB_PATH)

    with connection:
        connection.execute("""
            UPDATE ingestion_registry
            SET instrument_id = (
                SELECT instruments.instrument_id
                FROM instruments
                WHERE instruments.canonical_symbol =
                    CASE
                        WHEN ingestion_registry.symbol LIKE '%.NS'
                        THEN REPLACE(ingestion_registry.symbol, '.NS', '')
                        ELSE ingestion_registry.symbol
                    END
                AND instruments.exchange_mic =
                    CASE
                        WHEN ingestion_registry.symbol LIKE '%.NS'
                        THEN 'XNSE'
                        ELSE 'XNAS'
                    END
            )
        """)

    rows = connection.execute("""
        SELECT symbol, instrument_id, provider, record_count
        FROM ingestion_registry
        ORDER BY symbol
    """).fetchall()

    print("\nIngestion-to-Instrument Mapping:")

    for row in rows:
        print(row)

    missing = [row for row in rows if row[1] is None]

    print(f"\nTotal records linked: {len(rows) - len(missing)}")
    print(f"Missing instrument IDs: {len(missing)}")

    connection.close()


if __name__ == "__main__":
    link_ingestions()
