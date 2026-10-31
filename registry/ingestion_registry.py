
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("data/registry/quantera_registry.db")


class IngestionRegistry:

    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row

        self.create_tables()

    def create_tables(self):

        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    instrument_id TEXT,
                    symbol TEXT NOT NULL,
                    provider TEXT NOT NULL,

                    asset_class TEXT,
                    exchange_mic TEXT,

                    start_date TEXT,
                    end_date TEXT,

                    record_count INTEGER DEFAULT 0,

                    local_file TEXT,
                    s3_bucket TEXT,
                    s3_key TEXT,

                    checksum_sha256 TEXT,

                    status TEXT NOT NULL,
                    error_message TEXT,

                    ingestion_timestamp TEXT NOT NULL,

                    UNIQUE (
                        provider,
                        symbol,
                        start_date,
                        end_date,
                        checksum_sha256
                    )
                )
            """)

            self.connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_registry_symbol
                ON ingestion_registry(symbol)
            """)

            self.connection.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_registry_status
                ON ingestion_registry(status)
            """)

        print("Quantera ingestion registry initialized.")

    @staticmethod
    def calculate_checksum(file_path):

        sha256 = hashlib.sha256()

        with open(file_path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                sha256.update(chunk)

        return sha256.hexdigest()

    def register_ingestion(
        self,
        symbol,
        provider,
        start_date,
        end_date,
        record_count,
        local_file,
        s3_bucket,
        s3_key,
        instrument_id=None,
        asset_class="EQUITY",
        exchange_mic=None,
        status="SUCCESS",
        error_message=None,
    ):

        local_file = Path(local_file)

        checksum = None

        if local_file.exists():
            checksum = self.calculate_checksum(local_file)

        timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        query = """
            INSERT INTO ingestion_registry (
                instrument_id,
                symbol,
                provider,
                asset_class,
                exchange_mic,
                start_date,
                end_date,
                record_count,
                local_file,
                s3_bucket,
                s3_key,
                checksum_sha256,
                status,
                error_message,
                ingestion_timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT (
                provider,
                symbol,
                start_date,
                end_date,
                checksum_sha256
            )
            DO UPDATE SET
                record_count = excluded.record_count,
                local_file = excluded.local_file,
                s3_bucket = excluded.s3_bucket,
                s3_key = excluded.s3_key,
                status = excluded.status,
                error_message = excluded.error_message,
                ingestion_timestamp = excluded.ingestion_timestamp
        """

        values = (
            instrument_id,
            symbol,
            provider,
            asset_class,
            exchange_mic,
            start_date,
            end_date,
            record_count,
            str(local_file),
            s3_bucket,
            s3_key,
            checksum,
            status,
            error_message,
            timestamp,
        )

        with self.connection:
            self.connection.execute(query, values)

        print(f"Registered: {symbol}")
        print(f"Status: {status}")
        print(f"SHA256: {checksum}")

    def list_ingestions(self):

        rows = self.connection.execute("""
            SELECT
                symbol,
                provider,
                start_date,
                end_date,
                record_count,
                status,
                s3_key,
                ingestion_timestamp
            FROM ingestion_registry
            ORDER BY id DESC
        """).fetchall()

        return [dict(row) for row in rows]

    def get_summary(self):

        row = self.connection.execute("""
            SELECT
                COUNT(*) AS total_ingestions,
                COUNT(DISTINCT symbol) AS unique_symbols,
                SUM(record_count) AS total_records,
                SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END)
                    AS successful_ingestions,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END)
                    AS failed_ingestions
            FROM ingestion_registry
        """).fetchone()

        return dict(row)

    def close(self):
        self.connection.close()


if __name__ == "__main__":

    registry = IngestionRegistry()

    print("\nRegistry Summary:")
    print(registry.get_summary())

    print("\nRegistered Ingestions:")
    for record in registry.list_ingestions():
        print(record)

    registry.close()
