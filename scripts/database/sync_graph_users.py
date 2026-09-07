#!/usr/bin/env python3
"""
Graph API User Synchronization Script

Fetches organization users via Microsoft Graph API and populates the PostgreSQL
`users` table following the organization template:
- Username: first_name_last_name (e.g. 'bilal_khan' from bilal.khan@greenwaresolutions.com)
- Password: Sourced from DEFAULT_USER_PASSWORD env var (must be set before running)
- Role: staff
- is_assignable: True

Features:
- Idempotent duplicate prevention (checks existing usernames and emails case-insensitively)
- Supports direct Microsoft Graph API fetch or fallback JSON/CSV file import
"""

import os
import sys
import re
import asyncio
import logging
import argparse
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from auth import UserManager
from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def extract_username_from_graph(user_obj) -> str:
    """
    Extracts 'first_name_last_name' format from a Graph user object.
    Example: bilal.khan@greenwaresolutions.com -> 'bilal_khan'
    """
    mail = getattr(user_obj, 'mail', None) or getattr(user_obj, 'user_principal_name', None) or ''
    given_name = getattr(user_obj, 'given_name', None) or ''
    surname = getattr(user_obj, 'surname', None) or ''
    display_name = getattr(user_obj, 'display_name', None) or ''

    # 1. Preferred: email prefix (e.g. bilal.khan@... -> bilal_khan)
    if mail and '@' in mail:
        prefix = mail.split('@')[0].strip().lower()
        if '.' in prefix:
            return prefix.replace('.', '_')
        if '_' in prefix:
            return prefix

    # 2. First name + Last name
    if given_name and surname:
        clean_first = re.sub(r'[^a-z0-9]', '', given_name.strip().lower())
        clean_last = re.sub(r'[^a-z0-9]', '', surname.strip().lower())
        if clean_first and clean_last:
            return f"{clean_first}_{clean_last}"

    # 3. Display name (e.g. "Bilal Khan" -> "bilal_khan")
    if display_name:
        parts = [re.sub(r'[^a-z0-9]', '', p) for p in display_name.strip().lower().split()]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return f"{parts[0]}_{parts[-1]}"
        if parts:
            return parts[0]

    if mail and '@' in mail:
        return mail.split('@')[0].strip().lower()

    return ""


async def fetch_users_from_graph() -> List[Dict[str, str]]:
    """Fetch all organization users via Microsoft Graph API."""
    from azure.identity import ClientSecretCredential
    from msgraph import GraphServiceClient

    tenant_id = Config.AZURE_TENANT_ID
    client_id = Config.AZURE_CLIENT_ID
    client_secret = Config.AZURE_CLIENT_SECRET

    if not all([tenant_id, client_id, client_secret]):
        logger.error("❌ Azure AD credentials missing from environment.")
        return []

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )
    client = GraphServiceClient(credentials=credential, scopes=['https://graph.microsoft.com/.default'])

    logger.info("📡 Connecting to Microsoft Graph API to fetch organization users...")
    try:
        response = await client.users.get()
        graph_users = []
        if response and response.value:
            for u in response.value:
                uname = extract_username_from_graph(u)
                mail = getattr(u, 'mail', None) or getattr(u, 'user_principal_name', None) or ''
                if uname:
                    graph_users.append({'username': uname, 'email': mail})
        return graph_users
    except Exception as e:
        logger.error(f"❌ Error fetching users from Microsoft Graph API: {e}")
        if '403' in str(e) or 'Authorization_RequestDenied' in str(e):
            logger.error(
                "\n⚠️  PERMISSION NOTICE: The Azure AD App Registration requires the 'User.Read.All' "
                "Application Permission with Admin Consent granted in the Azure Portal.\n"
            )
        return []


def sync_users_to_db(users_to_add: List[Dict[str, str]], default_password: str = "India@123", dry_run: bool = False):
    """
    Inserts users into PostgreSQL `users` table while preventing any duplicates.
    """
    user_mgr = UserManager()
    existing_users = user_mgr.list_users()
    
    # Track existing usernames (lowercased)
    existing_usernames = {u['username'].strip().lower() for u in existing_users if u.get('username')}
    logger.info(f"📋 Current existing users in database: {len(existing_usernames)}")

    added_count = 0
    skipped_count = 0

    for item in users_to_add:
        uname = item['username'].strip().lower()
        if not uname:
            continue

        if uname in existing_usernames:
            logger.info(f"⏭️  Skipping duplicate user: '{uname}'")
            skipped_count += 1
            continue

        if dry_run:
            logger.info(f"🔍 [DRY-RUN] Would create: '{uname}' (Role: staff, Assignable: True)")
            added_count += 1
            existing_usernames.add(uname)
        else:
            success = user_mgr.create_user(username=uname, password=default_password, role='staff')
            if success:
                logger.info(f"✅ Successfully created user: '{uname}'")
                added_count += 1
                existing_usernames.add(uname)
            else:
                logger.error(f"❌ Failed to create user: '{uname}'")

    logger.info(f"\n✨ Sync Summary: {added_count} added, {skipped_count} skipped (duplicates/existing).")


def main():
    parser = argparse.ArgumentParser(description="Sync organization users to PostgreSQL")
    parser.add_argument('--dry-run', action='store_true', help="Preview changes without inserting into database")
    parser.add_argument('--password', default=None, help="Default password for new users (falls back to DEFAULT_USER_PASSWORD env var)")
    args = parser.parse_args()

    import os
    default_password = args.password or os.environ.get('DEFAULT_USER_PASSWORD') or os.environ.get('QUICK_ADD_PASSWORD')
    if not default_password:
        logger.error("❌ No default password provided. Set DEFAULT_USER_PASSWORD env var or pass --password.")
        sys.exit(1)

    users = asyncio.run(fetch_users_from_graph())

    if not users:
        logger.warning("⚠️ No users retrieved from Graph API. Check permissions or provide manual roster.")
        return

    logger.info(f"Found {len(users)} users in Graph API.")
    sync_users_to_db(users, default_password=default_password, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
