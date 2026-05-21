import os
import json
import logging
import chromadb
import psycopg2
from psycopg2 import extras
from pathlib import Path
from typing import List, Dict
from config import Config
from chromadb.utils import embedding_functions

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MigrateOpenAI")

# Configuration
OLD_BOOKSTACK_PATH = "./chromaDB"
UNIFIED_DB_PATH = "./data/chroma_db"

EMAIL_COLLECTION_NAME = "new_support_emails"
BOOKSTACK_COLLECTION_NAME = "bookstack_db"

def migrate_bookstack():
    logger.info("🎬 Phase 1: Migrating Bookstack Documentation")
    if not os.path.exists(OLD_BOOKSTACK_PATH):
        logger.warning(f"⚠️ Old Bookstack directory '{OLD_BOOKSTACK_PATH}' not found. Skipping document migration.")
        return

    logger.info(f"🔌 Connecting to old Bookstack DB at {OLD_BOOKSTACK_PATH}")
    old_client = chromadb.PersistentClient(path=OLD_BOOKSTACK_PATH)
    try:
        old_collection = old_client.get_collection(BOOKSTACK_COLLECTION_NAME)
        old_data = old_collection.get()
    except Exception as e:
        logger.error(f"❌ Failed to read from old bookstack_db: {e}")
        return

    docs = old_data.get('documents', [])
    metas = old_data.get('metadatas', [])
    ids = old_data.get('ids', [])

    if not docs:
        logger.warning("⚠️ No Bookstack documentation found in the old database.")
        return

    logger.info(f"📚 Retrieved {len(docs)} documents/chunks from old Bookstack DB.")

    # Initialize new unified database collection with OpenAI Embedder
    openai_ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=Config.OPENAI_API_KEY,
        model_name="text-embedding-3-small"
    )
    new_client = chromadb.PersistentClient(path=UNIFIED_DB_PATH)
    new_collection = new_client.get_or_create_collection(
        name=BOOKSTACK_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
        embedding_function=openai_ef
    )

    BATCH_SIZE = 100
    for i in range(0, len(docs), BATCH_SIZE):
        batch_docs = docs[i : i + BATCH_SIZE]
        batch_metas = metas[i : i + BATCH_SIZE]
        batch_ids = ids[i : i + BATCH_SIZE]
        
        # Clean any metadata fields (ChromaDB needs pure types: str, int, float, bool)
        cleaned_metas = []
        for meta in batch_metas:
            cleaned = {k: str(v) if not isinstance(v, (int, float, bool)) else v for k, v in meta.items()}
            cleaned_metas.append(cleaned)

        new_collection.add(
            ids=batch_ids,
            documents=batch_docs,
            metadatas=cleaned_metas
        )
        logger.info(f"✅ Ingested Bookstack batch {i // BATCH_SIZE + 1} ({len(batch_docs)} chunks)")

    logger.info(f"🎉 Successfully migrated {len(docs)} Bookstack documentation chunks.")

def clean_html(html_text: str) -> str:
    if not html_text: return ""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html_text, "html.parser").get_text(separator=" ", strip=True)

