# Support Automation --- Operations Guide

------------------------------------------------------------------------

# System Workflow

1.  Email received in support inbox
2.  Pulled via Microsoft Graph
3.  Converted into ticket + message
4.  PII redacted
5.  Attachments analyzed
6.  Context embedded into vector DB
7.  RAG retrieves documentation + past cases
8.  AI draft generated
9.  Staff reviews and sends via dashboard

------------------------------------------------------------------------

# Authentication Model

-   Custom username/password
-   Session-based authentication
-   Roles: admin, user
-   Default admin: admin / admin123

------------------------------------------------------------------------

# Ticket Lifecycle

New Ticket → Created when no thread mapping exists. Existing Ticket →
Message appended to existing ticket. Status → Open / Closed.

------------------------------------------------------------------------

# Cleanup Strategy

Stop services before running cleanup.

Auto Mode: python ticket_cleanup.py --auto

Standard Mode: python ticket_cleanup.py --standard YYYY-MM-DD

Hard Clean: python ticket_cleanup.py --hard-clean YYYY-MM-DD

------------------------------------------------------------------------

# Maintenance Recommendations

-   Nightly PostgreSQL backups
-   Periodic vector DB cleanup
-   Secure environment variables
-   Monitor logs
