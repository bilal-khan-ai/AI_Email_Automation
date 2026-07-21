"""
OpenAI Agent with Enhanced RAG Quality and Response Provenance

Improvements:
1. Treats only the latest customer message as the question
2. Provides full thread as structured context with clear speaker roles
3. Stores vector search provenance (docs used + similarity scores)
4. Light summarization of older thread content for token efficiency
5. Better structured logging
"""

import logging
import re
import json
from openai import OpenAI
from typing import List, Dict, Optional, Tuple, Any, cast
from config import Config
from datetime import datetime

# Model Configuration - Dynamically linked to Config for hot-reloading
CONTEXT_WINDOW = 400000
MAX_COMPLETION_TOKENS = 16384

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OpenAIAgent:
    def __init__(self, api_key: str):
        """Initialize OpenAI client"""
        self.api_key = api_key
        self.client: Optional[OpenAI] = None
        
    @property
    def GENERATIONAL_MODEL(self):
        return Config.GENERATIONAL_MODEL
        
    @property
    def INTERPRETATION_MODEL(self):
        return Config.INTERPRETATION_MODEL
        
    @property
    def ANALYSIS_MODEL(self):
        return Config.ANALYSIS_MODEL
        
    def authenticate(self):
        """Authenticate with OpenAI API"""
        try:
            self.client = OpenAI(api_key=self.api_key)
            logger.info("✅ Successfully authenticated with OpenAI")
            return True
        except Exception as e:
            logger.error(f"❌ OpenAI authentication failed: {e}")
            return False
            
    def _preprocess_text(self, text: str) -> str:
        """
        Clean text for interpretation:
        - Strip common signatures
        - Remove repeated whitespace
        """
        if not text:
            return ""
            
        # 1. Simple signature stripping (heuristics)
        sig_patterns = [
            r'(?m)^--\s*$',  # Standard dash signature
            r'(?i)best regards.*',
            r'(?i)kind regards.*',
            r'(?i)thanks and regards.*',
            r'(?i)thanks,.*',
            r'(?i)thank you,.*',
            r'(?i)sincerely,.*',
            r'(?i)sent from my .*',
            r'(?i)regards,.*'
        ]
        
        lines = text.split('\n')
        clean_lines = []
        for line in lines:
            is_sig = False
            for pattern in sig_patterns:
                if re.search(pattern, line.strip()):
                    is_sig = True
                    break
            if is_sig:
                break
            clean_lines.append(line)
            
        text = '\n'.join(clean_lines).strip()
        
        # 2. Collapse whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text

    def interpret_message(self, text: str, subject: str = "") -> Dict[str, Any]:
        """
        Stage 1: Interpretation Layer
        Classify intent, urgency, and requirements using gpt-4.1-nano-2025-04-14.
        """
        if not self.client:
            raise Exception("Not authenticated.")

        clean_text = self._preprocess_text(text)
        
        try:
            prompt = f"Subject: {subject}\n\nMessage Body:\n{clean_text}"
            
            response = self.client.chat.completions.create(
                model=Config.INTERPRETATION_MODEL,
                messages=[
                    {"role": "system", "content": """Analyze the support email and return a JSON object with:
- intent: [QUERY, COMPLAINT, RESOLUTION, SCHEDULING, REQUEST, OTHER]
- urgency: [LOW, MEDIUM, HIGH, CRITICAL]
- summary: A 1-sentence summary of the core request.
- requires_human: boolean (true if it's sensitive, legal, or high-stakes).
- confidence_score: float (0.0 to 1.0).

Intents:
- QUERY: Asking for information or help.
- COMPLAINT: Expressing dissatisfaction or reporting a failure.
- RESOLUTION: Confirming a fix or saying thank you.
- SCHEDULING: Requesting a call, meeting, or demo.
- REQUEST: Action request (e.g., reset password, update account).
- OTHER: None of the above."""},
                    {"role": "user", "content": prompt}
                ],
                response_format={ "type": "json_object" },
                temperature=0.0
            )
            
            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            logger.error(f"Interpretation failed: {e}")
            return {
                "confidence_score": 0.0
            }
    
    def update_issue_state(self, messages: List[Dict[str, Any]], current_state: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Consolidate thread history into a persistent Issue State.
        
        Args:
            messages: Full thread messages
            current_state: Previous issue state (if any) to refine
            
        Returns:
            Dict: {problem_summary, key_entities, error_codes, technical_signals}
        """
        if not self.client:
            raise Exception("Not authenticated.")
            
        try:
            # Format thread for the state engine
            history = []
            for m in messages:
                role = "SUPPORT" if m.get('is_internal') else "CUSTOMER"
                history.append(f"{role}: {m.get('body_text', '')[:2000]}")
            
            thread_str = "\n".join(history)
            
            system_msg = """You are a Technical Support State Engine. 
Your task is to extract a high-signal 'Issue State' from a support thread.
This state is used for RAG retrieval, so focus on technical terms, error codes, and the core problem.

RULES:
1. Be extremely concise.
2. Preserve all error codes (e.g., 0x8004, 404, 'Access Denied').
3. Identify key entities (e.g., 'Outlook', 'Azure', 'Server B').
4. Summarize the 'Current Problem' in one clear technical sentence.

RETURN ONLY A JSON OBJECT:
{
  "problem_summary": "Technical description of the issue",
  "entities": ["list", "of", "software/systems"],
  "error_codes": ["list", "of", "errors"],
  "technical_signals": "Specific technical context for RAG (e.g., 'SMTP timeout during handshake')"
}"""

            user_msg = f"CONVERSATION HISTORY:\n{thread_str}\n\nPREVIOUS STATE:\n{json.dumps(current_state) if current_state else 'None'}"
            
            response = self.client.chat.completions.create(
                model=Config.INTERPRETATION_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ],
                response_format={ "type": "json_object" },
                temperature=0.0
            )
            
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Issue state update failed: {e}")
            return current_state or {
                "problem_summary": "Context unavailable",
                "entities": [],
                "error_codes": [],
                "technical_signals": ""
            }
    
    def _format_thread_context(self, messages: List[Dict[str, Any]], max_old_messages: int = 20) -> Tuple[str, str]:
        """
        Format thread messages into structured context.
        
        Strategy:
        - Latest message is the "question"
        - Previous messages are "thread history"
        - Older messages (beyond max_old_messages) are summarized
        
        Args:
            messages: List of message dicts from SQL (ordered chronologically)
            max_old_messages: Number of recent messages to include in full detail
            
        Returns:
            Tuple of (thread_history, latest_question)
        """
        if not messages:
            return "", ""
        
        # Latest message is the question
        latest = messages[-1]
        latest_question = f"""
From: {latest.get('sender', 'Unknown')}
Time: {latest.get('timestamp', 'Unknown')}
Message:
{latest.get('body_text', '')}
""".strip()
        
        # Previous messages are thread history
        previous_messages = list(messages[:-1])
        
        if not previous_messages:
            return "", latest_question
        
        # If thread is long, summarize older messages
        if len(previous_messages) > max_old_messages:
            older_messages = previous_messages[:-max_old_messages]
            recent_messages = previous_messages[-max_old_messages:]
            
            # Create summary of older messages
            older_summary = f"""
[EARLIER CONVERSATION - {len(older_messages)} messages]
First message from: {older_messages[0].get('sender', 'Unknown')}
Topic: {older_messages[0].get('subject', 'No subject')}
Approximate timespan: {older_messages[0].get('timestamp', '')} to {older_messages[-1].get('timestamp', '')}
"""
            
            # Format recent messages in full detail
            recent_formatted: List[str] = []
            for msg in recent_messages:
                speaker = "SUPPORT AGENT" if msg.get('is_internal') else "CUSTOMER"
                body_text = str(msg.get('body_text', ''))
                if len(body_text) > 50000:
                    body_text = body_text[0:50000] + "... [MESSAGE TRUNCATED FOR LENGTH]"
                recent_formatted.append(f"""
--- {speaker} ({msg.get('timestamp', 'Unknown')}) ---
{body_text}
""".strip())
            
            thread_history = older_summary + "\n\n" + "\n\n".join(recent_formatted)
        else:
            # Format all previous messages
            formatted: List[str] = []
            for msg in previous_messages:
                speaker = "SUPPORT AGENT" if msg.get('is_internal') else "CUSTOMER"
                body_text = str(msg.get('body_text', ''))
                if len(body_text) > 10000:
                    body_text = body_text[0:10000] + "... [MESSAGE TRUNCATED FOR LENGTH]"
                formatted.append(f"""
--- {speaker} ({msg.get('timestamp', 'Unknown')}) ---
{body_text}
""".strip())
            
            thread_history = "\n\n".join(formatted)
        
        return thread_history, latest_question
    
    def _format_rag_context(self, context_emails: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Format RAG context emails and extract provenance.
        
        Args:
            context_emails: List of similar emails from vector search
            
        Returns:
            Tuple of (formatted_context, provenance_list)
        """
        if not context_emails:
            return "No similar past support cases found in knowledge base.", []
        
        provenance = []
        formatted_parts = []
        
        for i, email in enumerate(list(context_emails[:10]), 1):  # Use top 10 for larger context
            content = email.get('content', '')
            metadata = email.get('metadata', {})
            distance = email.get('distance', 0.0)
            similarity_score = 1.0 - distance  # Convert distance to similarity
            
            # Extract subject from content or metadata
            subject = metadata.get('subject', 'N/A')
            
            # Store provenance
            provenance.append({
                'rank': i,
                'subject': subject,
                'similarity_score': round(float(similarity_score), 3),
                'metadata': metadata
            })
            
            # Format for context (truncate if too long - increased for GPT-5)
            content_preview = content[0:5000] + "..." if len(content) > 5000 else content
            
            formatted_parts.append(f"""
Past Support Case #{i} (Similarity: {similarity_score:.1%}):
Subject: {subject}
{content_preview}
""".strip())
        
        formatted_context = "\n\n---\n\n".join(formatted_parts)
        
        return formatted_context, provenance
    
    def generate_response(
        self,
        messages: List[Dict[str, Any]],
        context_emails: Optional[List[Dict[str, Any]]] = None,
        documentation_context: Optional[List[Dict[str, Any]]] = None,
        experience_context: Optional[List[Dict[str, Any]]] = None,
        user_instructions: Optional[str] = None,
        current_draft: Optional[str] = None,
        customer_email: str = "", 
        intent: str = "OTHER",
        summary: str = "",
        display_id: str = None
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        """
        Generate AI response using GPT-5-mini with enhanced reasoning and sendability gate.
        
        ENHANCEMENTS (v2):
        - Separate documentation vs experience contexts
        - Internal reasoning framework
        - Sendability gate (safe to send or needs clarification)
        - Documentation overrides experience in conflicts
        - Instruction-based refinement of existing drafts
        
        BACKWARD COMPATIBILITY:
        - If called with old signature (context_emails), works as before
        - If called with new signature (documentation_context, experience_context), uses enhanced logic
        
        Args:
            messages: Full thread messages (ordered chronologically)
            context_emails: [LEGACY] Similar past emails from RAG
            documentation_context: [NEW] BookStack docs from RAG (authority: hard_fact)
            experience_context: [NEW] Past support emails from RAG
            user_instructions: [REFINEMENT] Specific instructions from the user for this generation
            current_draft: [REFINEMENT] The current draft to be refined/improved
            customer_email: Customer's email address
            subject: Email subject
            
        Returns:
            Tuple of (ai_response_html, combined_provenance_list)
            - ai_response_html: Generated response (or None on error)
            - combined_provenance_list: List of dicts with vector search provenance
        """
        if not self.client:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        if not messages:
            logger.error("❌ Cannot generate response: no messages provided")
            return None, None
        
        # Handle backward compatibility
        use_enhanced_mode = (documentation_context is not None or experience_context is not None)
        
        if not use_enhanced_mode and context_emails is not None:
            # Legacy mode: treat context_emails as combined context
            logger.debug("Using legacy API signature (context_emails)")
            documentation_context = []
            experience_context = context_emails or []
        else:
            # Enhanced mode: use separate contexts
            documentation_context = documentation_context or []
            experience_context = experience_context or []
        
        try:
            # Resolve log ID: display_id > messages[0]['subject']
            log_id = display_id or (messages[0].get('subject', 'unknown') if messages else 'unknown')
            
            logger.info(f"🧠 Generating AI response for ticket {log_id}...")
            
            # 1. Format thread into question + history
            thread_history, latest_question = self._format_thread_context(messages)
            
            # 2. Format documentation context and extract provenance
            doc_context, doc_provenance = self._format_documentation_context(documentation_context)
            
            # 3. Format experience context and extract provenance
            exp_context, exp_provenance = self._format_experience_context(experience_context)
            
            # 4. Combine provenance
            combined_provenance = doc_provenance + exp_provenance
            
            logger.info(
                f"📊 RAG context for {log_id}: "
                f"{len(documentation_context)} docs, "
                f"{len(experience_context)} past cases"
            )
            
            intent_instructions = {
                "QUERY": "Your goal is to provide accurate, documentation-backed information. Be direct and helpful.",
                "COMPLAINT": "The customer is frustrated. Be extremely empathetic, acknowledge the issue, and provide a clear path forward or workaround.",
                "RESOLUTION": "The customer is confirming a fix. Keep it brief, express gratitude, and confirm the ticket can be monitored for further issues.",
                "SCHEDULING": "The customer wants to meet. Provide clear instructions on how to book a slot or confirm the requested time.",
                "REQUEST": "The customer is requesting an action. Acknowledge the request, set expectations on processing time, and confirm next steps.",
                "OTHER": "Provide a standard professional support response."
            }
            
            current_intent_instruction = intent_instructions.get(intent, intent_instructions["OTHER"])

            system_prompt = f"""You are a professional technical support agent for Greenware Solutions.
CLASSIFIED INTENT: {intent}
{current_intent_instruction}

CRITICAL OPERATING PRINCIPLES:

1. INTERNAL REASONING (DO NOT SHOW TO USER):
   Reason through classification, documentation & conversational coverage, conflict resolution, and assumption safety.
   
2. RESPONSE RULES:
   - Follow documentation EXACTLY when available and aplicable.
   - Use past experience ONLY if it does not contradict documentation.
   - NEVER invent product behavior.
   - If unsure: ask ONE specific clarifying question.
   - Use HTML formatting (<p>, <br>, <strong>, <ul>, <li>).
   - DO NOT include subject line or 'Dear Customer'.

SENDABILITY GATE:
- If you can provide a complete, accurate response: DO IT.
- If you need clarification: Ask ONE specific question.
- If issue requires human expertise: Recommend escalation.

NOTE ON EXPERIENCE:
Past Support Cases labeled [RESOLVED] are verified high-quality resolutions. Regard them as authoritative examples of how to handle similar issues.
"""

            # 5. Build user prompt
            user_prompt_parts = []
            
            if thread_history:
                user_prompt_parts.append(f"PREVIOUS CONVERSATION THREAD:\n{thread_history}")
            
            user_prompt_parts.append(f"CURRENT CUSTOMER QUESTION:\n{latest_question}")
            
            if summary:
                user_prompt_parts.append(f"INTERPRETED SUMMARY: {summary}")
            
            if current_draft:
                user_prompt_parts.append(f"DRAFT TO REFINE:\n{current_draft}")
            
            if user_instructions:
                user_prompt_parts.append(f"SPECIFIC USER INSTRUCTIONS:\n{user_instructions}")
            
            user_prompt_parts.append("═══════════════════════════════════════════════════════════════")
            user_prompt_parts.append("SECTION 1: AUTHORITATIVE DOCUMENTATION")
            user_prompt_parts.append("═══════════════════════════════════════════════════════════════")
            user_prompt_parts.append(doc_context)
            
            user_prompt_parts.append("═══════════════════════════════════════════════════════════════")
            user_prompt_parts.append("SECTION 2: PAST SUPPORT EXPERIENCE")
            user_prompt_parts.append("═══════════════════════════════════════════════════════════════")
            user_prompt_parts.append(exp_context)
            
            user_prompt_parts.append("═══════════════════════════════════════════════════════════════")
            user_prompt_parts.append("YOUR TASK:")
            user_prompt_parts.append("═══════════════════════════════════════════════════════════════")
            
            if user_instructions:
                user_prompt_parts.append(f"1. Execute the USER INSTRUCTIONS faithfully: '{user_instructions}'")
            else:
                user_prompt_parts.append(f"1. Draft a professional email response addressing the {intent}.")
            
            user_prompt_parts.append("2. Ensure your response is to the point and direct and dont use hyphons (-).")
            
            user_prompt = "\n\n".join(user_prompt_parts)

            # 6. Call OpenAI API
            logger.info(f"📤 Calling {Config.GENERATIONAL_MODEL} (Context Window: {CONTEXT_WINDOW}) for ticket {log_id}...")
            if self.client is None:
                raise Exception("Client is None")
            response = self.client.chat.completions.create(
                model=Config.GENERATIONAL_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=1,
                max_completion_tokens=MAX_COMPLETION_TOKENS
            )
            
            ai_response = response.choices[0].message.content
            
            # 7. Log token usage
            if hasattr(response, 'usage'):
                logger.info(
                    f"📈 Token usage for {log_id} - "
                    f"Prompt: {response.usage.prompt_tokens}, "
                    f"Completion: {response.usage.completion_tokens}, "
                    f"Total: {response.usage.total_tokens}"
                )
            
            logger.info(f"✅ AI response generated for ticket {log_id}")
            
            return ai_response, combined_provenance
            
        except Exception as e:
            logger.error(f"❌ Error generating AI response for ticket {log_id}: {e}")
            return None, None
    
    def summarize_ticket(self, customer_question: str) -> Optional[str]:
        """
        Generate a brief summary of a support ticket
        Args:
            customer_question: The customer's question/issue
            
        Returns:
            Brief summary (1-2 sentences)
        """
        if not self.client:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            if self.client is None:
                raise Exception("Client is None")
            response = self.client.chat.completions.create(
                model=Config.INTERPRETATION_MODEL,
                messages=[
                    {"role": "system", "content": "Summarize the following support ticket in 1-2 concise sentences."},
                    {"role": "user", "content": customer_question}
                ],
                temperature=0.5,
                max_tokens=100
            )
            
            summary = response.choices[0].message.content
            return summary
            
        except Exception as e:
            logger.error(f"❌ Error summarizing ticket: {e}")
            return None
    
    def categorize_ticket(self, customer_question: str, subject: str = "") -> str:
        """
        Categorize a support ticket using gpt-4.1-nano-2025-04-14.
        
        Args:
            customer_question: The customer's question/issue
            subject: Email subject
            
        Returns:
            Category (e.g., "Technical Issue", "Billing", "Notification", "Assistance Required" etc.)
        """
        if not self.client:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            prompt = f"Subject: {subject}\n\nQuestion: {customer_question}"
            
            if self.client is None:
                raise Exception("Client is None")
            response = self.client.chat.completions.create(
                model=Config.INTERPRETATION_MODEL,
                messages=[
                    {"role": "system", "content": """Categorize this support ticket into ONE of these categories:
- Technical Issue
- Billing/Payment
- Feature Request
- Account Management
- Installation/Setup
- Bug Report
- General Inquiry
- Update
- Other

Respond with ONLY the category name, nothing else."""},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=20
            )
            
            category = response.choices[0].message.content.strip()
            return category
            
        except Exception as e:
            logger.error(f"❌ Error categorizing ticket: {e}")
            return "Other"
        
    def _format_documentation_context(self, docs: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Format authoritative documentation (BookStack) with provenance.
        
        Args:
            docs: List of documentation chunks from vector search
            
        Returns:
            Tuple of (formatted_context, provenance_list)
        """
        if not docs:
            return "No documentation found in knowledge base.", []
        
        provenance = []
        formatted_parts = []
        
        for i, doc in enumerate(list(docs[:10]), 1):  # Use top 10 for larger context
            content = doc.get('content', '')
            metadata = doc.get('metadata', {})
            distance = doc.get('distance', 0.0)
            similarity_score = 1.0 - distance
            
            # Extract metadata
            book = metadata.get('book', 'Unknown')
            section = metadata.get('section', 'N/A')
            
            # Store provenance
            provenance.append({
                'rank': i,
                'source': 'documentation',
                'book': book,
                'section': section,
                'similarity_score': round(float(similarity_score), 3),
                'metadata': metadata
            })
            
            # Format for context (increased for GPT-5)
            content_preview = content[0:10000] + "..." if len(content) > 10000 else content
            
            formatted_parts.append(f"""
Documentation #{i} (Similarity: {similarity_score:.1%}):
Source: {book} - {section}
{content_preview}
""".strip())
        
        formatted_context = "\n\n---\n\n".join(formatted_parts)
        
        return formatted_context, provenance
    
    def _format_experience_context(self, emails: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Format past support experience emails with provenance.
        
        Args:
            emails: List of similar emails from vector search
            
        Returns:
            Tuple of (formatted_context, provenance_list)
        """
        if not emails:
            return "No similar past support cases found.", []
        
        provenance = []
        formatted_parts = []
        
        for i, email in enumerate(list(emails[:10]), 1):  # Use top 10 for larger context
            content = email.get('content', '')
            metadata = email.get('metadata', {})
            distance = email.get('distance', 0.0)
            similarity_score = 1.0 - distance
            
            # Extract metadata
            subject = metadata.get('subject', 'N/A')
            is_resolved = metadata.get('is_resolved', False)
            status_label = "[RESOLVED]" if is_resolved else "[ONGOING]"
            
            # Store provenance
            provenance.append({
                'rank': i,
                'source': 'experience',
                'subject': subject,
                'is_resolved': is_resolved,
                'similarity_score': round(float(similarity_score), 3),
                'metadata': metadata
            })
            
            # Format for context (truncate if too long - increased for GPT-5)
            content_preview = content[0:5000] + "..." if len(content) > 5000 else content
            
            formatted_parts.append(f"""
Past Support Case #{i} {status_label} (Similarity: {similarity_score:.1%}):
Subject: {subject}
{content_preview}
""".strip())
        
        formatted_context = "\n\n---\n\n".join(formatted_parts)
        
        return formatted_context, provenance
    def analyze_image(self, image_bytes: bytes, model: Optional[str] = None) -> Optional[str]:
        """
        Analyze an image using OpenAI Vision.
        
        Args:
            image_bytes: Raw image data
            model: The model to use (defaults to Config.ANALYSIS_MODEL)
            
        Returns:
            str: Analysis result or None on error
        """
        if not self.client:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        # Use provided model or fall back to config
        target_model = model if model else Config.ANALYSIS_MODEL
        
        try:
            # Encode image to base64
            import base64
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            # Determine prompt based on model
            if target_model == Config.ANALYSIS_MODEL:
                prompt = "Extract all tabular data from this image and format it as a markdown table. Be extremely precise with numbers and headers."
            else:
                prompt = "What is in this image? If it's a technical error or screenshot, extract the error message and key details. If it's a general image, provide a brief description."
            
            if self.client is None:
                raise Exception("Client is None")
            response = self.client.chat.completions.create(
                model=target_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=2000,
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"❌ Error analyzing image with {target_model}: {e}")
            return None

    def parse_pasted_holidays(self, text: str) -> List[Dict[str, str]]:
        """
        Parse manually pasted holidays list into structured JSON.
        """
        if not self.client:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        system_msg = """You are a parser assistant.
Your task is to parse a manually pasted text listing public/trading holidays into a structured JSON array.
Each holiday object in the array must contain:
- holiday: The name of the holiday (e.g. "Republic Day")
- date: The date formatted as "YYYY-MM-DD"
- day: The day of the week (e.g. "Monday")

Verify and convert the dates carefully. E.g., short year date format "15-Jan-26" should be resolved to "2026-01-15".
Always output a valid JSON array of objects, containing ONLY the JSON array. Do not include markdown blocks, backticks (like ```json), or any surrounding text.
"""
        try:
            response = self.client.chat.completions.create(
                model=Config.INTERPRETATION_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": text}
                ],
                temperature=0.0
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\n", "", content)
                content = re.sub(r"\n```$", "", content)
            
            holidays_list = json.loads(content.strip())
            if not isinstance(holidays_list, list):
                raise ValueError("Expected a list of holidays")
            
            validated_list = []
            for item in holidays_list:
                holiday_name = str(item.get('holiday', '')).strip()
                date_str = str(item.get('date', '')).strip()
                day_str = str(item.get('day', '')).strip()
                if not holiday_name or not date_str or not day_str:
                    continue
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    continue
                validated_list.append({
                    'holiday': holiday_name,
                    'date': date_str,
                    'day': day_str
                })
            return validated_list
        except Exception as e:
            logger.error(f"❌ Error parsing pasted holidays: {e}")
            raise