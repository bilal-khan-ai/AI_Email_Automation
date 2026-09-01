"""
Vector Database utilizing lightweight OpenAI API-based text-embedding-3-small.
Permanently eliminates all local CPU/GPU/VRAM memory overhead.
Soft-Delete Support and unified path structure.
"""

# Bilal Khan (10/08/2026) Issue No  Sheet_Name  - Make chromadb optional to support model unload mode
try:
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions
    HAS_CHROMADB = True
except ImportError:
    chromadb = None
    Settings = None
    embedding_functions = None
    HAS_CHROMADB = False
from typing import List, Dict, Optional, Tuple, Any, Set
import logging
import gc

# Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Remove module-level basicConfig in library module
logger = logging.getLogger(__name__)


class VectorDatabase:
    """
    Vector database manager for support email similarity retrieval.
    
    Uses OpenAI's text-embedding-3-small API for lightweight remote embedding generation,
    completely bypassing heavy local transformer dependencies and VRAM usage.
    Supports soft-deletion and metadata filtering for knowledge base freshness.
    """
    def __init__(self, db_path: str, collection_name: str, force_cpu: bool = True):
        """
        Initialize persistent ChromaDB client and collections with OpenAI embeddings.
        
        Args:
            db_path: Directory path for persistent storage of Chroma vector data.
            collection_name: Name of the primary active documents collection.
            force_cpu: Retained for backward compatibility (embedding generation runs via API).
        """
        self.db_path = db_path
        self.collection_name = collection_name
        
        # Bilal Khan (10/08/2026) Issue No  Sheet_Name  - Guard VectorDatabase initialization when chromadb is not installed
        if not HAS_CHROMADB:
            logger.warning("⚠️ chromadb is not installed. VectorDatabase functionality is disabled.")
            self.client = None
            self.collection = None
            return
        
        # Load API key dynamically from Config to avoid import cycles
        from config import Config
        self.openai_ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=Config.OPENAI_API_KEY,
            model_name="text-embedding-3-small"
        )
        
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
        
        # Initialize collection with OpenAI Embedder
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=self.openai_ef
        )
        
        self.deleted_collection = self.client.get_or_create_collection(
            name=f"{collection_name}_deleted",
            metadata={"description": "Tracks soft-deleted documents"}
        )
        
        logger.info(
            f"✅ OpenAI Vector DB initialized | "
            f"Active count: {self.collection.count()} | "
            f"Deleted count: {self.deleted_collection.count()}"
        )
    
    def add_email(self, email_id: str, subject: str, body: str, sender: str, 
                  image_descriptions: Optional[List[str]] = None, 
                  metadata: Optional[Dict] = None,
                  display_id: str = None) -> bool:
        """
        Add a single email document and its extracted metadata/vision descriptions to the vector index.
        
        Args:
            email_id: Unique identifier for the email / message.
            subject: Email subject line.
            body: Text content of the email.
            sender: Sender email address.
            image_descriptions: OCR or vision-model descriptions of attached images.
            metadata: Additional key-value metadata to store alongside embeddings.
            display_id: Human-readable ticket ID for structured logging.
            
        Returns:
            True if document was successfully indexed, False otherwise.
        """
        try:
            if self._is_deleted(email_id):
                logger.warning(f"⚠️ Skipping add for already deleted email (ID: {email_id[0:20]})")
                return False
            
            img_text = "\n".join(image_descriptions) if image_descriptions else ""
            text_content = f"Subject: {subject}\n\nBody: {body}\n\nAttachment Descriptions: {img_text}"
            
            meta = {
                "subject": subject,
                "sender": sender,
                "is_resolved": metadata.get('is_resolved', False) if metadata else False,
                "is_authority": metadata.get('is_authority', False) if metadata else False,
                **(metadata or {})
            }
            meta = {k: v for k, v in meta.items() if v is not None}
            
            # Embeddings generated automatically by registered OpenAI ef
            self.collection.add(
                ids=[email_id],
                documents=[text_content],
                metadatas=[meta]
            )
            
            log_ticket = display_id or meta.get('ticket_id', 'N/A')
            logger.info(f"✅ Added email to vector DB: {email_id[0:20]} | Ticket: {log_ticket}")
            return True
        except Exception as e:
            logger.error(f"❌ Error adding email (ID: {email_id[0:20]}): {e}")
            return False
            
    def add_emails_batch(self, emails: List[Dict]) -> int:
        """
        Bulk ingest a batch of emails to minimize round-trip vector indexing overhead.
        
        Args:
            emails: List of email dictionaries containing 'id', 'subject', 'body', 'sender',
                    and optional 'image_descriptions' and 'metadata'.
                    
        Returns:
            Count of emails successfully added.
        """
        if not emails: return 0
        try:
            deleted_ids = self._get_deleted_ids([email['id'] for email in emails])
            emails = [e for e in emails if e['id'] not in deleted_ids]
            if not emails: return 0
            
            texts, ids, metadatas = [], [], []
            for email in emails:
                img_text = "\n".join(email.get('image_descriptions', []))
                text_content = f"Subject: {email['subject']}\n\nBody: {email['body']}\n\nAttachment Descriptions: {img_text}"
                
                texts.append(text_content)
                ids.append(email['id'])
                metadatas.append({
                    "subject": email['subject'],
                    "sender": email['sender'],
                    "is_resolved": email.get('metadata', {}).get('is_resolved', False),
                    "is_authority": email.get('metadata', {}).get('is_authority', False),
                    **(email.get('metadata', {}))
                })
            
            metadatas = [{k: v for k, v in m.items() if v is not None} for m in metadatas]
            
            self.collection.add(ids=ids, documents=texts, metadatas=metadatas)
            logger.info(f"✅ Added {len(emails)} emails to vector DB in batch")
            return len(emails)
        except Exception as e:
            logger.error(f"❌ Error adding email batch: {e}")
            return 0
            
    def search_similar(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Perform cosine similarity search against active indexed emails.
        
        Ranks results prioritizing authoritative responses and resolved historical tickets.
        
        Args:
            query: Question or email body text to find similar documents for.
            top_k: Maximum number of relevant matches to return.
            
        Returns:
            List of candidate dictionaries with 'id', 'content', 'metadata', and 'distance'.
        """
        try:
            # Automatic encoding by OpenAIef on query_texts
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k * 4
            )
            
            candidates = []
            if results['documents'] and results['documents'][0]:
                result_ids = results['ids'][0]
                deleted_ids = self._get_deleted_ids(result_ids)
                
                for i, doc in enumerate(results['documents'][0]):
                    doc_id = result_ids[i]
                    if doc_id in deleted_ids: continue
                    
                    candidates.append({
                        'id': doc_id,
                        'content': doc,
                        'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                        'distance': results['distances'][0][i] if results['distances'] else 0
                    })
            
            candidates.sort(key=lambda x: (
                x['metadata'].get('is_authority', False),
                x['metadata'].get('is_resolved', False),
                -x['distance']
            ), reverse=True)
            
            return candidates[:top_k]
        except Exception as e:
            logger.error(f"❌ Error searching: {e}")
            return []

    def soft_delete(self, doc_ids: List[str]) -> int:
        """
        Mark documents as soft-deleted without purging index structures immediately.
        
        Prevents deleted emails from surfacing in RAG searches while preserving audit history.
        
        Args:
            doc_ids: List of document IDs to soft-delete.
            
        Returns:
            Count of documents successfully soft-deleted.
        """
        if not doc_ids: return 0
        try:
            existing_ids = []
            for doc_id in doc_ids:
                try:
                    result = self.collection.get(ids=[doc_id])
                    if result and result['ids']: existing_ids.append(doc_id)
                except Exception: pass
            
            if not existing_ids: return 0
            self.deleted_collection.upsert(
                ids=existing_ids,
                documents=[f"deleted:{doc_id}" for doc_id in existing_ids],
                metadatas=[{"deleted": True} for _ in existing_ids]
            )
            logger.info(f"🗑️ Soft-deleted {len(existing_ids)} documents")
            return len(existing_ids)
        except Exception as e:
            logger.error(f"❌ Error soft-deleting: {e}")
            return 0

    def hard_delete(self, doc_ids: List[str]) -> int:
        """
        Permanently delete documents from both active and deleted collections.
        
        Args:
            doc_ids: List of document IDs to permanently erase.
            
        Returns:
            Count of document IDs passed for deletion.
        """
        if not doc_ids: return 0
        try:
            self.collection.delete(ids=doc_ids)
            try: self.deleted_collection.delete(ids=doc_ids)
            except Exception: pass
            return len(doc_ids)
        except Exception as e:
            logger.error(f"❌ Error hard-deleting: {e}")
            return 0

    def cleanup_deleted(self, batch_size: int = 100) -> int:
        """
        Purge all soft-deleted documents from the main collection in batches.
        
        Args:
            batch_size: Number of records to remove per chunk.
            
        Returns:
            Total number of purged documents.
        """
        try:
            deleted_docs = self.deleted_collection.get()
            if not deleted_docs or not deleted_docs['ids']: return 0
            deleted_ids = deleted_docs['ids']
            total_deleted = 0
            for i in range(0, len(deleted_ids), batch_size):
                batch = deleted_ids[i:i + batch_size]
                self.collection.delete(ids=batch)
                total_deleted += len(batch)
            try: self.deleted_collection.delete(ids=deleted_ids)
            except Exception: pass
            return total_deleted
        except Exception as e:
            logger.error(f"❌ Error cleaning up: {e}")
            return 0

    def _is_deleted(self, doc_id: str) -> bool:
        """
        Check whether a given document ID exists in the soft-deleted collection.
        
        Args:
            doc_id: Document ID to verify.
            
        Returns:
            True if the document is marked deleted, False otherwise.
        """
        try:
            result = self.deleted_collection.get(ids=[doc_id])
            return bool(result and result['ids'])
        except Exception: return False

    def _get_deleted_ids(self, doc_ids: List[str]) -> Set[str]:
        """
        Filter a list of IDs to find which ones are currently soft-deleted.
        
        Args:
            doc_ids: List of document IDs to check.
            
        Returns:
            Set of IDs that are marked as soft-deleted.
        """
        try:
            result = self.deleted_collection.get(ids=doc_ids)
            if result and result['ids']: return set(result['ids'])
            return set()
        except Exception: return set()

    def get_count(self) -> int:
        """Return total document count in the primary collection."""
        return self.collection.count()
        
    def get_active_count(self) -> int:
        """Return count of active (non-deleted) documents."""
        return self.collection.count() - self.deleted_collection.count()
        
    def get_deleted_count(self) -> int:
        """Return count of soft-deleted documents."""
        return self.deleted_collection.count()
    
    def update_ticket_status(self, ticket_id: str, is_resolved: bool, display_id: str = None) -> int:
        """
        Update resolution status metadata for all vector chunks linked to a ticket.
        
        Args:
            ticket_id: UUID of the ticket.
            is_resolved: Boolean flag indicating if the issue is solved.
            display_id: Optional human-readable ticket ID for logging.
            
        Returns:
            Count of updated document chunks.
        """
        try:
            results = self.collection.get(where={"ticket_id": ticket_id})
            if not results or not results['ids']: return 0
            ids = results['ids']
            metadatas = results['metadatas']
            for meta in metadatas: meta['is_resolved'] = is_resolved
            self.collection.update(ids=ids, metadatas=metadatas)
            return len(ids)
        except Exception as e:
            logger.error(f"❌ Error updating status: {e}")
            return 0

    def update_authority_status(self, ticket_id: str, is_authority: bool, display_id: str = None) -> int:
        """
        Update authoritative knowledge flag for vector chunks linked to a ticket.
        
        Args:
            ticket_id: UUID of the ticket.
            is_authority: Boolean flag indicating if response was authored/verified by senior staff.
            display_id: Optional human-readable ticket ID for logging.
            
        Returns:
            Count of updated document chunks.
        """
        try:
            results = self.collection.get(where={"ticket_id": ticket_id})
            if not results or not results['ids']: return 0
            ids = results['ids']
            metadatas = results['metadatas']
            for meta in metadatas: meta['is_authority'] = is_authority
            self.collection.update(ids=ids, metadatas=metadatas)
            return len(ids)
        except Exception as e:
            logger.error(f"❌ Error updating authority: {e}")
            return 0

    def cleanup(self):
        """Invoke garbage collector to reclaim unused object buffers."""
        gc.collect()


class BookVectorDB:
    """
    Documentation and Knowledge Base vector database manager.
    
    Manages chunked BookStack articles and product documentation embedded via OpenAI.
    Provides targeted domain queries for troubleshooting, visual UI walkthroughs,
    and failure diagnostics.
    """
    def __init__(self, persist_directory: Optional[str] = None):
        """
        Initialize persistent ChromaDB client for BookStack knowledge base.
        
        Args:
            persist_directory: Storage path for BookStack Chroma database.
        """
        from config import Config
        path = persist_directory or Config.BOOKSTACK_DB_PATH
        
        self.openai_ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=Config.OPENAI_API_KEY,
            model_name="text-embedding-3-small"
        )
        self.client = chromadb.PersistentClient(
            path=path,
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
        logger.info(f"📚 BookVectorDB: Initialized under OpenAI space at {path}")

    def get_bookstack_collection(self):
        """Retrieve or create the bookstack_db collection with cosine distance."""
        return self.client.get_or_create_collection(
            name="bookstack_db",
            metadata={"hnsw:space": "cosine"},
            embedding_function=self.openai_ef
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
        Query documentation chunks with optional structured metadata filters.
        
        Args:
            query_text: Search query string.
            n_results: Number of top chunks to retrieve.
            product: Filter by product name.
            intent: Filter by intent category (e.g., 'troubleshooting').
            has_visual: Filter for chunks containing screenshot/vision descriptions.
            failure_related: Filter for chunks addressing specific failure modes.
            
        Returns:
            Chroma query result dictionary.
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
            
        return collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where if where else None
        )

    def add_to_bookstack(self, documents: List[str], metadatas: List[Dict], ids: Optional[List[str]] = None) -> None:
        """
        Insert document chunks and metadata into the BookStack collection.
        
        Args:
            documents: List of text content chunks.
            metadatas: List of associated metadata dictionaries.
            ids: Optional custom IDs (auto-generated if omitted).
        """
        collection = self.get_bookstack_collection()
        if ids is None:
            start_idx = collection.count()
            ids = [f"chunk_{i}" for i in range(start_idx, start_idx + len(documents))]
        
        collection.add(ids=ids, documents=documents, metadatas=metadatas)

    def search_similar(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Standardized retrieval interface aligning BookVectorDB with VectorDatabase.
        
        Args:
            query: Search query text.
            top_k: Number of results to return.
            
        Returns:
            List of dictionaries with 'id', 'content', 'metadata', and 'distance'.
        """
        raw = self.query_bookstack(query, n_results=top_k)
        formatted = []
        if raw and 'documents' in raw and raw['documents']:
            for i, doc in enumerate(raw['documents'][0]):
                formatted.append({
                    'id': raw['ids'][0][i],
                    'content': doc,
                    'metadata': raw['metadatas'][0][i] if raw['metadatas'] else {},
                    'distance': raw['distances'][0][i] if raw['distances'] else 0.5
                })
        return formatted

    def delete_from_bookstack(self, ids: Optional[List[str]] = None, where: Optional[Dict] = None) -> None:
        """
        Delete document chunks from BookStack by IDs or metadata filter.
        
        Args:
            ids: Optional list of chunk IDs to delete.
            where: Optional metadata filter dict.
        """
        collection = self.get_bookstack_collection()
        if ids: collection.delete(ids=ids)
        elif where: collection.delete(where=where)
        else: raise ValueError("Provide ids or where filter")

    def get_bookstack_stats(self) -> Dict:
        """Return total chunks and collection name for BookStack."""
        collection = self.get_bookstack_collection()
        return {"total_chunks": collection.count(), "name": collection.name}

    def reset_bookstack_db(self) -> None:
        """Purge and reinitialize the BookStack vector database collection."""
        try: self.client.delete_collection(name="bookstack_db")
        except Exception: pass
        self.get_bookstack_collection()

    def query_troubleshooting(
        self,
        query_text: str,
        product: Optional[str] = None,
        n_results: int = 5
    ) -> Dict:
        """
        Query specifically for troubleshooting and fix-step documentation chunks.
        
        Args:
            query_text: Troubleshooting query text.
            product: Optional product identifier.
            n_results: Max chunks to return.
        """
        collection = self.get_bookstack_collection()
        where = {"intent": "troubleshooting"}
        if product:
            where["product"] = product
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
        """
        Query for documentation chunks containing UI screenshots and visual descriptions.
        
        Args:
            query_text: Search query.
            n_results: Max chunks to return.
        """
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
        """
        Query specifically for known failure modes and system error resolutions.
        
        Args:
            query_text: Failure or error text.
            product: Optional product identifier.
            n_results: Max chunks to return.
        """
        collection = self.get_bookstack_collection()
        where = {"failure_related": "True"}
        if product:
            where["product"] = product
        return collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where
        )
