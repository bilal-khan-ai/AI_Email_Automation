# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Services Processors Package Initialization - start
"""
Content Processing & Transformation
Document extraction, table parsing, image OCR/optimization, and PII redaction.
"""

from .doc_processor import DocProcessor
from .tables_processor import TablesProcessor
from .image_processor import ImageProcessor
from .pii_redactor import PIIRedactor

def redact_pii(payload):
    return PIIRedactor().redact_email_content(payload)

__all__ = [
    'DocProcessor',
    'TablesProcessor',
    'ImageProcessor',
    'PIIRedactor',
    'redact_pii'
]
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Services Processors Package Initialization - end
