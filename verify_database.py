"""Database verification script for the current schema."""

import os
import sqlite3

DB_FILE = os.getenv("AK_DB", "heart_predictions.db")

print("Database Verification Report\n")
print("=" * 60)

try:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]

    print(f"Database file: {DB_FILE}")
    print(f"Tables found: {len(tables)}")
    print("-" * 60)

    for table_name in tables:
        cur.execute(f"PRAGMA table_info({table_name})")
        columns = cur.fetchall()

        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cur.fetchone()[0]

        print(f"\n[OK] {table_name.upper()}")
        print(f"  Rows: {count} | Columns: {len(columns)}")
        print(f"  Column names: {', '.join(col[1] for col in columns)}")

        if table_name == "doctors" and count > 0:
            cur.execute(
                """
                SELECT id, name, specialization, city, state
                FROM doctors
                ORDER BY id
                LIMIT 3
                """
            )
            print("  Sample doctors:")
            for doctor_id, name, specialization, city, state in cur.fetchall():
                location = ", ".join(part for part in [city, state] if part)
                print(
                    f"    - #{doctor_id}: {name} | {specialization or 'N/A'}"
                    f" | {location or 'No location'}"
                )

        if table_name == "appointments":
            cur.execute(
                "SELECT COUNT(*) FROM appointments WHERE status = 'scheduled'"
            )
            scheduled = cur.fetchone()[0]
            print(f"  Scheduled appointments: {scheduled}")

        if table_name == "users":
            print(f"  Registered users: {count}")

        if table_name == "predictions":
            print(f"  Saved predictions: {count}")

    print("\n" + "=" * 60)
    print("Database verification complete.")
    conn.close()

except FileNotFoundError:
    print(f"Database file not found: {DB_FILE}")
    print("Make sure the app has been started at least once.")
except Exception as exc:
    print(f"Error: {exc}")
    raise
