import psycopg2
from config import Config

def main():
    try:
        conn = psycopg2.connect(
            host=Config.POSTGRES_HOST,
            port=Config.POSTGRES_PORT,
            database=Config.POSTGRES_DB,
            user=Config.POSTGRES_USER,
            password=Config.POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        
        # 1. Total ticket messages
        cur.execute("SELECT COUNT(*) FROM ticket_messages")
        total = cur.fetchone()[0]
        
        # 2. Non-internal messages
        cur.execute("SELECT COUNT(*) FROM ticket_messages WHERE is_internal = FALSE")
        non_internal = cur.fetchone()[0]
        
        # 3. Soft-deleted messages
        cur.execute("SELECT COUNT(*) FROM ticket_messages WHERE deleted_at IS NOT NULL")
        deleted = cur.fetchone()[0]
        
        # 4. Total tickets
        cur.execute("SELECT COUNT(*) FROM tickets")
        total_tickets = cur.fetchone()[0]
        
        print(f"PostgreSQL Counts:")
        print(f"  Total ticket_messages: {total}")
        print(f"  Non-internal messages: {non_internal}")
        print(f"  Soft-deleted messages: {deleted}")
        print(f"  Total tickets: {total_tickets}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"PostgreSQL error: {e}")

if __name__ == "__main__":
    main()
