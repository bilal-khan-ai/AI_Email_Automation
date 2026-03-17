import re
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

class SubjectMatcher:
    """
    Robust fuzzy subject matching for ticket linking.
    
    Design principles:
    - Deterministic (same inputs → same output)
    - Normalized comparison (case, whitespace, prefixes)
    - Token-based similarity (handles word reordering)
    - Configurable threshold
    """
    
    def __init__(self, similarity_threshold: float = 0.75):
        """
        Initialize subject matcher.
        
        Args:
            similarity_threshold: Minimum similarity score (0.0-1.0) for match
        """
        self.similarity_threshold = similarity_threshold
        
        self.strip_patterns = [
            r'^re:\s*',
            r'^fwd:\s*',
            r'^fw:\s*',
            r'^\[.*?\]\s*',
            r'\s*\[#\d+\]',
        ]
    
    def normalize_subject(self, subject: str) -> str:
        """Normalize subject for comparison"""
        if not subject:
            return ""
        
        normalized = subject.lower().strip()
        
        for pattern in self.strip_patterns:
            normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)
        
        normalized = ' '.join(normalized.split())
        normalized = re.sub(r'[!?.,:;]+$', '', normalized)
        
        return normalized.strip()
    
    def calculate_similarity(self, subject1: str, subject2: str) -> float:
        """Calculate similarity score between two subjects"""
        norm1 = self.normalize_subject(subject1)
        norm2 = self.normalize_subject(subject2)
        
        if not norm1 or not norm2:
            return 0.0
        
        if norm1 == norm2:
            return 1.0
        
        matcher = SequenceMatcher(None, norm1, norm2)
        return matcher.ratio()
    
    def find_matching_ticket(self, subject: str, sender: str, 
                            active_tickets: list) -> dict:
        """Find matching ticket using fuzzy subject matching"""
        if not active_tickets:
            return None
        
        customer_tickets = [
            t for t in active_tickets
            if t.get('customer_email', '').lower() == sender.lower()
        ]
        
        if not customer_tickets:
            return None
        
        best_match = None
        best_score = 0.0
        
        for ticket in customer_tickets:
            ticket_subject = ticket.get('subject', '')
            score = self.calculate_similarity(subject, ticket_subject)
            
            if score > best_score:
                best_score = score
                best_match = ticket
        
        if best_score >= self.similarity_threshold:
            logger.info(
                f"🔗 Fuzzy match: '{subject}' → '{best_match.get('subject')}' "
                f"(score: {best_score:.2f})"
            )
            return best_match
        
        return None
