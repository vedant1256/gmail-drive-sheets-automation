from models import get_connection

def migrate():
    with get_connection() as conn:
        try:
            conn.execute("ALTER TABLE countries ADD COLUMN source_type TEXT DEFAULT 'Indian Embassy'")
            print("Added source_type column to countries table.")
        except Exception as e:
            print("countries table note:", e)

        try:
            conn.execute("ALTER TABLE scanned_messages ADD COLUMN source_type TEXT DEFAULT 'Indian Embassy'")
            print("Added source_type column to scanned_messages table.")
        except Exception as e:
            print("scanned_messages table note:", e)

        # Set all current countries to 'Indian Embassy'
        conn.execute("UPDATE countries SET source_type = 'Indian Embassy' WHERE source_type IS NULL OR source_type = ''")
        conn.commit()
    print("Migration finished successfully.")

if __name__ == "__main__":
    migrate()
