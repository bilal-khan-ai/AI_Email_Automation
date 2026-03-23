"""
Vector Database with Soft-Delete Support and VRAM Management

Improvements:
1. Soft-delete mechanism to prevent orphaned vectors
2. Batch cleanup operations for deleted tickets/messages
3. Enhanced logging with structured context
4. Maintains strict VRAM management from original
"""

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from typing import List, Dict, Optional
import logging
import gc
from contextlib import contextmanager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)


@contextmanager
def embedding_model_context(force_cpu: bool = False):
    """
    Context manager for embedding model with guaranteed cleanup.
    
    Usage:
        with embedding_model_context() as (model, device):
            if model is None:
                # Failed to load
                return
            # Use model...
        # Automatic cleanup here
    
    Guarantees:
    - Model is unloaded when context exits
    - VRAM is freed (if on GPU)
    - No dangling references
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
    Vector database with strict VRAM management and soft-delete support.
    
    Processing strategy:
    1. Load embedding model only when needed
    2. Process batch of operations
    3. Unload immediately
    4. Never keep model loaded between operations
    
    New features:
    - Soft-delete mechanism to mark vectors as deleted
    - Batch cleanup to remove deleted vectors
    - Better logging with operation context
    """
    
    def __init__(self, db_path: str, collection_name: str, force_cpu: bool = False):
        """
        Initialize ChromaDB client.
        
        Args:
            db_path: Path to ChromaDB storage
            collection_name: Collection name
            force_cpu: Force CPU for embeddings (no GPU)
        """
        self.db_path = db_path
        self.collection_name = collection_name
        self.force_cpu = force_cpu
        
        # Initialize ChromaDB (lightweight, no GPU)
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False)
        )
        
        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        
        # Create soft-delete collection (stores deleted IDs)
        self.deleted_collection = self.client.get_or_create_collection(
            name=f"{collection_name}_deleted",
            metadata={"description": "Tracks soft-deleted documents"}
        )
        
        logger.info(
            f"✅ Vector database initialized | "
            f"Active count: {self.collection.count()} | "
            f"Deleted count: {self.deleted_collection.count()}"
        )
    
    def add_email(self, email_id: str, subject: str, body: str, sender: str, 
                  image_descriptions: Optional[List[str]] = None, 
                  metadata: Optional[Dict] = None) -> bool:
        """
        Add email to vector database.
        
        VRAM safety:
        - Embedding model is loaded, used, and unloaded within this method
        - No model remains in VRAM after return
        
        Args:
            email_id: Unique email identifier
            subject: Email subject
            body: Email body
            sender: Sender email
            image_descriptions: Optional image descriptions
            metadata: Optional metadata dict
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Check if already soft-deleted
            if self._is_deleted(email_id):
                logger.warning(f"⚠️  Skipping add for deleted email {email_id[:20]}...")
                return False
            
            # Prepare text content
            img_text = "\n".join(image_descriptions) if image_descriptions else ""
            text_content = f"Subject: {subject}\n\nBody: {body}\n\nAttachment Descriptions: {img_text}"
            
            # Load embedding model, generate embedding, unload
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None:
                    logger.error("❌ Embedding model not available")
                    return False
                
                embedding = model.encode(text_content).tolist()
            
            # Prepare metadata
            meta = {
                "subject": subject,
                "sender": sender,
                **(metadata or {})
            }
            
            # Add to ChromaDB
            self.collection.add(
                ids=[email_id],
                embeddings=[embedding],
                documents=[text_content],
                metadatas=[meta]
            )
            
            logger.info(
                f"✅ Added email to vector DB: {email_id[:20]}... | "
                f"Ticket: {metadata.get('ticket_id', 'N/A')}"
            )
            return True
        
        except Exception as e:
            logger.error(f"❌ Error adding email {email_id[:20]}...: {e}")
            return False
    
    def add_emails_batch(self, emails: List[Dict]) -> int:
        """
        Add multiple emails in a single batch (more efficient).
        
        VRAM safety:
        - Embedding model loaded once for entire batch
        - Unloaded immediately after batch processing
        
        Args:
            emails: List of dicts with keys: id, subject, body, sender, image_descriptions, metadata
            
        Returns:
            int: Number of successfully added emails
        """
        if not emails:
            return 0
        
        try:
            # Filter out soft-deleted emails
            deleted_ids = self._get_deleted_ids([email['id'] for email in emails])
            emails = [e for e in emails if e['id'] not in deleted_ids]
            
            if not emails:
                logger.info("ℹ️  All emails in batch were soft-deleted, skipping")
                return 0
            
            # Prepare all text content
            texts = []
            ids = []
            metadatas = []
            
            for email in emails:
                img_text = "\n".join(email.get('image_descriptions', []))
                text_content = (
                    f"Subject: {email['subject']}\n\n"
                    f"Body: {email['body']}\n\n"
                    f"Attachment Descriptions: {img_text}"
                )
                
                texts.append(text_content)
                ids.append(email['id'])
                metadatas.append({
                    "subject": email['subject'],
                    "sender": email['sender'],
                    **(email.get('metadata', {}))
                })
            
            # Load embedding model, generate all embeddings, unload
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None:
                    logger.error("❌ Embedding model not available")
                    return 0
                
                embeddings = model.encode(texts)
                embeddings_list = [emb.tolist() for emb in embeddings]
            
            # Add batch to ChromaDB
            self.collection.add(
                ids=ids,
                embeddings=embeddings_list,
                documents=texts,
                metadatas=metadatas
            )
            
            logger.info(f"✅ Added {len(emails)} emails to vector DB in batch")
            return len(emails)
        
        except Exception as e:
            logger.error(f"❌ Error adding email batch: {e}")
            return 0
    
    def search_similar(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Search for similar documents (excludes soft-deleted).
        
        VRAM safety:
        - Embedding model loaded, used for query encoding, unloaded
        
        Args:
            query: Search query
            top_k: Number of results to return
            
        Returns:
            List of dicts with keys: content, metadata, distance
        """
        try:
            # Load embedding model, encode query, unload
            with embedding_model_context(self.force_cpu) as (model, device):
                if model is None:
                    logger.error("❌ Embedding model not available")
                    return []
                
                query_embedding = model.encode(query).tolist()
            
            # Search in ChromaDB (fetch extra to account for potential deleted docs)
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k * 2  # Fetch 2x to handle deleted docs
            )
            
            # Filter out soft-deleted documents
            formatted_results = []
            if results['documents'] and results['documents'][0]:
                result_ids = results['ids'][0]
                deleted_ids = self._get_deleted_ids(result_ids)
                
                for i, doc in enumerate(results['documents'][0]):
                    doc_id = result_ids[i]
                    
                    # Skip if soft-deleted
                    if doc_id in deleted_ids:
                        continue
                    
                    formatted_results.append({
                        'id': doc_id,
                        'content': doc,
                        'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                        'distance': results['distances'][0][i] if results['distances'] else 0
                    })
                    
                    # Stop once we have enough results
                    if len(formatted_results) >= top_k:
                        break
            
            logger.info(
                f"🔍 Search returned {len(formatted_results)} results "
                f"(filtered from {len(results.get('ids', [[]])[0])} total)"
            )
            return formatted_results
        
        except Exception as e:
            logger.error(f"❌ Error searching: {e}")
            return []
    
    def soft_delete(self, doc_ids: List[str]) -> int:
        """
        Soft-delete documents (mark as deleted without removing).
        
        This allows safe cleanup later while preventing immediate data loss.
        
        Args:
            doc_ids: List of document IDs to soft-delete
            
        Returns:
            int: Number of documents marked as deleted
        """
        if not doc_ids:
            return 0
        
        try:
            # Check which IDs actually exist
            existing_ids = []
            for doc_id in doc_ids:
                try:
                    result = self.collection.get(ids=[doc_id])
                    if result and result['ids']:
                        existing_ids.append(doc_id)
                except Exception:
                    pass
            
            if not existing_ids:
                logger.info("ℹ️  No documents found to soft-delete")
                return 0
            
            # Add to deleted collection
            self.deleted_collection.upsert(
                ids=existing_ids,
                documents=[f"deleted:{doc_id}" for doc_id in existing_ids],
                metadatas=[{"deleted": True} for _ in existing_ids]
            )
            
            logger.info(f"🗑️  Soft-deleted {len(existing_ids)} documents from vector DB")
            return len(existing_ids)
        
        except Exception as e:
            logger.error(f"❌ Error soft-deleting documents: {e}")
            return 0
    
    def hard_delete(self, doc_ids: List[str]) -> int:
        """
        Hard-delete documents (permanently remove from vector DB).
        
        WARNING: This cannot be undone. Use soft_delete for safety.
        
        Args:
            doc_ids: List of document IDs to hard-delete
            
        Returns:
            int: Number of documents permanently deleted
        """
        if not doc_ids:
            return 0
        
        try:
            # Delete from main collection
            self.collection.delete(ids=doc_ids)
            
            # Also remove from deleted collection if present
            try:
                self.deleted_collection.delete(ids=doc_ids)
            except Exception:
                pass
            
            logger.info(f"🗑️  Hard-deleted {len(doc_ids)} documents from vector DB (permanent)")
            return len(doc_ids)
        
        except Exception as e:
            logger.error(f"❌ Error hard-deleting documents: {e}")
            return 0
    
    def cleanup_deleted(self, batch_size: int = 100) -> int:
        """
        Permanently remove soft-deleted documents from vector DB.
        
        This should be run periodically (e.g., daily) to clean up disk space.
        
        Args:
            batch_size: Number of documents to delete per batch
            
        Returns:
            int: Total number of documents permanently deleted
        """
        try:
            # Get all soft-deleted IDs
            deleted_docs = self.deleted_collection.get()
            
            if not deleted_docs or not deleted_docs['ids']:
                logger.info("ℹ️  No soft-deleted documents to clean up")
                return 0
            
            deleted_ids = deleted_docs['ids']
            total_deleted = 0
            
            # Delete in batches
            for i in range(0, len(deleted_ids), batch_size):
                batch = deleted_ids[i:i + batch_size]
                
                try:
                    self.collection.delete(ids=batch)
                    total_deleted += len(batch)
                    
                    logger.info(f"🧹 Cleaned up batch {i // batch_size + 1}: {len(batch)} documents")
                except Exception as e:
                    logger.error(f"❌ Error cleaning up batch: {e}")
            
            # Clear the deleted collection
            if total_deleted > 0:
                try:
                    self.deleted_collection.delete(ids=deleted_ids)
                    logger.info(f"✅ Cleanup complete: removed {total_deleted} soft-deleted documents")
                except Exception as e:
                    logger.error(f"❌ Error clearing deleted collection: {e}")
            
            return total_deleted
        
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")
            return 0
    
    def _is_deleted(self, doc_id: str) -> bool:
        """Check if a document is soft-deleted"""
        try:
            result = self.deleted_collection.get(ids=[doc_id])
            return bool(result and result['ids'])
        except Exception:
            return False
    
    def _get_deleted_ids(self, doc_ids: List[str]) -> set:
        """Get set of IDs that are soft-deleted from a list"""
        try:
            result = self.deleted_collection.get(ids=doc_ids)
            if result and result['ids']:
                return set(result['ids'])
            return set()
        except Exception:
            return set()
    
    def get_count(self) -> int:
        """Get total number of documents in collection (includes soft-deleted)"""
        return self.collection.count()
    
    def get_active_count(self) -> int:
        """Get number of active (non-deleted) documents"""
        total = self.collection.count()
        deleted = self.deleted_collection.count()
        return total - deleted
    
    def get_deleted_count(self) -> int:
        """Get number of soft-deleted documents"""
        return self.deleted_collection.count()
    
    def cleanup(self):
        """
        Force cleanup of all resources.
        
        Call this during shutdown.
        """
        logger.info("🧹 VectorDatabase cleanup")
        gc.collect()

