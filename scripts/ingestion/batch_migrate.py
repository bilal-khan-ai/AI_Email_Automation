import os
import json
import sqlite3
import logging
import chromadb
from pathlib import Path
from openai import OpenAI
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("BatchMigrate")

OLD_EMAIL_PATH = "./chroma_db"
UNIFIED_DB_PATH = "./data/chroma_db"
CACHE_DB_PATH = "./data/migration_cache.db"
BATCH_DIR = "./data/batches"

class BatchMigrator:
    def __init__(self):
        self.openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
        os.makedirs(BATCH_DIR, exist_ok=True)
        self.init_cache_db()

    def init_cache_db(self):
        """Initializes a local SQLite database to resiliently track migration state."""
        self.conn = sqlite3.connect(CACHE_DB_PATH)
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS emails_cache (
                id TEXT PRIMARY KEY,
                document TEXT,
                metadata TEXT,
                batch_file_index INTEGER,
                batch_job_id TEXT,
                status TEXT DEFAULT 'pending' -- pending, queued, completed, failed
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS batch_jobs (
                job_id TEXT PRIMARY KEY,
                batch_file_index INTEGER,
                openai_file_id TEXT,
                status TEXT, -- in_progress, completed, failed, cancelled
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def get_cache_counts(self):
        cur = self.conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM emails_cache GROUP BY status")
        rows = cur.fetchall()
        counts = {row[0]: row[1] for row in rows}
        cur.execute("SELECT COUNT(*) FROM emails_cache")
        total = cur.fetchone()[0]
        return total, counts

    def populate_cache_from_old_chroma(self):
        """Pages through the 48,805 old emails and populates the SQLite cache."""
        total, _ = self.get_cache_counts()
        if total > 0:
            logger.info(f"📂 Cache already populated with {total} emails. Skipping database read.")
            return

        logger.info(f"🔌 Connecting to old ChromaDB at {OLD_EMAIL_PATH}...")
        try:
            old_client = chromadb.PersistentClient(path=OLD_EMAIL_PATH)
            old_collection = old_client.get_collection("new_support_emails")
            total_elements = old_collection.count()
            logger.info(f"📊 Found {total_elements} documents in old collection 'new_support_emails'")
        except Exception as e:
            logger.error(f"❌ Failed to connect to old ChromaDB: {e}")
            return

        PAGE_SIZE = 5000
        offset = 0
        inserted_count = 0

        logger.info("⏳ Scanning old documents and loading them into SQLite...")
        while offset < total_elements:
            try:
                data = old_collection.get(limit=PAGE_SIZE, offset=offset)
                ids = data.get('ids', [])
                documents = data.get('documents', [])
                metadatas = data.get('metadatas', [])

                if not ids:
                    break

                # Bulk insert into SQLite
                cur = self.conn.cursor()
                rows_to_insert = []
                for idx, doc_id in enumerate(ids):
                    rows_to_insert.append((
                        doc_id,
                        documents[idx],
                        json.dumps(metadatas[idx] if metadatas else {})
                    ))

                cur.executemany("""
                    INSERT OR IGNORE INTO emails_cache (id, document, metadata)
                    VALUES (?, ?, ?)
                """, rows_to_insert)
                self.conn.commit()

                inserted_count += len(ids)
                offset += PAGE_SIZE
                logger.info(f"💾 Loaded {inserted_count}/{total_elements} documents into local cache...")
            except Exception as e:
                logger.error(f"❌ Error paging old database at offset {offset}: {e}")
                break

        total, _ = self.get_cache_counts()
        logger.info(f"🎉 SQLite Cache built successfully. Total records stored: {total}")

    def generate_batch_files(self):
        """Creates partition JSONL files for OpenAI Batch API (max 25,000 items each)."""
        cur = self.conn.cursor()
        cur.execute("SELECT id, document FROM emails_cache WHERE status = 'pending'")
        rows = cur.fetchall()

        if not rows:
            logger.info("ℹ️ No pending emails left to partition.")
            return

        logger.info(f"⚙️ Formatting {len(rows)} pending emails into batch request files...")
        BATCH_LIMIT = 24000
        current_batch_index = 1
        current_batch_rows = []

        def flush_batch(index, items):
            file_path = os.path.join(BATCH_DIR, f"batch_{index}.jsonl")
            logger.info(f"✏️ Writing {len(items)} requests to {file_path}...")
            
            with open(file_path, "w", encoding="utf-8") as f:
                for row_id, doc in items:
                    # Truncate doc to ~12,000 characters to prevent OpenAI maximum context length of 8192 tokens error
                    truncated_doc = doc[:12000] if doc else ""
                    req = {
                        "custom_id": row_id,
                        "method": "POST",
                        "url": "/v1/embeddings",
                        "body": {
                            "model": "text-embedding-3-small",
                            "input": truncated_doc
                        }
                    }
                    f.write(json.dumps(req) + "\n")
            
            # Update SQLite status and batch index mapping
            cur2 = self.conn.cursor()
            ids = [x[0] for x in items]
            cur2.execute(f"""
                UPDATE emails_cache 
                SET batch_file_index = ? 
                WHERE id IN ({','.join(['?']*len(ids))})
            """, [index] + ids)
            self.conn.commit()
            logger.info(f"✅ Batch {index} file written.")

        for row in rows:
            current_batch_rows.append((row['id'], row['document']))
            if len(current_batch_rows) >= BATCH_LIMIT:
                flush_batch(current_batch_index, current_batch_rows)
                current_batch_index += 1
                current_batch_rows = []

        if current_batch_rows:
            flush_batch(current_batch_index, current_batch_rows)

    def submit_batches(self):
        """Uploads the files and submits jobs to OpenAI."""
        # Find all written batch files
        batch_files = sorted(Path(BATCH_DIR).glob("batch_*.jsonl"))
        if not batch_files:
            logger.warning("⚠️ No batch files found to submit! Run step 1 & 2 first.")
            return

        cur = self.conn.cursor()
        for bf in batch_files:
            batch_index = int(bf.stem.split("_")[1])
            
            # Check if this batch is already submitted or completed
            cur.execute("SELECT job_id FROM batch_jobs WHERE batch_file_index = ?", (batch_index,))
            existing = cur.fetchone()
            if existing:
                logger.info(f"⏭️ Batch {batch_index} already submitted as Job: {existing['job_id']}. Skipping.")
                continue

            logger.info(f"📤 Uploading {bf.name} to OpenAI Files API...")
            try:
                with open(bf, "rb") as file_data:
                    uploaded = self.openai_client.files.create(
                        file=file_data,
                        purpose="batch"
                    )
                logger.info(f"✅ Uploaded to OpenAI. File ID: {uploaded.id}")

                logger.info(f"🚀 Submitting Batch Job for {bf.name}...")
                job = self.openai_client.batches.create(
                    input_file_id=uploaded.id,
                    endpoint="/v1/embeddings",
                    completion_window="24h"
                )
                logger.info(f"🔥 Submitted successfully! Job ID: {job.id}")

                # Save job record
                cur.execute("""
                    INSERT INTO batch_jobs (job_id, batch_file_index, openai_file_id, status)
                    VALUES (?, ?, ?, ?)
                """, (job.id, batch_index, uploaded.id, job.status))
                
                # Update email statuses in cache
                cur.execute("""
                    UPDATE emails_cache 
                    SET batch_job_id = ?, status = 'queued'
                    WHERE batch_file_index = ? AND status = 'pending'
                """, (job.id, batch_index))
                self.conn.commit()

            except Exception as e:
                logger.error(f"❌ Failed to submit batch {batch_index}: {e}")

    def check_jobs_status(self):
        """Queries OpenAI to update job status."""
        cur = self.conn.cursor()
        cur.execute("SELECT job_id, batch_file_index, status FROM batch_jobs WHERE status NOT IN ('completed', 'failed', 'cancelled')")
        active_jobs = cur.fetchall()

        if not active_jobs:
            logger.info("ℹ️ No active/running OpenAI batch jobs to monitor.")
            return

        logger.info(f"🔍 Monitoring {len(active_jobs)} active Batch jobs...")
        for row in active_jobs:
            job_id = row['job_id']
            idx = row['batch_file_index']
            try:
                job_status = self.openai_client.batches.retrieve(job_id)
                logger.info(f"  Job {job_id} (Batch {idx}) status: [bold]{job_status.status}[/bold]")
                
                if job_status.status != row['status']:
                    cur.execute("UPDATE batch_jobs SET status = ? WHERE job_id = ?", (job_status.status, job_id))
                    self.conn.commit()
                    
                    if job_status.status == "completed":
                        logger.info(f"🎉 Job {job_id} has COMPLETED! Ready for extraction.")
                    elif job_status.status in ["failed", "expired", "cancelled"]:
                        logger.error(f"❌ Job {job_id} terminated with status: {job_status.status}")
                        cur.execute("UPDATE emails_cache SET status = 'pending' WHERE batch_job_id = ?", (job_id,))
                        self.conn.commit()
            except Exception as e:
                logger.error(f"❌ Error checking job {job_id}: {e}")

    def ingest_completed_batches(self):
        """Downloads embedding results from completed jobs and writes them to ChromaDB."""
        cur = self.conn.cursor()
        cur.execute("SELECT job_id, batch_file_index FROM batch_jobs WHERE status = 'completed'")
        completed_jobs = cur.fetchall()

        if not completed_jobs:
            logger.info("ℹ️ No completed batch jobs ready for ingestion.")
            return

        # Initialize the new unified ChromaDB
        new_client = chromadb.PersistentClient(
            path=UNIFIED_DB_PATH,
            settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=True)
        )
        # Avoid embedding function mismatch error by registering the OpenAI Embedder
        from chromadb.utils import embedding_functions
        openai_ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=Config.OPENAI_API_KEY,
            model_name="text-embedding-3-small"
        )
        new_collection = new_client.get_or_create_collection(
            name=Config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=openai_ef
        )

        for job in completed_jobs:
            job_id = job['job_id']
            idx = job['batch_file_index']
            
            # Resilient check: Handle failed requests in the batch job
            try:
                job_details = self.openai_client.batches.retrieve(job_id)
                error_file_id = job_details.error_file_id
                if error_file_id:
                    logger.info(f"🔍 Found error file {error_file_id} for Job {job_id}. Downloading error items...")
                    err_response = self.openai_client.files.content(error_file_id)
                    err_text = err_response.text
                    failed_ids = []
                    for line in err_text.splitlines():
                        if line.strip():
                            obj = json.loads(line)
                            custom_id = obj.get("custom_id")
                            if custom_id:
                                failed_ids.append(custom_id)
                    
                    if failed_ids:
                        logger.info(f"🔄 Resetting {len(failed_ids)} failed requests from Job {job_id} to 'pending' in cache...")
                        placeholders = ",".join(["?"] * len(failed_ids))
                        cur.execute(f"""
                            UPDATE emails_cache 
                            SET status = 'pending', batch_job_id = NULL, batch_file_index = NULL
                            WHERE id IN ({placeholders})
                        """, failed_ids)
                        self.conn.commit()
            except Exception as e:
                logger.error(f"⚠️ Failed to retrieve/process errors for Job {job_id}: {e}")

            # Check if this batch has any 'queued' records left for ingestion
            cur.execute("SELECT COUNT(*) FROM emails_cache WHERE batch_job_id = ? AND status = 'queued'", (job_id,))
            pending_ingest = cur.fetchone()[0]
            if pending_ingest == 0:
                logger.info(f"⏭️ Batch {idx} (Job {job_id}) has already been fully ingested. Skipping.")
                continue

            logger.info(f"📥 Downloading results for Job {job_id}...")
            try:
                job_details = self.openai_client.batches.retrieve(job_id)
                output_file_id = job_details.output_file_id
                if not output_file_id:
                    logger.error(f"❌ No output file ID found for completed job {job_id}!")
                    continue

                file_response = self.openai_client.files.content(output_file_id)
                response_text = file_response.text

                logger.info(f"⚡ Parsing and preparing vector payloads for Batch {idx}...")
                
                # We will parse the output JSONL line by line
                # Each line: {"id": "batch_req...", "custom_id": "...", "response": {"status_code": 200, "body": {"data": [{"embedding": [...]}]}}}
                id_to_embedding = {}
                for line in response_text.splitlines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    custom_id = obj.get("custom_id")
                    
                    body = obj.get("response", {}).get("body", {})
                    data_arr = body.get("data", [])
                    if data_arr and custom_id:
                        embedding = data_arr[0].get("embedding")
                        id_to_embedding[custom_id] = embedding

                logger.info(f"📊 Downloaded {len(id_to_embedding)} embeddings from OpenAI output.")

                # Ingest in chunks of 2000 into ChromaDB
                cur.execute("SELECT id, document, metadata FROM emails_cache WHERE batch_job_id = ? AND status = 'queued'", (job_id,))
                rows = cur.fetchall()

                ids_batch, docs_batch, metas_batch, embs_batch = [], [], [], []
                chunk_size = 2000
                total_ingested = 0

                for row in rows:
                    doc_id = row['id']
                    if doc_id not in id_to_embedding:
                        continue
                    
                    ids_batch.append(doc_id)
                    docs_batch.append(row['document'])
                    
                    # Clean metadata pure values
                    meta_dict = json.loads(row['metadata'])
                    cleaned_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v for k, v in meta_dict.items()}
                    metas_batch.append(cleaned_meta)
                    embs_batch.append(id_to_embedding[doc_id])

                    if len(ids_batch) >= chunk_size:
                        new_collection.add(
                            ids=ids_batch,
                            embeddings=embs_batch,
                            documents=docs_batch,
                            metadatas=metas_batch
                        )
                        total_ingested += len(ids_batch)
                        logger.info(f"🚀 Ingested {total_ingested}/{len(rows)} documents to ChromaDB...")
                        ids_batch, docs_batch, metas_batch, embs_batch = [], [], [], []

                if ids_batch:
                    new_collection.add(
                        ids=ids_batch,
                        embeddings=embs_batch,
                        documents=docs_batch,
                        metadatas=metas_batch
                    )
                    total_ingested += len(ids_batch)

                logger.info(f"🎉 Fully ingested {total_ingested} documents into ChromaDB from Batch {idx}!")

                # Mark successfully ingested records as completed in local database
                cur.execute("UPDATE emails_cache SET status = 'completed' WHERE batch_job_id = ? AND status = 'queued'", (job_id,))
                cur.execute("UPDATE batch_jobs SET status = 'completed' WHERE job_id = ?", (job_id,))
                self.conn.commit()

            except Exception as e:
                logger.error(f"❌ Failed to ingest batch {idx} results: {e}")

    def embed_pending_direct(self):
        """Embeds all remaining pending emails directly using standard OpenAI API calls."""
        cur = self.conn.cursor()
        cur.execute("SELECT id, document, metadata FROM emails_cache WHERE status = 'pending'")
        rows = cur.fetchall()

        if not rows:
            logger.info("ℹ️ No pending emails to embed directly.")
            return

        logger.info(f"⚡ Found {len(rows)} pending emails to embed directly.")

        # Initialize ChromaDB client
        new_client = chromadb.PersistentClient(
            path=UNIFIED_DB_PATH,
            settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=True)
        )
        from chromadb.utils import embedding_functions
        openai_ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=Config.OPENAI_API_KEY,
            model_name="text-embedding-3-small"
        )
        new_collection = new_client.get_or_create_collection(
            name=Config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=openai_ef
        )

        # Dynamically partition into safe chunks (<120k characters and <=100 documents per request)
        chunks = []
        current_chunk = []
        current_char_count = 0

        for row in rows:
            doc = row['document']
            truncated_doc = doc[:12000] if doc else ""
            char_len = len(truncated_doc)

            # If adding this document would exceed 120,000 characters (~30k tokens) or chunk has 100 items, flush it
            if (current_char_count + char_len > 120000) or len(current_chunk) >= 100:
                chunks.append(current_chunk)
                current_chunk = []
                current_char_count = 0

            # Clean metadata pure values
            meta_dict = json.loads(row['metadata'])
            cleaned_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v for k, v in meta_dict.items()}

            current_chunk.append({
                "id": row['id'],
                "doc": truncated_doc,
                "meta": cleaned_meta
            })
            current_char_count += char_len

        if current_chunk:
            chunks.append(current_chunk)

        total_embedded = 0
        total_chunks = len(chunks)
        logger.info(f"📦 Dynamically partitioned into {total_chunks} safe embedding chunks...")

        for idx, chunk in enumerate(chunks):
            chunk_ids = [item['id'] for item in chunk]
            chunk_docs = [item['doc'] for item in chunk]
            chunk_metas = [item['meta'] for item in chunk]

            logger.info(f"📡 Requesting embeddings for chunk {idx + 1}/{total_chunks} ({len(chunk_ids)} items, ~{sum(len(d) for d in chunk_docs)} chars)...")
            try:
                # Call OpenAI standard API for embeddings
                response = self.openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=chunk_docs
                )

                # Extract embeddings
                embeddings = [item.embedding for item in response.data]

                logger.info(f"💾 Writing {len(chunk_ids)} embedded documents to ChromaDB...")
                new_collection.add(
                    ids=chunk_ids,
                    embeddings=embeddings,
                    documents=chunk_docs,
                    metadatas=chunk_metas
                )

                # Update SQLite cache statuses
                placeholders = ",".join(["?"] * len(chunk_ids))
                cur.execute(f"UPDATE emails_cache SET status = 'completed' WHERE id IN ({placeholders})", chunk_ids)
                self.conn.commit()

                total_embedded += len(chunk_ids)
                logger.info(f"✅ Successfully embedded and ingested {total_embedded}/{len(rows)} documents!")

            except Exception as e:
                logger.error(f"❌ Failed to embed chunk {idx + 1}: {e}")

    def close(self):
        self.conn.close()

