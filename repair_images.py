import asyncio
import json
import re
import base64
import psycopg2
from datetime import datetime
from modules.graph_connector import GraphConnector, RetryConfig
from modules.sql_logger import SQLLogger
from config import Config

async def repair_messages():
    print("Starting Inline Image Repair Script...")
    
    # Initialize components
    sql = SQLLogger()
    from azure.identity import ClientSecretCredential
    from msgraph import GraphServiceClient

    credential = ClientSecretCredential(
        tenant_id=Config.AZURE_TENANT_ID,
        client_id=Config.AZURE_CLIENT_ID,
        client_secret=Config.AZURE_CLIENT_SECRET
    )
    scopes = ['https://graph.microsoft.com/.default']
    client = GraphServiceClient(credentials=credential, scopes=scopes)
        
    user_email = Config.USER_EMAIL
    
    # Find messages with cid: but no attachments
    conn = psycopg2.connect(
        host=getattr(Config, 'POSTGRES_HOST', 'localhost'),
        port=getattr(Config, 'POSTGRES_PORT', '5432'),
        dbname=getattr(Config, 'POSTGRES_DB', 'postgres'),
        user=getattr(Config, 'POSTGRES_USER', 'postgres'),
        password=getattr(Config, 'POSTGRES_PASSWORD', 'bilal')
    )
    cur = conn.cursor()
    
    cur.execute("""
        SELECT message_id, body_html, ticket_id 
        FROM ticket_messages 
        WHERE attachments = '[]' AND body_html LIKE '%cid:%'
        ORDER BY timestamp DESC
    """)
    rows = cur.fetchall()
    print(f"Found {len(rows)} messages needing repair.")
    
    for msg_id, body_html, ticket_id in rows:
        print(f"Repairing message {msg_id[:15]}... (Ticket: {ticket_id[:10]})")
        
        try:
            # 1. Fetch attachments from Graph
            att_resp = await client.users.by_user_id(user_email).messages.by_message_id(msg_id).attachments.get()
            
            if not att_resp or not att_resp.value:
                print(f"  No attachments found in Graph for {msg_id[:15]}")
                continue
                
            attachments = []
            new_body_html = body_html
            
            for att in att_resp.value:
                att_id = getattr(att, 'id', '')
                att_name = getattr(att, 'name', 'unknown')
                ct = getattr(att, 'content_type', '') or 'application/octet-stream'
                cid = (getattr(att, 'content_id', None) or '').strip('<>')
                size = getattr(att, 'size', 0)
                
                attachments.append({
                    'id': att_id,
                    'name': att_name,
                    'content_type': ct,
                    'size': size,
                    'is_inline': getattr(att, 'is_inline', False),
                    'content_id': cid
                })
                
                # NOTE: We no longer bake images into body_html. 
                # Dashboard.js handles live fetching using the metadata in the 'attachments' field.
                pass

            # 3. Update Database
            cur.execute("""
                UPDATE ticket_messages 
                SET attachments = %s, body_html = %s 
                WHERE message_id = %s
            """, (json.dumps(attachments), new_body_html, msg_id))
            conn.commit()
            print(f"  Successfully repaired {msg_id[:15]}")
            
        except Exception as e:
            print(f"  Error repairing {msg_id[:15]}: {e}")

    cur.close()
    conn.close()
    print("Repair complete.")

if __name__ == "__main__":
    asyncio.run(repair_messages())
