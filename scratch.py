import json
from modules.sql_logger import SQLLogger
import re

sql = SQLLogger()
with sql.conn_manager.get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT message_id, body_html, attachments FROM ticket_messages WHERE body_html LIKE '%cid:%' ORDER BY timestamp DESC LIMIT 3;")
        rows = cur.fetchall()
        for row in rows:
            print("Message ID:", row[0])
            html = row[1]
            m = re.findall(r'src=["\'](cid:[^"\']+)["\']', html, re.IGNORECASE)
            print('found src cids:', m)
            
            atts = row[2]
            print('attachments type:', type(atts), 'value:', repr(atts)[:200])
            if isinstance(atts, str) and atts: atts = json.loads(atts)
            if atts:
                for a in atts:
                    print('att name:', a.get('name'), 'cid:', a.get('content_id'), 'size:', a.get('size'), 'is_inline:', a.get('is_inline'))
            else:
                print('NO ATTACHMENTS FOR THIS MESSAGE!')
            print("-" * 40)
