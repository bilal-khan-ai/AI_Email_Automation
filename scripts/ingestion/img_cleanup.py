import logging
from data_access.vector_db import VectorDatabase
from config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def purge_logos():
    logger.info("Initializing DB for Cleanup...")
    db = VectorDatabase(Config.CHROMA_DB_PATH, Config.COLLECTION_NAME, force_cpu=True)
    
    # These are terms that, if found in the document text, indicate we indexed a logo/icon
    # We look for the format [Visual: a company logo...] which your processor generates
    noise_terms = [
        "a company logo", 
        "icon of a", 
        "social media", 
        "twitter logo", 
        "facebook logo", 
        "linkedin logo",
        "close up of a logo",
        "signature",""
        "beyond beyond beyond beyond",
        "the b & s securities logo",
        "a blue ball with a white star on it",
        "a business card with a flat design",
        "the logo for greenware solutions",
        "NIRMAL BANG",
        "the app icon"
    ]
    
    total_deleted = 0
    
    for term in noise_terms:
        logger.info(f"Scanning for noise term: '{term}'...")
        
        # Query the DB for documents containing this specific phrase
        # We rely on Chroma's 'contains' filter for the document content
        results = db.collection.get(
            where_document={"$contains": term}
        )
        
        ids_to_delete = results['ids']
        
        if ids_to_delete:
            logger.info(f"Found {len(ids_to_delete)} items matching '{term}'. Deleting...")
            
            # Delete by ID
            db.collection.delete(ids=ids_to_delete)
            total_deleted += len(ids_to_delete)
        else:
            logger.info(f"No items found for '{term}'.")

    logger.info("="*40)
    logger.info(f"Cleanup Complete. Total items removed: {total_deleted}")
    logger.info("="*40)

if __name__ == "__main__":
    purge_logos()