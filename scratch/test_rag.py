import os
import sys
from config import Config
from modules.vector_db import VectorDatabase, BookVectorDB

def main():
    print("Starting non-interactive RAG verification...")
    
    # 1. Experience Database (Past Support Emails)
    print("\nQuerying Support Emails (Experience DB)...")
    email_db = VectorDatabase(Config.CHROMA_DB_PATH, Config.COLLECTION_NAME)
    print(f"Total active emails in DB: {email_db.get_active_count()}")
    
    email_results = email_db.search_similar("Outlook integration sync issue error", top_k=3)
    print(f"Retrieved {len(email_results)} matches:")
    for idx, res in enumerate(email_results, 1):
        print(f"  Match {idx}:")
        print(f"    ID: {res['id']}")
        print(f"    Subject: {res['metadata'].get('subject')}")
        print(f"    Sender: {res['metadata'].get('sender')}")
        print(f"    Distance: {res['distance']:.4f}")
        print(f"    Preview: {res['content'][:150]}...")
        
    # 2. Bookstack Database (Official Documentation)
    print("\nQuerying Bookstack Documentation DB...")
    book_db = BookVectorDB()
    stats = book_db.get_bookstack_stats()
    print(f"Bookstack Stats: {stats}")
    
    book_results = book_db.search_similar("Outlook integration sync issue error", top_k=3)
    print(f"Retrieved {len(book_results)} matches:")
    for idx, res in enumerate(book_results, 1):
        print(f"  Match {idx}:")
        print(f"    ID: {res['id']}")
        print(f"    Book: {res['metadata'].get('book', 'N/A')} > Section: {res['metadata'].get('section', 'N/A')}")
        print(f"    Distance: {res['distance']:.4f}")
        print(f"    Preview: {res['content'][:150]}...")
        
    print("\nVerification complete!")

if __name__ == "__main__":
    main()
