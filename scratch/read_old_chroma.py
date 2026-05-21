import chromadb

def main():
    print("Connecting to old ChromaDB at ./chroma_db...")
    try:
        client = chromadb.PersistentClient(path="./chroma_db")
        collections = client.list_collections()
        print(f"Found {len(collections)} collections:")
        for col in collections:
            print(f"  Collection Name: {col.name}")
            print(f"    Count: {col.count()}")
            # Print a sample document and metadata if count > 0
            if col.count() > 0:
                sample = col.peek(1)
                print(f"    Sample ID: {sample['ids'][0] if sample['ids'] else 'N/A'}")
                print(f"    Sample Document preview: {sample['documents'][0][:150] if sample['documents'] else 'N/A'}")
                print(f"    Sample Metadata: {sample['metadatas'][0] if sample['metadatas'] else 'N/A'}")
    except Exception as e:
        print(f"ChromaDB inspection error: {e}")

if __name__ == "__main__":
    main()
