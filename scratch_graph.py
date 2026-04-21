import asyncio
import json
from config import Config
from modules.graph_connector import GraphConnector

async def check():
    conf = Config()
    graph = GraphConnector(
        client_id=conf.AZURE_CLIENT_ID,
        client_secret=conf.AZURE_CLIENT_SECRET,
        tenant_id=conf.AZURE_TENANT_ID
    )
    # just manual fetch
    from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder
    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        select=['id', 'subject', 'hasAttachments'],
        expand=['attachments'],
        top=50
    )
    request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    res = await graph.client.users.by_user_id(conf.USER_EMAIL).messages.get(request_configuration=request_config)
    found_att = False
    for msg in res.value:
        if getattr(msg, 'has_attachments', False) or getattr(msg, 'attachments', None):
            atts = getattr(msg, 'attachments', None)
            if atts:
                for att in atts:
                    cid = getattr(att, 'content_id', 'None')
                    if cid and cid != 'None':
                        found_att = True
                        print(f"Message: {msg.subject} has_attachments: {getattr(msg, 'has_attachments', 'None')}")
                        print(f"  Got {len(atts)} attachments")
                        print(f"    - name: {getattr(att, 'name', 'None')}")
                        print(f"      content_id: {cid}")
                        print(f"      is_inline: {getattr(att, 'is_inline', 'None')}")
                        print(f"      odata_type: {getattr(att, 'odata_type', 'None')}")
                        print("-" * 30)
    if not found_att: print("No attachments with content_id found in last 50 emails")

asyncio.run(check())
