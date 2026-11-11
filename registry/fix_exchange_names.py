import sqlite3

db = sqlite3.connect("data/registry/quantera_registry.db")

query = """
UPDATE instruments
SET exchange_name = (
    SELECT exchange_name
    FROM exchanges
    WHERE mic = instruments.exchange_mic
),
updated_at = CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1 FROM exchanges
    WHERE mic = instruments.exchange_mic
)
AND TRIM(exchange_name) != TRIM((
    SELECT exchange_name
    FROM exchanges
    WHERE mic = instruments.exchange_mic
))
"""

cursor = db.execute(query)
db.commit()

print("Corrected rows:", cursor.rowcount)

db.close()
