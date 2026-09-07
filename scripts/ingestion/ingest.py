# /mnt/data/ingest.py
import sys
import os
import json
import logging
import signal
import threading
from bs4 import BeautifulSoup
from config import Config
from data_access.vector_db import VectorDatabase
from services.connectors.graph_connector import GraphConnector
from services.processors.image_processor import ImageProcessor

# ---------- CONFIGURATION ----------
# Set to True to disable all GPU usage (useful for low-memory environments)
FORCE_CPU = False
# -----------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EMAIL_DUMP_FILE = "data/emails_dump.json"
PROCESS_CHECKPOINT = "data/process_checkpoint.json"

# ---------- HARD Ctrl+C EXIT ----------
def load_process_checkpoint():
    if os.path.exists(PROCESS_CHECKPOINT):
        with open(PROCESS_CHECKPOINT, "r") as f:
            return json.load(f).get("last_index", 0)
    return 0


def save_process_checkpoint(index: int):
    with open(PROCESS_CHECKPOINT, "w") as f:
        json.dump({"last_index": index}, f)


def signal_handler(signum, frame):
    logger.warning("\n⚠️ Immediate shutdown requested (Ctrl+C)")
    try:
        idx = load_process_checkpoint()
        save_process_checkpoint(idx)
        logger.info(f"Checkpoint saved at index {idx}")
    finally:
        sys.exit(130)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

threading.excepthook = lambda args: sys.exit(130)

# ---------- Utilities ----------
def clean_html(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator=" ", strip=True)

# ---------- Phase 1: Fetch ----------
def fetch_and_persist_emails(graph: GraphConnector, user_email: str, max_emails: int):
    existing_emails = []

    os.makedirs(os.path.dirname(EMAIL_DUMP_FILE), exist_ok=True)

    if os.path.exists(EMAIL_DUMP_FILE):
        with open(EMAIL_DUMP_FILE, "r", encoding="utf-8") as f:
            existing_emails = json.load(f)
        logger.info(f"Loaded {len(existing_emails)} emails from disk.")

    skip_count = len(existing_emails)
    remaining_to_fetch = max_emails - skip_count

    if remaining_to_fetch <= 0:
        logger.info("Target reached. No fetch needed.")
        return

    def save_checkpoint(batch):
        nonlocal existing_emails
        existing_emails.extend(batch)
        with open(EMAIL_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_emails, f)
        logger.info(f"Checkpoint saved ({len(existing_emails)} emails)")

    logger.info(f"Fetching {remaining_to_fetch} emails...")
    graph.fetch_all_emails(
        user_email,
        max_emails=remaining_to_fetch,
        skip=skip_count,
        checkpoint_callback=save_checkpoint,
    )

# ---------- Phase 2: Processing ----------
def process_emails(vector_db: VectorDatabase, graph: GraphConnector, img_processor: ImageProcessor):
    logger.info("Starting processing phase")

    with open(EMAIL_DUMP_FILE, "r", encoding="utf-8") as f:
        emails = json.load(f)

    start_index = load_process_checkpoint()
    logger.info(f"Resuming from index {start_index}")

    processed = start_index
    img_count = 0

    MIN_IMAGE_SIZE_BYTES = 12 * 1024

    for i in range(start_index, len(emails)):
        email = emails[i]

        try:
            clean_body = clean_html(email.get("body", ""))
            image_descriptions = []

            attachments = [att for att in email.get("attachments", []) if isinstance(att, dict)]

            # Identify image attachments that pass size and content-type gate
            image_candidates = []
            for att in attachments:
                file_size = att.get("size", 0)
                if file_size < MIN_IMAGE_SIZE_BYTES:
                    continue
                ct = (att.get("content_type") or "").lower()
                if ct in ("image/jpeg", "image/png", "image/jpg"):
                    image_candidates.append(att)

            # Process each image candidate
            for att in image_candidates:
                img_bytes = graph.get_attachment_sync(
                    Config.USER_EMAIL,
                    email["id"],
                    att["id"]
                )
                if not img_bytes:
                    continue
                desc = img_processor.process_image(img_bytes)
                if desc:
                    image_descriptions.append(
                        f"File {att.get('name', 'img')}: {desc}"
                    )
                    img_count += 1

            # If there is nothing (no body and no images), skip DB addition
            if not clean_body.strip() and not image_descriptions:
                processed += 1
                save_process_checkpoint(processed)
                continue

            vector_db.add_email(
                email_id=email["id"],
                subject=email.get("subject"),
                body=clean_body,
                sender=email.get("sender"),
                image_descriptions=image_descriptions,
                metadata={
                    "received": email.get("received"),
                    "has_images": bool(image_descriptions),
                },
            )

            processed += 1
            save_process_checkpoint(processed)

            if processed % 100 == 0:
                logger.info(f"Processed {processed}/{len(emails)}")

        except Exception as e:
            logger.error(f"Email failed at index {i}: {e}")
            processed += 1
            save_process_checkpoint(processed)

    logger.info(f"Processing complete. Images processed: {img_count}")

# ---------- Main ----------
def main():
    logger.info("=" * 80)
    logger.info("CRASH-RESILIENT INGESTION STARTED")
    if FORCE_CPU:
        logger.info("MODE: FORCE_CPU ENABLED (GPU Disabled)")
    logger.info("=" * 80)

    logger.info("Authenticating with Microsoft Graph...")
    graph = GraphConnector(
        Config.AZURE_CLIENT_ID,
        Config.AZURE_CLIENT_SECRET,
        Config.AZURE_TENANT_ID,
    )

    if not graph.authenticate():
        logger.error("Graph authentication failed")
        sys.exit(1)

    logger.info("Initializing Image Processor (Cloud-based/OpenAI Vision)...")
    img_processor = ImageProcessor() # No force_cpu argument anymore, uses AI agent if attached
    # Note: in standalone ingest we might need an AI agent if we want Vision.
    # But for just text ingestion, it's fine.
    # To enable vision in ingest, we'd need to init AI agent.
    from services.connectors.openai_agent import OpenAIAgent
    ai = OpenAIAgent(Config.OPENAI_API_KEY)
    ai.authenticate()
    img_processor.set_ai_agent(ai)

    logger.info("Initializing Vector DB...")
    vector_db = VectorDatabase(
        Config.CHROMA_DB_PATH,
        Config.COLLECTION_NAME,
        force_cpu=FORCE_CPU,
    )

    logger.info("Fetching emails...")
    fetch_and_persist_emails(
        graph,
        Config.USER_EMAIL,
        max_emails=100000,
    )

    logger.info("Processing emails...")
    process_emails(vector_db, graph, img_processor)

    logger.info("=" * 80)
    logger.info("INGESTION COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
