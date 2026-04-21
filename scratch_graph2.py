import asyncio
import json
from config import Config
from modules.graph_connector import AsyncioWorker

async def check():
    conf = Config()
    worker = AsyncioWorker(
        client_id=conf.AZURE_CLIENT_ID,
        client_secret=conf.AZURE_CLIENT_SECRET,
        tenant_id=conf.AZURE_TENANT_ID
    )
    worker.start()
    
    # We can inject a request to fetch
    # Wait, instead of setting up queue, I'll just init client manually
    worker._initialize_client()
    
    from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder
    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        select=['id', 'subject', 'hasAttachments'],
        expand=['attachments'],
        top=20
    )
    request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    res = await worker.client.users.by_user_id(conf.USER_EMAIL).messages.get(request_configuration=request_config)
    found = 0
    for msg in res.value:
        atts = getattr(msg, 'attachments', None)
        has_att = getattr(msg, 'has_attachments', False)
        if atts and not has_att:
            print(f"BINGO! Message: {msg.subject} has_attachments={has_att} BUT len(attachments)={len(atts)}")
            found += 1
            for att in atts:
                print(f"  - cid: {getattr(att, 'content_id', 'None')}")
            print("-" * 30)
    print(f"Found {found} messages with attachments array but has_attachments==False")

asyncio.run(check())
