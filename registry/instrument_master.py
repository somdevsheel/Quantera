import sqlite3
import uuid

from pathlib import Path
from datetime import datetime, timezone


DB_PATH = Path("data/registry/quantera_registry.db")

# Permanent namespace for generating deterministic instrument IDs.
QUANTERA_NAMESPACE = uuid.UUID("a91f9b37-1c28-4a62-9c91-5f1a7d8e2b40")


class InstrumentMaster:

    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row

        self.create_tables()

    def create_tables(self):
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS instruments (
                    instrument_id TEXT PRIMARY KEY,
                    canonical_symbol TEXT NOT NULL,
                    instrument_name TEXT,
                    asset_class TEXT NOT NULL,
                    country TEXT,
                    exchange_name TEXT,
                    exchange_mic TEXT,
                    currency TEXT,
                    isin TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(canonical_symbol, exchange_mic)
                )
            """)

            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS provider_symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(instrument_id)
                        REFERENCES instruments(instrument_id),
                    UNIQUE(provider, provider_symbol)
                )
            """)

    @staticmethod
    def generate_instrument_id(canonical_symbol, exchange_mic):
        identity = f"{canonical_symbol.upper()}:{exchange_mic.upper()}"
        return str(uuid.uuid5(QUANTERA_NAMESPACE, identity))

    def add_instrument(
        self,
        canonical_symbol,
        asset_class,
        country,
        exchange_name,
        exchange_mic,
        currency,
        instrument_name=None,
        isin=None,
        provider="yahoo_finance",
        provider_symbol=None,
    ):
        now = datetime.now(timezone.utc).isoformat()

        canonical_symbol = canonical_symbol.upper()
        exchange_mic = exchange_mic.upper()

        instrument_id = self.generate_instrument_id(
            canonical_symbol,
            exchange_mic
        )

        with self.connection:
            self.connection.execute("""
                INSERT INTO instruments (
                    instrument_id, canonical_symbol, instrument_name,
                    asset_class, country, exchange_name, exchange_mic,
                    currency, isin, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                ON CONFLICT(canonical_symbol, exchange_mic)
                DO UPDATE SET
                    instrument_name = COALESCE(
                        excluded.instrument_name,
                        instruments.instrument_name
                    ),
                    currency = excluded.currency,
                    updated_at = excluded.updated_at
            """, (
                instrument_id,
                canonical_symbol,
                instrument_name,
                asset_class,
                country,
                exchange_name,
                exchange_mic,
                currency,
                isin,
                now,
                now,
            ))

            if provider_symbol:
                self.connection.execute("""
                    INSERT INTO provider_symbols (
                        instrument_id, provider,
                        provider_symbol, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(provider, provider_symbol)
                    DO UPDATE SET instrument_id = excluded.instrument_id
                """, (
                    instrument_id,
                    provider,
                    provider_symbol,
                    now,
                ))

        print(f"Registered: {canonical_symbol}")
        print(f"Instrument ID: {instrument_id}")

    def list_instruments(self):
        rows = self.connection.execute("""
            SELECT i.*, p.provider, p.provider_symbol
            FROM instruments i
            LEFT JOIN provider_symbols p
                ON i.instrument_id = p.instrument_id
            ORDER BY i.canonical_symbol
        """).fetchall()

        return [dict(row) for row in rows]

    def close(self):
        self.connection.close()


def initialize_instruments():
    master = InstrumentMaster()

    instruments = [
        {
            "canonical_symbol": "RELIANCE",
            "asset_class": "EQUITY",
            "country": "IN",
            "exchange_name": "National Stock Exchange of India",
            "exchange_mic": "XNSE",
            "currency": "INR",
            "instrument_name": "Reliance Industries Limited",
            "provider_symbol": "RELIANCE.NS",
        },
        {
            "canonical_symbol": "TCS",
            "asset_class": "EQUITY",
            "country": "IN",
            "exchange_name": "National Stock Exchange of India",
            "exchange_mic": "XNSE",
            "currency": "INR",
            "instrument_name": "Tata Consultancy Services Limited",
            "provider_symbol": "TCS.NS",
        },
        {
            "canonical_symbol": "INFY",
            "asset_class": "EQUITY",
            "country": "IN",
            "exchange_name": "National Stock Exchange of India",
            "exchange_mic": "XNSE",
            "currency": "INR",
            "instrument_name": "Infosys Limited",
            "provider_symbol": "INFY.NS",
        },
        {
            "canonical_symbol": "AAPL",
            "asset_class": "EQUITY",
            "country": "US",
            "exchange_name": "NASDAQ",
            "exchange_mic": "XNAS",
            "currency": "USD",
            "instrument_name": "Apple Inc.",
            "provider_symbol": "AAPL",
        },
        {
            "canonical_symbol": "MSFT",
            "asset_class": "EQUITY",
            "country": "US",
            "exchange_name": "NASDAQ",
            "exchange_mic": "XNAS",
            "currency": "USD",
            "instrument_name": "Microsoft Corporation",
            "provider_symbol": "MSFT",
        },
        {
            "canonical_symbol": "NVDA",
            "asset_class": "EQUITY",
            "country": "US",
            "exchange_name": "NASDAQ",
            "exchange_mic": "XNAS",
            "currency": "USD",
            "instrument_name": "NVIDIA Corporation",
            "provider_symbol": "NVDA",
        },
        {
            "canonical_symbol": "GOOGL",
            "asset_class": "EQUITY",
            "country": "US",
            "exchange_name": "NASDAQ",
            "exchange_mic": "XNAS",
            "currency": "USD",
            "instrument_name": "Alphabet Inc. Class A",
            "provider_symbol": "GOOGL",
        },
    ]

    for item in instruments:
        master.add_instrument(**item)

    print("\nInstrument Master:")
    for instrument in master.list_instruments():
        print(instrument)

    master.close()


if __name__ == "__main__":
    initialize_instruments()