class BookVectorDB:
    def __init__(self, persist_directory: str = "./chromaDB"):
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
        
        self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
    
    def get_bookstack_collection(self):
        """Get or create the bookstack_db collection."""
        return self.client.get_or_create_collection(
            name="bookstack_db",
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"}
        )
    
    def query_bookstack(
        self,
        query_text: str,
        n_results: int = 5,
        product: Optional[str] = None,
        intent: Optional[str] = None,
        has_visual: Optional[bool] = None,
        failure_related: Optional[bool] = None
    ) -> Dict:
        """
        Query the Bookstack documentation database.
        
        Args:
            query_text: Search query
            n_results: Number of results to return
            product: Filter by product name
            intent: Filter by intent (how_to, troubleshooting, configuration)
            has_visual: Filter for chunks with images
            failure_related: Filter for failure-related content
            
        Returns:
            Query results with documents and metadata
        """
        collection = self.get_bookstack_collection()
        
        where = {}
        if product:
            where["product"] = product
        if intent:
            where["intent"] = intent
        if has_visual is not None:
            where["has_visual"] = str(has_visual)
        if failure_related is not None:
            where["failure_related"] = str(failure_related)
        
        results = collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where if where else None
        )
        
        return results
    
    def add_to_bookstack(
        self,
        documents: List[str],
        metadatas: List[Dict],
        ids: Optional[List[str]] = None
    ) -> None:
        """
        Add documents to bookstack_db collection.
        
        Args:
            documents: List of document texts
            metadatas: List of metadata dictionaries
            ids: Optional list of IDs (auto-generated if not provided)
        """
        collection = self.get_bookstack_collection()
        
        if ids is None:
            start_idx = collection.count()
            ids = [f"chunk_{i}" for i in range(start_idx, start_idx + len(documents))]
        
        collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
    
    def delete_from_bookstack(
        self,
        ids: Optional[List[str]] = None,
        where: Optional[Dict] = None
    ) -> None:
        """
        Delete documents from bookstack_db collection.
        
        Args:
            ids: List of specific document IDs to delete
            where: Metadata filter for deletion (e.g., {"product": "Gateway Pro"})
        """
        collection = self.get_bookstack_collection()
        
        if ids:
            collection.delete(ids=ids)
        elif where:
            collection.delete(where=where)
        else:
            raise ValueError("Must provide either ids or where filter")
    
    def get_bookstack_stats(self) -> Dict:
        """Get statistics about the Bookstack database."""
        collection = self.get_bookstack_collection()
        return {
            "total_chunks": collection.count(),
            "name": collection.name,
            "metadata": collection.metadata
        }
    
    def reset_bookstack_db(self) -> None:
        """Delete and recreate the bookstack_db collection."""
        try:
            self.client.delete_collection(name="bookstack_db")
        except:
            pass
        
        self.get_bookstack_collection()
    
    def query_troubleshooting(
        self,
        query_text: str,
        product: Optional[str] = None,
        n_results: int = 5
    ) -> Dict:
        """Query troubleshooting content specifically."""
        where = {"intent": "troubleshooting"}
        if product:
            where["product"] = product
        
        collection = self.get_bookstack_collection()
        return collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where
        )
    
    def query_with_visuals(
        self,
        query_text: str,
        n_results: int = 5
    ) -> Dict:
        """Query for content that includes UI screenshots and VLM descriptions."""
        collection = self.get_bookstack_collection()
        return collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where={"has_visual": "True"}
        )
    
    def query_failures(
        self,
        query_text: str,
        product: Optional[str] = None,
        n_results: int = 5
    ) -> Dict:
        """Query for failure-related content."""
        where = {"failure_related": "True"}
        if product:
            where["product"] = product
        
        collection = self.get_bookstack_collection()
        return collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where
        )
