"""
Vector Database utilizing lightweight OpenAI API-based text-embedding-3-small.
Permanently eliminates all local CPU/GPU/VRAM memory overhead.
Soft-Delete Support and unified path structure.
"""

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from typing import List, Dict, Optional
import logging
import gc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s'
)
logger = logging.getLogger(__name__)


class VectorDatabase:
    """
    Vector database utilizing lightweight OpenAI API-based text-embedding-3-small.
    Eliminates all local CPU/GPU/VRAM memory overhead.
    """
    def __init__(self, db_path: str, collection_name: str, force_cpu: bool = True):
        self.db_path = db_path
        self.collection_name = collection_name
        
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
        try:
            result = self.deleted_collection.get(ids=[doc_id])
            return bool(result and result['ids'])
        except Exception: return False

    def _get_deleted_ids(self, doc_ids: List[str]) -> set:
        try:
            result = self.deleted_collection.get(ids=doc_ids)
            if result and result['ids']: return set(result['ids'])
            return set()
        except Exception: return set()

    def get_count(self) -> int: return self.collection.count()
    def get_active_count(self) -> int: return self.collection.count() - self.deleted_collection.count()
    def get_deleted_count(self) -> int: return self.deleted_collection.count()
    
    def update_ticket_status(self, ticket_id: str, is_resolved: bool, display_id: str = None) -> int:
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

    def cleanup(self): gc.collect()


class BookVectorDB:
    """
    Documentation database wrapper utilizing OpenAI embedding functions.
    Replaces original local HuggingFace query/add procedures.
    """
    def __init__(self, persist_directory: Optional[str] = None):
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
        collection = self.get_bookstack_collection()
        if ids is None:
            start_idx = collection.count()
            ids = [f"chunk_{i}" for i in range(start_idx, start_idx + len(documents))]
        
        collection.add(ids=ids, documents=documents, metadatas=metadatas)

    def search_similar(self, query: str, top_k: int = 5) -> List[Dict]:
        """Aligns interface with search_similar used in main.py"""
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
        collection = self.get_bookstack_collection()
        if ids: collection.delete(ids=ids)
        elif where: collection.delete(where=where)
        else: raise ValueError("Provide ids or where filter")

    def get_bookstack_stats(self) -> Dict:
        collection = self.get_bookstack_collection()
        return {"total_chunks": collection.count(), "name": collection.name}

    def reset_bookstack_db(self) -> None:
        try: self.client.delete_collection(name="bookstack_db")
        except Exception: pass
        self.get_bookstack_collection()

    def query_troubleshooting(
        self,
        query_text: str,
        product: Optional[str] = None,
        n_results: int = 5
    ) -> Dict:
        """Query troubleshooting content specifically."""
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
        collection = self.get_bookstack_collection()
        where = {"failure_related": "True"}
        if product:
            where["product"] = product
        return collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where
        )
