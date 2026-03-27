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
from openai import OpenAI
from typing import List, Dict, Optional, Tuple, Any, cast

# Model Configuration for GPT-5-mini
GENERATION_MODEL = "gpt-5-mini"
CONTEXT_WINDOW = 400000  # Updated from 128,000 to 400,000 tokens
MAX_COMPLETION_TOKENS = 16384

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OpenAIAgent:
    def __init__(self, api_key: str):
        """Initialize OpenAI client"""
        self.api_key = api_key
        self.client: Optional[OpenAI] = None
        
    def authenticate(self):
        """Authenticate with OpenAI API"""
        try:
            self.client = OpenAI(api_key=self.api_key)
            logger.info("✅ Successfully authenticated with OpenAI")
            return True
        except Exception as e:
            logger.error(f"❌ OpenAI authentication failed: {e}")
            return False
    
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
                body = str(msg.get('body_text', ''))
                if len(body) > 50000:
                body = body[:50000] + "... [MESSAGE TRUNCATED FOR LENGTH]"
                recent_formatted.append(f"""
--- {speaker} ({msg.get('timestamp', 'Unknown')}) ---
{body}
""".strip())
            
            thread_history = older_summary + "\n\n" + "\n\n".join(recent_formatted)
        else:
            # Format all previous messages
            formatted: List[str] = []
            for msg in previous_messages:
                speaker = "SUPPORT AGENT" if msg.get('is_internal') else "CUSTOMER"
                body = str(msg.get('body_text', ''))
                if len(body) > 10000:
                    body = body[:10000] + "... [MESSAGE TRUNCATED FOR LENGTH]"
                formatted.append(f"""
--- {speaker} ({msg.get('timestamp', 'Unknown')}) ---
{body}
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
            content_preview = content[:5000] + "..." if len(content) > 5000 else content
            
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
        context_emails: Optional[List[Dict[str, Any]]] = None,          # LEGACY parameter
        documentation_context: Optional[List[Dict[str, Any]]] = None,    # NEW parameter
        experience_context: Optional[List[Dict[str, Any]]] = None,       # NEW parameter
        customer_email: str = "", 
        subject: str = ""
    ) -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
        """
        Generate AI response using GPT-5-mini with enhanced reasoning and sendability gate.
        
        ENHANCEMENTS (v2):
        - Separate documentation vs experience contexts
        - Internal reasoning framework
        - Sendability gate (safe to send or needs clarification)
        - Documentation overrides experience in conflicts
        
        BACKWARD COMPATIBILITY:
        - If called with old signature (context_emails), works as before
        - If called with new signature (documentation_context, experience_context), uses enhanced logic
        
        Args:
            messages: Full thread messages (ordered chronologically)
            context_emails: [LEGACY] Similar past emails from RAG
            documentation_context: [NEW] BookStack docs from RAG (authority: hard_fact)
            experience_context: [NEW] Past support emails from RAG
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
            # Extract ticket_id for logging
            ticket_id = messages[0].get('ticket_id', 'unknown') if messages else 'unknown'
            
            logger.info(f"🧠 Generating AI response for ticket {ticket_id}...")
            
            # 1. Format thread into question + history
            thread_history, latest_question = self._format_thread_context(messages)
            
            # 2. Format documentation context and extract provenance
            doc_context, doc_provenance = self._format_documentation_context(documentation_context)
            
            # 3. Format experience context and extract provenance
            exp_context, exp_provenance = self._format_experience_context(experience_context)
            
            # Combine provenance
            combined_provenance = doc_provenance + exp_provenance
            
            logger.info(
                f"📊 RAG context for {ticket_id}: "
                f"{len(documentation_context)} docs, "
                f"{len(experience_context)} past cases"
            )
            
            # 4. Build system prompt with reasoning framework
            if use_enhanced_mode and documentation_context:
                # Enhanced mode with documentation
                system_prompt = """You are a professional technical support agent for Greenware Solutions.

CRITICAL OPERATING PRINCIPLES:

1. INTERNAL REASONING (DO NOT SHOW TO USER):
   Before writing your response, you must internally reason through:
   
   a) Problem Classification:
      - What type of issue is this? (technical, account, billing, feature request, etc.)
      - What is the customer actually asking for?
      - Are there implicit questions or concerns?
   
   b) Documentation Coverage Check:
      - Is this issue covered in our AUTHORITATIVE DOCUMENTATION?
      - If yes, what does the documentation say EXACTLY?
      - Are there any gaps in the documentation for this specific case?
   
   c) Conflict Resolution:
      - Does past experience contradict or conflict with documentation?
      - RULE: If documentation exists, it OVERRIDES all past experience 
      - RULE: Past experience is only valid when it does NOT contradict documentation
   
   d) Assumption Safety Check:
      - Am I about to make any assumptions not grounded in documentation or past experience?
      - Am I inventing product behavior or features not mentioned in provided context?
      - Would a senior engineer flag this response as speculative?
   
   e) Sendability Decision:
      - Can I provide a complete, accurate, safe-to-send response?
      - OR do I need to ask ONE clarifying question?
      - OR do I need to escalate to a human agent?

2. RESPONSE RULES:
   - Follow documentation EXACTLY when available (do not paraphrase or interpret loosely)
   - Use past experience ONLY if it does not contradict documentation
   - NEVER invent product behavior not mentioned in provided context
   - If unsure: ask ONE specific clarifying question OR recommend escalation
   - Do NOT hedge excessively ("might", "could", "possibly") if you have clear documentation
   - Do NOT apologize unless there was an actual service failure

3. CONTEXT NOTES:
   - Some context includes "Attachment Descriptions" - automated text descriptions 
     of customer screenshots. Use these to identify error codes, UI states, or 
     misconfigurations visible in images that customers didn't mention in text.
   - If you see an error code in an attachment description, treat it as a primary diagnostic clue.

4. RESPONSE FORMATTING:
   - Use HTML formatting for email (use <p>, <br>, <strong>, <ul>, <li>, <ol> tags)
   - Structure your response clearly with paragraphs
   - End with a professional closing
   - DO NOT include subject line or "Dear Customer" - start directly with the greeting

EXAMPLE FORMAT:
<p>Hello,</p>
<p>Thank you for reaching out to Greenware Solutions support.</p>
<p>[Your detailed response here]</p>
<p>If you have any further questions, please don't hesitate to reach out.</p>
<p>Best regards,</p>
<p>Greenware Solutions Support Team</p>

SENDABILITY GATE:
- If you can provide a complete, accurate response: DO IT
- If you need clarification: Ask ONE specific question, then provide best-effort guidance
- If issue requires human expertise: State this clearly and recommend next steps
- NEVER send a response you're not confident in"""
            else:
                # Legacy mode or no documentation available
                system_prompt = """You are a professional technical support agent for Greenware Solutions.

IMPORTANT NOTES:
- Some RAG context includes "Attachment Descriptions" - these are automated text descriptions 
  of screenshots provided by customers. Use this to identify error codes, UI states, or 
  misconfigurations visible in images that customers didn't mention in text.
- If you see an error code in an attachment description, treat it as a primary diagnostic clue.
- Use information from past similar support cases when relevant, but always tailor your 
  response to the current customer's specific situation.

Your responsibilities:
1. Provide clear, accurate, and helpful responses to customer questions
2. Leverage past support cases to inform your response (when relevant)
3. Provide step-by-step instructions when needed
4. If unsure, acknowledge it and suggest next steps or escalation
5. Maintain a professional, empathetic tone

Response formatting:
- Use HTML formatting for email (use <p>, <br>, <strong>, <ul>, <li>, <ol> tags)
- Structure your response clearly with paragraphs
- End with a professional closing
- DO NOT include subject line or "Dear Customer" - start directly with the greeting

Example format:
<p>Hello, </p>
<p>Thank you for reaching out to Greenware Solutions support.</p>
<p>[Your detailed response here]</p>
<p>If you have any further questions, please don't hesitate to reach out.</p>
<p>Best regards,</p>
<p>Greenware Solutions Support Team</p>"""

            # 5. Build user prompt with clearly separated contexts
            if use_enhanced_mode and documentation_context:
                # Enhanced mode with documentation
                if thread_history:
                    user_prompt = f"""PREVIOUS CONVERSATION THREAD:
{thread_history}

CURRENT CUSTOMER QUESTION:
{latest_question}

═══════════════════════════════════════════════════════════════
SECTION 1: AUTHORITATIVE DOCUMENTATION (Follow EXACTLY)
═══════════════════════════════════════════════════════════════
{doc_context}

═══════════════════════════════════════════════════════════════
SECTION 2: PAST SUPPORT EXPERIENCE (Use ONLY if not contradicting documentation)
═══════════════════════════════════════════════════════════════
{exp_context}

═══════════════════════════════════════════════════════════════
YOUR TASK:
═══════════════════════════════════════════════════════════════
1. Apply the Internal Reasoning framework (do not show this to the customer)
2. Draft a professional email response following the Response Rules
3. Ensure your response passes the Sendability Gate
4. Make sure your response is to the point and direct, not wordy

Remember: Documentation is authoritative. Past experience is advisory only."""
                else:
                    # First message in thread
                    user_prompt = f"""CUSTOMER'S QUESTION:
{latest_question}

═══════════════════════════════════════════════════════════════
SECTION 1: AUTHORITATIVE DOCUMENTATION (Follow EXACTLY)
═══════════════════════════════════════════════════════════════
{doc_context}

═══════════════════════════════════════════════════════════════
SECTION 2: PAST SUPPORT EXPERIENCE (Use ONLY if not contradicting documentation)
═══════════════════════════════════════════════════════════════
{exp_context}

═══════════════════════════════════════════════════════════════
YOUR TASK:
═══════════════════════════════════════════════════════════════
1. Apply the Internal Reasoning framework (do not show this to the customer)
2. Draft a professional email response following the Response Rules
3. Ensure your response passes the Sendability Gate
4. Make sure your response is to the point and direct, not wordy

Remember: Documentation is authoritative. Past experience is advisory only."""
            else:
                # Legacy mode
                if thread_history:
                    user_prompt = f"""PREVIOUS CONVERSATION THREAD:
{thread_history}

CURRENT CUSTOMER QUESTION:
{latest_question}

SIMILAR PAST SUPPORT CASES (for reference):
{exp_context}

Please draft a professional email response that addresses the customer's current question, 
taking into account the conversation history and insights from similar past cases."""
                else:
                    # First message in thread
                    user_prompt = f"""CUSTOMER'S QUESTION:
{latest_question}

SIMILAR PAST SUPPORT CASES (for reference):
{exp_context}

Please draft a professional email response that addresses the customer's question. Make sure it's not wordy and it's to the point."""

            # 6. Call OpenAI API
            logger.info(f"📤 Calling {GENERATION_MODEL} (Context Window: {CONTEXT_WINDOW}) for ticket {ticket_id}...")
            if self.client is None:
                raise Exception("Client is None")
            response = self.client.chat.completions.create(
                model=GENERATION_MODEL,
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
                    f"📈 Token usage for {ticket_id} - "
                    f"Prompt: {response.usage.prompt_tokens}, "
                    f"Completion: {response.usage.completion_tokens}, "
                    f"Total: {response.usage.total_tokens}"
                )
            
            logger.info(f"✅ AI response generated for ticket {ticket_id}")
            
            return ai_response, combined_provenance
            
        except Exception as e:
            logger.error(f"❌ Error generating AI response for ticket {ticket_id}: {e}")
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
                model="gpt-4o-mini",
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
        Categorize a support ticket using gpt-4o-mini.
        
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
                model="gpt-4o-mini",
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
            content_preview = content[:10000] + "..." if len(content) > 10000 else content
            
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
            
            # Extract subject from content or metadata
            subject = metadata.get('subject', 'N/A')
            
            # Store provenance
            provenance.append({
                'rank': i,
                'source': 'experience',
                'subject': subject,
                'similarity_score': round(float(similarity_score), 3),
                'metadata': metadata
            })
            
            # Format for context (truncate if too long - increased for GPT-5)
            content_preview = content[:5000] + "..." if len(content) > 5000 else content
            
            formatted_parts.append(f"""
Past Support Case #{i} (Similarity: {similarity_score:.1%}):
Subject: {subject}
{content_preview}
""".strip())
        
        formatted_context = "\n\n---\n\n".join(formatted_parts)
        
        return formatted_context, provenance
    def analyze_image(self, image_bytes: bytes, model: str = "gpt-4o-mini") -> Optional[str]:
        """
        Analyze an image using OpenAI Vision.
        
        Args:
            image_bytes: Raw image data
            model: The model to use ('gpt-4o' or 'gpt-4o-mini')
            
        Returns:
            str: Analysis result or None on error
        """
        if not self.client:
            raise Exception("Not authenticated. Call authenticate() first.")
        
        try:
            # Encode image to base64
            import base64
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            # Determine prompt based on model
            if model == "gpt-4o":
                prompt = "Extract all tabular data from this image and format it as a markdown table. Be extremely precise with numbers and headers."
            else:
                prompt = "What is in this image? If it's a technical error or screenshot, extract the error message and key details. If it's a general image, provide a brief description."
            
            if self.client is None:
                raise Exception("Client is None")
            response = self.client.chat.completions.create(
                model=model,
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
            logger.error(f"❌ Error analyzing image with {model}: {e}")
            return None