def main():
    import sys
    migrator = BatchMigrator()
    
    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  python utility/batch_migrate.py scan      - Step 1: Scan old ChromaDB and load into local cache")
        print("  python utility/batch_migrate.py generate  - Step 2: Generate JSONL files for OpenAI")
        print("  python utility/batch_migrate.py submit    - Step 3: Upload files and start OpenAI Batch Embedding jobs")
        print("  python utility/batch_migrate.py check     - Step 4: Check active jobs progress")
        print("  python utility/batch_migrate.py ingest    - Step 5: Download completed batches and ingest into new ChromaDB")
        print("  python utility/batch_migrate.py status    - Show statistics of migration progress")
        print("  python utility/batch_migrate.py direct    - Embed remaining pending emails directly & synchronously")
        print("  python utility/batch_migrate.py run-all   - Run scan, generate, and submit sequentially\n")
        migrator.close()
        return

    cmd = sys.argv[1].lower()
    
    if cmd == "scan":
        migrator.populate_cache_from_old_chroma()
    elif cmd == "generate":
        migrator.generate_batch_files()
    elif cmd == "submit":
        migrator.submit_batches()
    elif cmd == "check":
        migrator.check_jobs_status()
    elif cmd == "ingest":
        migrator.ingest_completed_batches()
    elif cmd == "direct":
        migrator.embed_pending_direct()
    elif cmd == "status":
        total, stats = migrator.get_cache_counts()
        print("\n=========================================")
        print("MIGRATION STATUS SUMMARY")
        print("=========================================")
        print(f"Total Emails Monitored: {total}")
        for status, count in stats.items():
            print(f"  - {status.upper()}: {count} ({count/total:.1%})")
        
        # Check jobs
        cur = migrator.conn.cursor()
        cur.execute("SELECT batch_file_index, job_id, status FROM batch_jobs")
        jobs = cur.fetchall()
        print("\nSubmissions to OpenAI Batch API:")
        if not jobs:
            print("  None")
        for job in jobs:
            print(f"  - Batch {job['batch_file_index']}: Job ID={job['job_id']}, Status={job['status']}")
        print("=========================================\n")
    elif cmd == "run-all":
        migrator.populate_cache_from_old_chroma()
        migrator.generate_batch_files()
        migrator.submit_batches()
    else:
        print(f"❌ Unknown command: {cmd}")
        
    migrator.close()

if __name__ == "__main__":
    main()
