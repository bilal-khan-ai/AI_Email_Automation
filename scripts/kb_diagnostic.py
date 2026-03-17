"""
Knowledge Base Diagnostic Tool
Run this to check your ChromaDB status
"""
import os
import sys
from pathlib import Path
from config import Config

def check_knowledge_base():
    """Comprehensive knowledge base diagnostics"""
    print("=" * 80)
    print("KNOWLEDGE BASE DIAGNOSTIC TOOL")
    print("=" * 80)
    print()
    
    # Check configuration
    print("📋 CONFIGURATION:")
    print(f"   Database Path: {Config.CHROMA_DB_PATH}")
    print(f"   Collection Name: {Config.COLLECTION_NAME}")
    print()
    
    # Check if path exists
    db_path = Path(Config.CHROMA_DB_PATH)
    abs_path = db_path.absolute()
    
    print("📁 FILE SYSTEM CHECK:")
    print(f"   Absolute Path: {abs_path}")
    print(f"   Path Exists: {'✓ YES' if db_path.exists() else '✗ NO'}")
    
    if db_path.exists():
        # List contents
        contents = list(db_path.iterdir())
        print(f"   Directory Contents: {len(contents)} items")
        for item in contents[:10]:  # Show first 10 items
            print(f"      - {item.name}")
        if len(contents) > 10:
            print(f"      ... and {len(contents) - 10} more items")
    print()
    
    # Try to connect to ChromaDB
    print("🔌 CHROMADB CONNECTION TEST:")
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(abs_path))
        print("   ✓ ChromaDB client connected successfully")
        
        # List all collections
        collections = client.list_collections()
        print(f"   Collections Found: {len(collections)}")
        
        if collections:
            for coll in collections:
                count = coll.count()
                print(f"      - '{coll.name}': {count:,} documents")
                
                # Check if this is the configured collection
                if coll.name == Config.COLLECTION_NAME:
                    print(f"        ✓ This is your configured collection!")
                    
                    # Get sample data
                    if count > 0:
                        try:
                            sample = coll.peek(limit=3)
                            print(f"        Sample metadata:")
                            if sample.get('metadatas'):
                                for i, meta in enumerate(sample['metadatas'][:3]):
                                    print(f"          Document {i+1}: {meta}")
                        except Exception as e:
                            print(f"        Could not peek: {e}")
        else:
            print("   ⚠️ No collections found!")
            print("   Did you run ingest.py to populate the knowledge base?")
        
        print()
        
        # Try to load the specific collection
        print(f"🎯 LOADING COLLECTION '{Config.COLLECTION_NAME}':")
        try:
            collection = client.get_collection(name=Config.COLLECTION_NAME)
            count = collection.count()
            print(f"   ✓ Collection loaded successfully")
            print(f"   📊 Total Documents: {count:,}")
            
            if count > 0:
                print(f"   ✓ Knowledge base is populated with {count:,} emails")
                print()
                print("   Sample documents:")
                sample = collection.peek(limit=5)
                if sample.get('ids'):
                    for i, (doc_id, metadata) in enumerate(zip(sample['ids'], sample['metadatas'])):
                        print(f"      {i+1}. ID: {doc_id[:30]}...")
                        print(f"         Subject: {metadata.get('subject', 'N/A')[:60]}...")
                        print(f"         Sender: {metadata.get('sender', 'N/A')}")
            else:
                print(f"   ⚠️ Collection exists but is EMPTY!")
                print(f"   Run ingest.py to populate it with emails")
                
        except Exception as e:
            print(f"   ✗ Failed to load collection: {e}")
            print(f"   Collection '{Config.COLLECTION_NAME}' does not exist!")
            print(f"   Run ingest.py to create and populate it")
        
    except Exception as e:
        print(f"   ✗ Failed to connect to ChromaDB: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=" * 80)
    print("RECOMMENDATION:")
    print("=" * 80)
    
    # Provide recommendations
    if not db_path.exists():
        print("❌ Database path does not exist!")
        print("   → Run: python ingest.py")
        print("   This will create the database and populate it with emails")
    else:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(abs_path))
            collections = client.list_collections()
            
            if not collections:
                print("❌ No collections found in database!")
                print("   → Run: python ingest.py")
            else:
                found = False
                for coll in collections:
                    if coll.name == Config.COLLECTION_NAME:
                        count = coll.count()
                        if count == 0:
                            print(f"❌ Collection '{Config.COLLECTION_NAME}' is empty!")
                            print("   → Run: python ingest.py")
                        else:
                            print(f"✅ Everything looks good!")
                            print(f"   Knowledge base has {count:,} emails ready to use")
                            print(f"   You can now run: python main.py")
                        found = True
                        break
                
                if not found:
                    print(f"❌ Collection '{Config.COLLECTION_NAME}' not found!")
                    print(f"   Found these collections instead:")
                    for coll in collections:
                        print(f"      - {coll.name} ({coll.count():,} docs)")
                    print()
                    print("   Options:")
                    print(f"   1. Update COLLECTION_NAME in .env to match existing collection")
                    print(f"   2. Run: python ingest.py to create '{Config.COLLECTION_NAME}'")
        except Exception as e:
            print(f"❌ Error checking database: {e}")
    
    print("=" * 80)

if __name__ == "__main__":
    check_knowledge_base()
