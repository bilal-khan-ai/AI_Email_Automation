"""
PII Redaction Module using Presidio

Scrubs sensitive information before storing in RAG.

Patch notes (Feb 2026):
- redact_email_content now supports BOTH:
  1) text: str -> returns redacted str (backward-compatible)
  2) email: dict -> returns a redacted dict with fields:
     - pii_redacted: bool
     - pii_entities_found: int
     - pii_entity_types: list[str]
This fixes the common pipeline bug where callers pass the whole email dict.
"""

import logging
from typing import Any, Dict, List, Tuple, Union

from presidio_analyzer import (
    AnalyzerEngine,
    RecognizerRegistry,
    PatternRecognizer,
    Pattern,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

logger = logging.getLogger(__name__)


class PIIRedactor:
    def __init__(self):
        """Initialize Presidio analyzer and anonymizer"""
        try:
            registry = RecognizerRegistry()
            registry.load_predefined_recognizers()

            # PAN (Permanent Account Number) - strong, low false positives
            pan_recognizer = PatternRecognizer(
                supported_entity="PAN_NUMBER",
                name="pan_number_recognizer",
                patterns=[
                    Pattern(
                        name="pan_number",
                        regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
                        score=0.85,
                    )
                ],
            )

            # Aadhaar - allow optional spaces: #### #### #### or ############
            aadhaar_recognizer = PatternRecognizer(
                supported_entity="AADHAR_NUMBER",
                name="aadhaar_number_recognizer",
                patterns=[
                    Pattern(
                        name="aadhaar_number",
                        regex=r"\b\d{4}\s?\d{4}\s?\d{4}\b",
                        score=0.65,
                    )
                ],
            )

            # Bank account numbers: highly variable → keep conservative + require context
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
                    "account",
                    "a/c",
                    "acct",
                    "bank",
                    "iban",
                    "ifsc",
                    "beneficiary",
                    "transfer",
                ],
            )

            registry.add_recognizer(pan_recognizer)
            registry.add_recognizer(aadhaar_recognizer)
            registry.add_recognizer(bank_account_recognizer)

            # NLP engine config: ignore spaCy NER label FAC (facility) to remove warning
            nlp_engine = None
            try:
                nlp_configuration = {
                    "nlp_engine_name": "spacy",
                    "models": [
                        {
                            "lang_code": "en",
                            "model_name": "en_core_web_sm",
                            "ner_model_configuration": {
                                "labels_to_ignore": ["FAC"],
                            },
                        }
                    ],
                }
                nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
            except Exception as e:
                logger.warning(
                    "PII Redactor: spaCy NLP engine config failed (likely missing model). "
                    f"Falling back to default Presidio NLP engine. Details: {e}"
                )

            if nlp_engine is not None:
                self.analyzer = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine)
            else:
                self.analyzer = AnalyzerEngine(registry=registry)

            self.anonymizer = AnonymizerEngine()

            self.entities_to_redact = [
                "CREDIT_CARD",
                "PHONE_NUMBER",
                "EMAIL_ADDRESS",
                "AADHAR_NUMBER",
                "PAN_NUMBER",
                "BANK_ACCOUNT_NUMBER",
                "IBAN_CODE",
                "IP_ADDRESS",
                "LOCATION",
            ]

            self.operators = {
                "CREDIT_CARD": OperatorConfig("replace", {"new_value": "[CREDIT_CARD]"}),
                "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "[PHONE]"}),
                "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "[EMAIL]"}),
                "AADHAR_NUMBER": OperatorConfig("replace", {"new_value": "[AADHAR_NUMBER]"}),
                "PAN_NUMBER": OperatorConfig("replace", {"new_value": "[PAN_NUMBER]"}),
                "BANK_ACCOUNT_NUMBER": OperatorConfig("replace", {"new_value": "[BANK_ACCOUNT]"}),
                "IBAN_CODE": OperatorConfig("replace", {"new_value": "[IBAN]"}),
                "IP_ADDRESS": OperatorConfig("replace", {"new_value": "[IP_ADDRESS]"}),
                "LOCATION": OperatorConfig("replace", {"new_value": "[LOCATION]"}),
            }

            logger.info("PII Redactor initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize PII Redactor: {e}")
            raise

    def _redact_text_with_metadata(self, text: str) -> Tuple[str, int, List[str]]:
        if not text:
            return text, 0, []

        try:
            results = self.analyzer.analyze(
                text=text,
                language="en",
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
        """
        Redact PII from a string OR from an email dict.

        If payload is a dict, the returned dict includes:
        - pii_redacted: bool
        - pii_entities_found: int
        - pii_entity_types: list[str]
        """
        # Backward-compatible behavior
        if isinstance(payload, str):
            red, _, _ = self._redact_text_with_metadata(payload)
            return red

        # Dict mode (used by main.py)
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
