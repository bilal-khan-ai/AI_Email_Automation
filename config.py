import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Azure AD credentials
    AZURE_CLIENT_ID = os.getenv('AZURE_CLIENT_ID')
    AZURE_CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET')
    AZURE_TENANT_ID = os.getenv('AZURE_TENANT_ID')
    
    # User email to monitor
    USER_EMAIL = os.getenv('USER_EMAIL')
    
    # OpenAI API
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    
    # Chroma Database settings
    CHROMA_DB_PATH = os.getenv('CHROMA_DB_PATH', './chroma_db')
    COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'support_emails')
    BOOKSTACK_DB_PATH = os.getenv('BOOKSTACK_DB_PATH','./chromaDB')
    BOOKSTACK_COLLECTION = os.getenv('BOOKSTACK_COLLECTION','bookstack_db')

    # PSQL
    POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
    POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
    POSTGRES_DB = os.getenv('POSTGRES_DB', 'support_tickets')
    POSTGRES_USER = os.getenv('POSTGRES_USER', 'postgres')
    POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', '')
    POSTGRES_MIN_CONN = int(os.getenv('POSTGRES_MIN_CONN', 1))
    POSTGRES_MAX_CONN = int(os.getenv('POSTGRES_MAX_CONN', 10))

    # Processing settings
    POLLING_INTERVAL = int(os.getenv('POLLING_INTERVAL', 600))  # seconds
    TOP_K_RESULTS = int(os.getenv('TOP_K_RESULTS', 5))
    AUTO_GENERATE_RESPONSES = os.getenv('AUTO_GENERATE_RESPONSES', 'True').lower() == 'true'
    PROCESSING_DAYS_BACK = int(os.getenv('PROCESSING_DAYS_BACK',2))
    DAYS_TO_KEEP_TICKET = int(os.getenv('DAYS_TO_KEEP_TICKET', 30))
    
    # Test mode settings
    TEST_MODE = os.getenv('TEST_MODE', 'True').lower() == 'true'
    TEST_EMAIL = os.getenv('TEST_EMAIL', 'bilal.khan@greenwaresolutions.com')
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'go_make_a_random_key_to_add_in_env_file')

    # Cleanup daemon settings
    ENABLE_TICKET_CLEANUP_DAEMON = os.getenv('ENABLE_TICKET_CLEANUP_DAEMON', 'True').lower() == 'true'
    CLEANUP_DAEMON_INTERVAL_SECONDS = int(os.getenv('CLEANUP_DAEMON_INTERVAL_SECONDS', 3600))
    SOFT_DELETE_CLOSED_AFTER_DAYS = int(os.getenv('SOFT_DELETE_CLOSED_AFTER_DAYS', DAYS_TO_KEEP_TICKET))
    HARD_DELETE_AFTER_DAYS = int(os.getenv('HARD_DELETE_AFTER_DAYS', 7))
    CLEANUP_BATCH_SIZE = int(os.getenv('CLEANUP_BATCH_SIZE', 100))
    
    # Table processing settings
    TABLES_INCLUDE_PREVIEW = os.getenv('TABLES_INCLUDE_PREVIEW', 'False').lower() == 'true'
    TABLES_PREVIEW_ROWS = int(os.getenv('TABLES_PREVIEW_ROWS', 8))