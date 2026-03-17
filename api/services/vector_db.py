import psycopg2
from psycopg2.extras import execute_values, Json
import numpy as np
from typing import List, Dict, Optional
import logging
import gc
from contextlib import contextmanager
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)


@contextmanager
def embedding_model_context(force_cpu: bool = False):
    """
    Context manager for embedding model with guaranteed cleanup.
    Maintains strict VRAM management from original implementation.
    """
    model = None
    device = None
    
    try:
        from sentence_transformers import SentenceTransformer
        
        if force_cpu:
            device = "cpu"
        else:
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        
        logger.info(f"⚙️  Loading embedding model (all-MiniLM-L6-v2) on {device}...")
        model = SentenceTransformer('all-MiniLM-L6-v2', device=device)
        logger.info("✅ Embedding model loaded")
        
        yield model, device
    
    except Exception as e:
        logger.error(f"❌ Embedding model context error: {e}")
        yield None, None
    
    finally:
        logger.info("🧹 Cleaning up embedding model...")
        
        if model is not None and device == "cuda":
            try:
                import torch
                model.to("cpu")
            except Exception:
                pass
        
        if model is not None:
            del model
        
        gc.collect()
        if device == "cuda":
            try:
                import torch
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
            except Exception:
                pass
        
        logger.debug("✅ Embedding model cleanup complete")


class VectorDatabase:
    """
    Unified Vector Database using PostgreSQL and pgvector.
    Replaces ChromaDB and BookVectorDB while maintaining VRAM safety.
    """
    
    def __init__(self, force_cpu: bool = False):
        """Initialize with PostgreSQL connection parameters from Config."""
        self.force_cpu = force_cpu
        self.host = Config.POSTGRES_HOST
        self.port = Config.POSTGRES_PORT
        self.database = Config.POSTGRES_DB
        self.user = Config.POSTGRES_USER
        self.password = Config.POSTGRES_PASSWORD

    @contextmanager
    def _get_cursor(self):
        """Context manager for PostgreSQL database cursor."""
        conn = None
        try:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
            cur = conn.cursor()
            yield cur
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def add_email(self, email_id: str, subject: str, body: str, sender: str, 
                  image_descriptions: Optional[List[str]] = None, 
                  metadata: Optional[Dict] = None) -> bool:
        """Add email embedding to ticket_messages table."""
        try:
            img_text = "\n".join(image_descriptions) if image_descriptions else ""
            text_content = f"Subject: {subject}\n\nBody: {body}\n\nAttachment Descriptions: {img_text}"
            
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None: return False
                embedding = model.encode(text_content).tolist()
            
            with self._get_cursor() as cur:
                cur.execute("""
                    UPDATE ticket_messages 
                    SET embedding = %s 
                    WHERE message_id = %s
                """, (embedding, email_id))
            
            return True
        except Exception as e:
            logger.error(f"Error adding email embedding: {e}")
            return False

    def search_similar_tickets(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search for similar tickets using vector cosine similarity."""
        try:
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None: return []
                query_embedding = model.encode(query).tolist()
            
            results = []
            with self._get_cursor() as cur:
                # Use cosine similarity (<=> is cosine distance in pgvector)
                cur.execute("""
                    SELECT ticket_id, subject, customer_email, embedding <=> %s as distance
                    FROM tickets
                    WHERE embedding IS NOT NULL AND deleted_at IS NULL
                    ORDER BY distance ASC
                    LIMIT %s
                """, (query_embedding, top_k))
                
                for row in cur.fetchall():
                    results.append({
                        'ticket_id': row[0],
                        'subject': row[1],
                        'email': row[2],
                        'distance': row[3]
                    })
            return results
        except Exception as e:
            logger.error(f"Search similar tickets failed: {e}")
            return []

    def query_knowledge_base(self, query_text: str, n_results: int = 5, source_type: str = 'documentation') -> List[Dict]:
        """Query documentation or other knowledge base content."""
        try:
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None: return []
                query_embedding = model.encode(query_text).tolist()
            
            results = []
            with self._get_cursor() as cur:
                cur.execute("""
                    SELECT content, metadata, embedding <=> %s as distance
                    FROM knowledge_base
                    WHERE source_type = %s
                    ORDER BY distance ASC
                    LIMIT %s
                """, (query_embedding, source_type, n_results))
                
                for row in cur.fetchall():
                    results.append({
                        'content': row[0],
                        'metadata': row[1],
                        'distance': row[2]
                    })
            return results
        except Exception as e:
            logger.error(f"Knowledge base query failed: {e}")
            return []

    def add_knowledge_chunk(self, content: str, metadata: Dict, source_id: str, source_type: str = 'documentation') -> bool:
        """Add a document chunk to the knowledge base."""
        try:
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None: return False
                embedding = model.encode(content).tolist()
            
            with self._get_cursor() as cur:
                cur.execute("""
                    INSERT INTO knowledge_base (source_id, content, metadata, embedding, source_type)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding
                """, (source_id, content, Json(metadata), embedding, source_type))
            return True
        except Exception as e:
            logger.error(f"Add knowledge chunk failed: {e}")
            return False

    def cleanup(self):
        """Cleanup resources."""
        gc.collect()

# Compatibility classes for existing code
class BookVectorDB(VectorDatabase):
    def query_bookstack(self, query_text: str, n_results: int = 5, **kwargs) -> Dict:
        """Ported from old BookVectorDB for compatibility."""
        results = self.query_knowledge_base(query_text, n_results, source_type='documentation')
        # Format like ChromaDB output for backward compatibility
        return {
            'documents': [[r['content'] for r in results]],
            'metadatas': [[r['metadata'] for r in results]],
            'distances': [[r['distance'] for r in results]]
        }

    def query_troubleshooting(self, query_text: str, product: Optional[str] = None, n_results: int = 5) -> Dict:
        return self.query_bookstack(query_text, n_results)

    def query_with_visuals(self, query_text: str, n_results: int = 5) -> Dict:
        return self.query_bookstack(query_text, n_results)

    def query_failures(self, query_text: str, product: Optional[str] = None, n_results: int = 5) -> Dict:
        return self.query_bookstack(query_text, n_results)
