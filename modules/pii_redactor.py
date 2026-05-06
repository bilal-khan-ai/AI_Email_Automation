"""
PII Redaction Module using Presidio

Scrubs sensitive information before storing in RAG.
Enhanced with Indian PII support (Aadhaar, PAN, GSTIN, IFSC) and language-aware analysis.
"""

import logging
import re
from typing import Any, Dict, List, Tuple, Union

from presidio_analyzer import (
    AnalyzerEngine,
    RecognizerRegistry,
    PatternRecognizer,
    Pattern,
    EntityRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from langdetect import detect, DetectorFactory

# Set seed for reproducible language detection
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)


class PIIRedactor:
    def __init__(self):
        """Initialize Presidio analyzer and anonymizer with Indian PII and Multilingual support"""
        try:
            # 1. Initialize Registry with broader language support to resolve warnings
            self.supported_languages = ["en", "es", "it", "pl"]
            registry = RecognizerRegistry(supported_languages=self.supported_languages)
            registry.load_predefined_recognizers()

            # 2. Add Indian-Specific Recognizers
            
            # PAN (Permanent Account Number) - Improved regex + context
            pan_recognizer = PatternRecognizer(
                supported_entity="PAN_NUMBER",
                name="indian_pan_recognizer",
                patterns=[
                    Pattern(
                        name="pan_number",
                        regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
                        score=0.85,
                    )
                ],
                context=["pan", "income tax", "permanent account", "tax id", "pancard"]
            )

            # Aadhaar - custom class for Verhoeff check
            aadhaar_recognizer = AadhaarRecognizer()

            # GSTIN - 15 digits
            gstin_recognizer = PatternRecognizer(
                supported_entity="GSTIN",
                name="indian_gstin_recognizer",
                patterns=[
                    Pattern(
                        name="gstin",
                        regex=r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}\b",
                        score=0.85,
                    )
                ],
                context=["gst", "gstin", "tax", "invoice", "billing"]
            )

            # IFSC Code - 11 characters
            ifsc_recognizer = PatternRecognizer(
                supported_entity="IFSC_CODE",
                name="indian_ifsc_recognizer",
                patterns=[
                    Pattern(
                        name="ifsc",
                        regex=r"\b[A-Z]{4}0[A-Z0-9]{6}\b",
                        score=0.85,
                    )
                ],
                context=["ifsc", "bank", "branch", "transfer", "neft", "rtgs"]
            )

            # Voter ID (EPIC) - 10 characters
            voter_id_recognizer = PatternRecognizer(
                supported_entity="VOTER_ID",
                name="indian_voter_id_recognizer",
                patterns=[
                    Pattern(
                        name="voter_id",
                        regex=r"\b[A-Z]{3}\d{7}\b",
                        score=0.65,
                    )
                ],
                context=["voter id", "epic", "election card", "identity card"]
            )

            # Bank account numbers: refined with more Indian context
            bank_account_recognizer = PatternRecognizer(
                supported_entity="BANK_ACCOUNT_NUMBER",
                name="bank_account_number_recognizer",
                patterns=[
                    Pattern(
                        name="bank_account_number",
                        regex=r"\b\d{9,18}\b",
                        score=0.45,
                    )
                ],
                context=[
                    "account", "a/c", "acct", "bank", "iban", "ifsc", 
                    "beneficiary", "transfer", "savings", "current account"
                ],
            )

            registry.add_recognizer(pan_recognizer)
            registry.add_recognizer(aadhaar_recognizer)
            registry.add_recognizer(gstin_recognizer)
            registry.add_recognizer(ifsc_recognizer)
            registry.add_recognizer(voter_id_recognizer)
            registry.add_recognizer(bank_account_recognizer)

            # 3. NLP engine config: Alias ES/IT/PL to EN model to avoid heavy downloads
            nlp_engine = None
            try:
                nlp_configuration = {
                    "nlp_engine_name": "spacy",
                    "models": [
                        {
                            "lang_code": lang,
                            "model_name": "en_core_web_sm",
                        } for lang in self.supported_languages
                    ],
                }
                nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
            except Exception as e:
                logger.warning(f"PII Redactor: NLP engine config failed. Details: {e}")

            if nlp_engine is not None:
                self.analyzer = AnalyzerEngine(
                    registry=registry, 
                    nlp_engine=nlp_engine,
                    supported_languages=self.supported_languages
                )
            else:
                self.analyzer = AnalyzerEngine(
                    registry=registry,
                    supported_languages=self.supported_languages
                )

            self.anonymizer = AnonymizerEngine()

            self.entities_to_redact = [
                "CREDIT_CARD", "PHONE_NUMBER", "EMAIL_ADDRESS", "AADHAR_NUMBER",
                "PAN_NUMBER", "GSTIN", "IFSC_CODE", "VOTER_ID",
                "BANK_ACCOUNT_NUMBER", "IBAN_CODE", "IP_ADDRESS", "LOCATION", "PERSON"
            ]

            self.operators = {
                "CREDIT_CARD": OperatorConfig("replace", {"new_value": "[CREDIT_CARD]"}),
                "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "[PHONE]"}),
                "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "[EMAIL]"}),
                "AADHAR_NUMBER": OperatorConfig("replace", {"new_value": "[AADHAR]"}),
                "PAN_NUMBER": OperatorConfig("replace", {"new_value": "[PAN]"}),
                "GSTIN": OperatorConfig("replace", {"new_value": "[GSTIN]"}),
                "IFSC_CODE": OperatorConfig("replace", {"new_value": "[IFSC]"}),
                "VOTER_ID": OperatorConfig("replace", {"new_value": "[VOTER_ID]"}),
                "BANK_ACCOUNT_NUMBER": OperatorConfig("replace", {"new_value": "[BANK_ACCOUNT]"}),
                "IBAN_CODE": OperatorConfig("replace", {"new_value": "[IBAN]"}),
                "IP_ADDRESS": OperatorConfig("replace", {"new_value": "[IP_ADDRESS]"}),
                "LOCATION": OperatorConfig("replace", {"new_value": "[LOCATION]"}),
                "PERSON": OperatorConfig("replace", {"new_value": "[PERSON]"}),
            }

            logger.info("PII Redactor initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize PII Redactor: {e}")
            raise

    def _detect_language(self, text: str) -> str:
        """Detect language of text, defaulting to 'en'"""
        if not text or len(text.strip()) < 10:
            return "en"
        try:
            lang = detect(text)
            return lang if lang in self.supported_languages else "en"
        except:
            return "en"

    def _redact_text_with_metadata(self, text: str) -> Tuple[str, int, List[str]]:
        if not text:
            return text, 0, []

        try:
            lang = self._detect_language(text)
            
            results = self.analyzer.analyze(
                text=text,
                language=lang,
                entities=self.entities_to_redact,
            )

            if not results:
                return text, 0, []

            entity_types = sorted({r.entity_type for r in results})
            anonymized = self.anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators=self.operators,
            )

            return anonymized.text, len(results), entity_types

        except Exception as e:
            logger.error(f"Error during PII redaction: {e}")
            return text, 0, []

    def redact_email_content(self, payload: Union[str, Dict[str, Any]]) -> Union[str, Dict[str, Any]]:
        """Redact PII from a string OR from an email dict."""
        if isinstance(payload, str):
            red, _, _ = self._redact_text_with_metadata(payload)
            return red

        if not isinstance(payload, dict):
            return payload

        redacted = dict(payload)
        total_entities = 0
        types: List[str] = []

        for field in ["subject", "body"]:
            original = redacted.get(field, "")
            if not isinstance(original, str):
                continue
            cleaned, count, entity_types = self._redact_text_with_metadata(original)
            if count:
                total_entities += count
                types.extend(entity_types)
                redacted[field] = cleaned

        redacted["pii_redacted"] = bool(total_entities)
        redacted["pii_entities_found"] = int(total_entities)
        redacted["pii_entity_types"] = sorted(set(types))

        return redacted


class AadhaarRecognizer(PatternRecognizer):
    """Custom Recognizer for Indian Aadhaar Number with Verhoeff Checksum."""
    
    def __init__(self):
        patterns = [
            Pattern(
                name="aadhaar_pattern",
                regex=r"[2-9]{1}\d{3}\s?\d{4}\s?\d{4}",
                score=0.7,
            )
        ]
        # We register it for 'en' but it will be picked up because it's in the registry
        # and we analyzed with supported_languages
        super().__init__(
            supported_entity="AADHAR_NUMBER",
            supported_language="en",
            patterns=patterns,
            context=["aadhaar", "aadhar", "uidai", "uid"]
        )

    def validate_result(self, pattern_text: str) -> bool:
        """Verhoeff algorithm validation."""
        d = [
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
            [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
            [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
            [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
            [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
            [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
            [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
            [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
            [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
        ]
        p = [
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
            [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
            [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
            [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
            [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
            [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
            [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]
        ]
        number = "".join(filter(str.isdigit, number := pattern_text))
        if len(number) != 12: return False
        c = 0
        for i, digit in enumerate(map(int, reversed(number))):
            c = d[c][p[i % 8][digit]]
        return c == 0