def migrate_past_emails():
    logger.info("🎬 Phase 2: Migrating Support Email History from PostgreSQL")
    
    docs = []
    metas = []
    ids = []

    # Connect to PostgreSQL to retrieve all past customer support email messages
    try:
        logger.info("🔌 Connecting to PostgreSQL database...")
        conn = psycopg2.connect(
            host=Config.POSTGRES_HOST,
            port=Config.POSTGRES_PORT,
            database=Config.POSTGRES_DB,
            user=Config.POSTGRES_USER,
            password=Config.POSTGRES_PASSWORD
        )
        
        with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
            query = """
                SELECT 
                    m.message_id, 
                    t.subject, 
                    m.body_text, 
                    m.sender, 
                    m.timestamp, 
                    m.ticket_id,
                    t.is_authority
                FROM ticket_messages m
                JOIN tickets t ON m.ticket_id = t.ticket_id
                WHERE m.deleted_at IS NULL 
                  AND t.deleted_at IS NULL 
                  AND m.is_internal = FALSE
                ORDER BY m.timestamp ASC
            """
            cur.execute(query)
            rows = cur.fetchall()
            
            logger.info(f"📊 Retrieved {len(rows)} customer support messages from PostgreSQL.")
            
            for row in rows:
                clean_body = clean_html(row.get("body_text", ""))
                text_content = f"Subject: {row.get('subject')}\n\nBody: {clean_body}"
                
                docs.append(text_content)
                ids.append(row["message_id"])
                
                # Check is_resolved status by ticket
                # We will query ticket resolution or set default False
                metas.append({
                    "subject": row.get("subject") or "No Subject",
                    "sender": row.get("sender") or "unknown@sender.com",
                    "ticket_id": row["ticket_id"],
                    "is_resolved": False,  # Default resolved state
                    "is_authority": bool(row.get("is_authority", False)),
                    "received": str(row.get("timestamp"))
                })
                
        conn.close()
    except Exception as e:
        logger.error(f"❌ Failed to fetch emails from PostgreSQL: {e}")

    # Fallback to offline emails dump if PostgreSQL has no records or fails
    if not docs:
        EMAIL_DUMP_FILE = "data/emails_dump.json"
        emails = []
        if os.path.exists(EMAIL_DUMP_FILE):
            logger.info(f"📂 Loading existing offline emails backup from {EMAIL_DUMP_FILE}")
            with open(EMAIL_DUMP_FILE, "r", encoding="utf-8") as f:
                emails = json.load(f)
            
            for email in emails:
                try:
                    clean_body = clean_html(email.get("body", ""))
                    text_content = f"Subject: {email.get('subject')}\n\nBody: {clean_body}"
                    docs.append(text_content)
                    ids.append(email["id"])
                    metas.append({
                        "subject": email.get("subject") or "No Subject",
                        "sender": email.get("sender") or "unknown@sender.com",
                        "is_resolved": False,
                        "is_authority": False,
                        "received": email.get("received")
                    })
                except Exception as e:
                    logger.error(f"❌ Failed to parse email backup row: {e}")

    if not docs:
        logger.warning("⚠️ No past emails available for experience database migration.")
        return

    logger.info(f"📧 Ready to embed and ingest {len(docs)} emails into the unified database.")

    # Initialize new unified email database collection with OpenAI Embedder
    openai_ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=Config.OPENAI_API_KEY,
        model_name="text-embedding-3-small"
    )
    new_client = chromadb.PersistentClient(path=UNIFIED_DB_PATH)
    new_collection = new_client.get_or_create_collection(
        name=EMAIL_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
        embedding_function=openai_ef
    )

    BATCH_SIZE = 100
    for i in range(0, len(docs), BATCH_SIZE):
        batch_docs = docs[i : i + BATCH_SIZE]
        batch_metas = metas[i : i + BATCH_SIZE]
        batch_ids = ids[i : i + BATCH_SIZE]
        
        # Clean metadata fields
        cleaned_metas = []
        for meta in batch_metas:
            cleaned = {k: str(v) if not isinstance(v, (int, float, bool)) else v for k, v in meta.items()}
            cleaned_metas.append(cleaned)

        new_collection.add(
            ids=batch_ids,
            documents=batch_docs,
            metadatas=cleaned_metas
        )
        logger.info(f"🚀 Ingested email batch {i // BATCH_SIZE + 1}/{(len(docs) - 1) // BATCH_SIZE + 1}")

    logger.info("🎉 Email migration and re-embedding complete.")

def main():
    logger.info("=========================================")
    logger.info("🔥 UNIFIED CHROMA DB OPENAI MIGRATION STARTING")
    logger.info("=========================================")
    
    # Ensure unified data directory exists
    os.makedirs(os.path.dirname(UNIFIED_DB_PATH), exist_ok=True)
    
    # 1. Ingest/Re-embed documents
    migrate_bookstack()
    
    # 2. Fetch and embed emails from PostgreSQL
    migrate_past_emails()
    
    logger.info("=========================================")
    logger.info("✅ MIGRATION COMPLETED SUCCESSFULLY")
    logger.info("=========================================")

if __name__ == "__main__":
    main()
