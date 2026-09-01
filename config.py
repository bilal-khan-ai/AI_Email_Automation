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
    GENERATIONAL_MODEL = os.getenv('GENERATIONAL_MODEL', 'gpt-5-mini')
    INTERPRETATION_MODEL = os.getenv('INTERPRETATION_MODEL', 'gpt-4.1-nano-2025-04-14')
    ANALYSIS_MODEL = os.getenv('ANALYSIS_MODEL', 'gpt-4.1-mini-2025-04-14')
    WEB_SEARCH_MODEL = os.getenv('WEB_SEARCH_MODEL', 'gpt-4o-mini-2024-07-18')
    
    # Chroma Database settings
    CHROMA_DB_PATH = os.getenv('CHROMA_DB_PATH', './data/chroma_db')
    COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'new_support_emails')
    BOOKSTACK_DB_PATH = os.getenv('BOOKSTACK_DB_PATH', './data/chroma_db')
    BOOKSTACK_COLLECTION = os.getenv('BOOKSTACK_COLLECTION', 'bookstack_db')

    # PSQL
    POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
    POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
    # Bilal Khan (12/08/2026) Issue No  Sheet_Name  - Updated default POSTGRES_DB fallback to support_db
    POSTGRES_DB = os.getenv('POSTGRES_DB', 'support_db')
    # Bilal Khan (12/08/2026) Issue No  Sheet_Name  - Updated default POSTGRES_USER and POSTGRES_PASSWORD fallbacks
    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Remove hardcoded password fallback (read strictly from .env)
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
    TEST_CC = os.getenv('TEST_CC', '')
    TEST_SUBJECT_TAG = os.getenv('TEST_SUBJECT_TAG', '[TEST MODE] ')
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'go_make_a_random_key_to_add_in_env_file')
    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Add CORS_ALLOWED_ORIGINS configuration - start
    CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', '*')
    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Add CORS_ALLOWED_ORIGINS configuration - end

    # Cleanup daemon settings
    ENABLE_TICKET_CLEANUP_DAEMON = os.getenv('ENABLE_TICKET_CLEANUP_DAEMON', 'True').lower() == 'true'
    CLEANUP_DAEMON_INTERVAL_SECONDS = int(os.getenv('CLEANUP_DAEMON_INTERVAL_SECONDS', 3600))
    SOFT_DELETE_CLOSED_AFTER_DAYS = int(os.getenv('SOFT_DELETE_CLOSED_AFTER_DAYS', 1))
    HARD_DELETE_AFTER_DAYS = int(os.getenv('HARD_DELETE_AFTER_DAYS', 6))
    CLEANUP_BATCH_SIZE = int(os.getenv('CLEANUP_BATCH_SIZE', 100))
    
    # Table processing settings
    TABLES_INCLUDE_PREVIEW = os.getenv('TABLES_INCLUDE_PREVIEW', 'False').lower() == 'true'
    TABLES_PREVIEW_ROWS = int(os.getenv('TABLES_PREVIEW_ROWS', 8))

    # SLA Analytics Settings
    INTERNAL_NOTE_AS_RESPONSE = os.getenv('INTERNAL_NOTE_AS_RESPONSE', 'False').lower() == 'true'

    # RAG Settings
    ENABLE_RAG = os.getenv('ENABLE_RAG', 'False').lower() == 'true'

    # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Add ENABLE_AI and Azure DevOps settings - start
    ENABLE_AI = os.getenv('ENABLE_AI', 'False').lower() == 'true'

    # Azure DevOps Settings
    AZURE_DEVOPS_ORG = os.getenv('AZURE_DEVOPS_ORG', '')
    AZURE_DEVOPS_PROJECT = os.getenv('AZURE_DEVOPS_PROJECT', '')
    AZURE_DEVOPS_PAT = os.getenv('AZURE_DEVOPS_PAT', '')
    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Azure DevOps Polling Interval Configuration - start
    AZURE_DEVOPS_POLLING_INTERVAL = int(os.getenv('AZURE_DEVOPS_POLLING_INTERVAL', 600))
    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Azure DevOps Polling Interval Configuration - end
    # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Add ENABLE_AI and Azure DevOps settings - end

    @classmethod
    def reload(cls):
        """Reload configuration from .env file"""
        from dotenv import load_dotenv as _load_dotenv
        import os
        _load_dotenv(override=True)
        # Re-set all class attributes
        cls.USER_EMAIL = os.getenv('USER_EMAIL')
        cls.POLLING_INTERVAL = int(os.getenv('POLLING_INTERVAL', 600))
        cls.PROCESSING_DAYS_BACK = int(os.getenv('PROCESSING_DAYS_BACK', 2))
        cls.TEST_MODE = os.getenv('TEST_MODE', 'True').lower() == 'true'
        cls.TEST_EMAIL = os.getenv('TEST_EMAIL', 'bilal.khan@greenwaresolutions.com')
        cls.TEST_CC = os.getenv('TEST_CC', '')
        cls.TEST_SUBJECT_TAG = os.getenv('TEST_SUBJECT_TAG', '[TEST MODE] ')
        cls.ENABLE_TICKET_CLEANUP_DAEMON = os.getenv('ENABLE_TICKET_CLEANUP_DAEMON', 'True').lower() == 'true'
        cls.SOFT_DELETE_CLOSED_AFTER_DAYS = int(os.getenv('SOFT_DELETE_CLOSED_AFTER_DAYS', 1))
        cls.HARD_DELETE_AFTER_DAYS = int(os.getenv('HARD_DELETE_AFTER_DAYS', 6))
        cls.INTERNAL_NOTE_AS_RESPONSE = os.getenv('INTERNAL_NOTE_AS_RESPONSE', 'False').lower() == 'true'
        cls.ENABLE_RAG = os.getenv('ENABLE_RAG', 'False').lower() == 'true'
        # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Reload ENABLE_AI and Azure DevOps settings - start
        cls.ENABLE_AI = os.getenv('ENABLE_AI', 'False').lower() == 'true'
        cls.AZURE_DEVOPS_ORG = os.getenv('AZURE_DEVOPS_ORG', '')
        cls.AZURE_DEVOPS_PROJECT = os.getenv('AZURE_DEVOPS_PROJECT', '')
        cls.AZURE_DEVOPS_PAT = os.getenv('AZURE_DEVOPS_PAT', '')
        # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Reload Azure DevOps Polling Interval - start
        cls.AZURE_DEVOPS_POLLING_INTERVAL = int(os.getenv('AZURE_DEVOPS_POLLING_INTERVAL', 600))
        # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Reload Azure DevOps Polling Interval - end
        # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Reload ENABLE_AI and Azure DevOps settings - end
        # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic reload of all configurable settings - start
        cls.AUTO_GENERATE_RESPONSES = os.getenv('AUTO_GENERATE_RESPONSES', 'True').lower() == 'true'
        cls.TOP_K_RESULTS = int(os.getenv('TOP_K_RESULTS', 5))
        cls.DAYS_TO_KEEP_TICKET = int(os.getenv('DAYS_TO_KEEP_TICKET', 30))
        cls.CLEANUP_DAEMON_INTERVAL_SECONDS = int(os.getenv('CLEANUP_DAEMON_INTERVAL_SECONDS', 3600))
        cls.CLEANUP_BATCH_SIZE = int(os.getenv('CLEANUP_BATCH_SIZE', 100))
        cls.TABLES_INCLUDE_PREVIEW = os.getenv('TABLES_INCLUDE_PREVIEW', 'False').lower() == 'true'
        cls.TABLES_PREVIEW_ROWS = int(os.getenv('TABLES_PREVIEW_ROWS', 8))
        # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic reload of all configurable settings - end
        # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Reload CORS_ALLOWED_ORIGINS
        cls.CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', '*')