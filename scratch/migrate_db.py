import psycopg2
from config import Config

def migrate():
    try:
        conn = psycopg2.connect(
            host=Config.POSTGRES_HOST,
            port=Config.POSTGRES_PORT,
            database=Config.POSTGRES_DB,
            user=Config.POSTGRES_USER,
            password=Config.POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        
        # Add metrics columns to tickets table
        columns = [
            ("agent_response_latency_avg", "INTEGER DEFAULT 0"),
            ("customer_response_latency_avg", "INTEGER DEFAULT 0"),
            ("total_resolution_time", "INTEGER DEFAULT 0"),
            ("first_response_time", "INTEGER DEFAULT 0")
        ]
        
        for col_name, col_type in columns:
            try:
                cur.execute(f"ALTER TABLE tickets ADD COLUMN {col_name} {col_type}")
                print(f"Added column {col_name}")
            except Exception as e:
                print(f"Column {col_name} might already exist: {e}")
                conn.rollback()
                cur = conn.cursor()
        
        # Add UNIQUE constraint for idempotency on ticket_events
        try:
            cur.execute("ALTER TABLE ticket_events ADD CONSTRAINT unique_event_ref UNIQUE (ticket_id, event_type, reference_id)")
            print("Added unique constraint to ticket_events")
        except Exception as e:
            print(f"Constraint might already exist: {e}")
            conn.rollback()
            cur = conn.cursor()

        conn.commit()
        conn.close()
        print("Database migration completed.")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate()
