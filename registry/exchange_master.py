import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("data/registry/quantera_registry.db")


EXCHANGES = [
    {
        "mic": "XNSE",
        "name": "National Stock Exchange of India",
        "country": "IN",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
    },
    {
        "mic": "XBOM",
        "name": "BSE Limited",
        "country": "IN",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
    },
    {
        "mic": "XNAS",
        "name": "NASDAQ",
        "country": "US",
        "currency": "USD",
        "timezone": "America/New_York",
    },
    {
        "mic": "XNYS",
        "name": "New York Stock Exchange",
        "country": "US",
        "currency": "USD",
        "timezone": "America/New_York",
    },
    {
        "mic": "XLON",
        "name": "London Stock Exchange",
        "country": "GB",
        "currency": "GBP",
        "timezone": "Europe/London",
    },
    {
        "mic": "XJPX",
        "name": "Japan Exchange Group",
        "country": "JP",
        "currency": "JPY",
        "timezone": "Asia/Tokyo",
    },
    {
        "mic": "XHKG",
        "name": "Hong Kong Stock Exchange",
        "country": "HK",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
    },
    {
        "mic": "XASX",
        "name": "Australian Securities Exchange",
        "country": "AU",
        "currency": "AUD",
        "timezone": "Australia/Sydney",
    },
    {
        "mic": "XETR",
        "name": "Deutsche Börse Xetra",
        "country": "DE",
        "currency": "EUR",
        "timezone": "Europe/Berlin",
    },
    {
        "mic": "XPAR",
        "name": "Euronext Paris",
        "country": "FR",
        "currency": "EUR",
        "timezone": "Europe/Paris",
    },
]


def initialize_exchanges():
    connection = sqlite3.connect(DB_PATH)

    with connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS exchanges (
                mic TEXT PRIMARY KEY,
                exchange_name TEXT NOT NULL,
                country TEXT NOT NULL,
                currency TEXT NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                updated_at TEXT NOT NULL
            )
        """)

        for exchange in EXCHANGES:
            connection.execute("""
                INSERT INTO exchanges (
                    mic, exchange_name, country,
                    currency, timezone, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)
                ON CONFLICT(mic) DO UPDATE SET
                    exchange_name = excluded.exchange_name,
                    country = excluded.country,
                    currency = excluded.currency,
                    timezone = excluded.timezone,
                    updated_at = excluded.updated_at
            """, (
                exchange["mic"],
                exchange["name"],
                exchange["country"],
                exchange["currency"],
                exchange["timezone"],
                datetime.now(timezone.utc).isoformat(),
            ))

    rows = connection.execute("""
        SELECT mic, exchange_name, country, currency, timezone
        FROM exchanges
        ORDER BY country, exchange_name
    """).fetchall()

    print(f"\nExchange registry: {len(rows)} exchanges")

    for row in rows:
        print(row)

    connection.close()


if __name__ == "__main__":
    initialize_exchanges()
