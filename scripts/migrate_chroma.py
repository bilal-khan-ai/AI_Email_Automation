import os
import sys
import logging
import chromadb
import psycopg2
from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_chroma_to_pg():
    """Migrates existing ChromaDB embeddings and metadata to PostgreSQL pgvector."""
    
    # 1. Initialize ChromaDB
    logger.info(f"Connecting to ChromaDB at {Config.CHROMA_DB_PATH}...")
    chroma_client = chromadb.PersistentClient(path=Config.CHROMA_DB_PATH)
    
    # 2. Connect to PostgreSQL
    logger.info(f"Connecting to PostgreSQL at {Config.POSTGRES_HOST}...")
    try:
        pg_conn = psycopg2.connect(
            host=Config.POSTGRES_HOST,
            port=Config.POSTGRES_PORT,
            database=Config.POSTGRES_DB,
            user=Config.POSTGRES_USER,
            password=Config.POSTGRES_PASSWORD
        )
        pg_cur = pg_conn.cursor()
        
        # 2b. Enable Extension
        pg_cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        pg_conn.commit()
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL or enable vector: {e}")
        return

    collections_to_migrate = [
        (Config.COLLECTION_NAME, "tickets"),
        (Config.BOOKSTACK_COLLECTION, "documentation")
    ]

    for source_col_name, target_type in collections_to_migrate:
        logger.info(f"Checking collection: {source_col_name} (Target Type: {target_type})...")
        try:
            # Check if collection exists
            all_collections = chroma_client.list_collections()
            collection_names = [c.name for c in all_collections]
            
            if source_col_name not in collection_names:
                logger.warning(f"Collection {source_col_name} not found. Skipping.")
                continue

            collection = chroma_client.get_collection(source_col_name)
            total_count = collection.count()
            logger.info(f"Found {total_count} records in {source_col_name}. Migrating in chunks...")

            # Ensure knowledge_base exists
            pg_cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id SERIAL PRIMARY KEY,
                    source_id TEXT UNIQUE,
                    content TEXT,
                    metadata JSONB,
                    embedding vector(384),
                    source_type TEXT
                )
            """)

            chunk_size = 100
            for i in range(0, total_count, chunk_size):
                logger.info(f"Processing chunk {i} to {min(i + chunk_size, total_count)}...")
                
                # Fetch only IDs for the chunk
                res = collection.get(
                    limit=chunk_size,
                    offset=i,
                    include=['embeddings', 'metadatas', 'documents']
                )
                
                ids = res['ids']
                embeddings = res['embeddings']
                metadatas = res['metadatas']
                documents = res['documents']

                for j in range(len(ids)):
                    # Convert list to pgvector string format "[v1,v2,...]"
                    vec_str = "[" + ",".join(map(str, embeddings[j])) + "]"
                    
                    pg_cur.execute("""
                        INSERT INTO knowledge_base (source_id, content, metadata, embedding, source_type)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (source_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding
                    """, (ids[j], documents[j], psycopg2.extras.Json(metadatas[j]), vec_str, target_type))

                pg_conn.commit()

            logger.info(f"Successfully migrated {total_count} records from {source_col_name}")

        except Exception as e:
            logger.error(f"Error migrating {source_col_name}: {e}")

    pg_cur.close()
    pg_conn.close()
    logger.info("Migration complete.")

if __name__ == "__main__":
    migrate_chroma_to_pg()
