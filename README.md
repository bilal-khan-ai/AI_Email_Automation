# Support Automation System

An end-to-end AI-powered support automation platform that:

-   Ingests emails from Microsoft Graph
-   Converts conversations into structured tickets
-   Redacts PII before storage
-   Extracts context from attachments (OCR + image captioning)
-   Performs RAG over documentation and past experiences
-   Generates AI draft responses
-   Provides a multi-user real-time dashboard
-   Supports automated cleanup

------------------------------------------------------------------------

# Features

-   Microsoft Graph email ingestion
-   PostgreSQL ticket storage
-   PII redaction (Presidio)
-   Attachment processing (BLIP + OCR)
-   Vector search (ChromaDB)
-   AI draft generation (OpenAI)
-   Multi-user dashboard with ticket locking
-   Admin user management
-   Cleanup utility for old tickets

------------------------------------------------------------------------

# Running the System

## Start Email Ingestion

python main.py

## Start Dashboard

python dashboard_socketio.py

Default login: admin / admin123

------------------------------------------------------------------------

# Cleanup

Auto cleanup: python ticket_cleanup.py --auto

Standard cleanup: python ticket_cleanup.py --standard YYYY-MM-DD

Hard cleanup (danger): python ticket_cleanup.py --hard-clean YYYY-MM-DD
