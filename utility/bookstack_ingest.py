#!/usr/bin/env python3
import os
import re
import base64
import json
import requests
import argparse
import hashlib
import sys
from pathlib import Path
from typing import List, Dict, Any
from openai import OpenAI
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import torch
from config import Config

# --- CONFIGURATION ---
OPENAI_API_KEY = Config.OPENAI_API_KEY
CHROMA_PATH = "./chromaDB"
VLM_CACHE_PATH = "vlm_cache.json"

class DocumentArchitect:
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)
        self.vlm_cache = self._load_cache()

    def _load_cache(self) -> Dict[str, str]:
        if os.path.exists(VLM_CACHE_PATH):
            try:
                with open(VLM_CACHE_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        with open(VLM_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.vlm_cache, f, indent=2)

    def analyze_image_vlm(self, image_source: str) -> str:
        """Step 1: VLM Pass using GPT-4.1-mini"""
        try:
            if image_source.startswith(('http://', 'https://')):
                resp = requests.get(image_source, timeout=15)
                resp.raise_for_status()
                image_bytes = resp.content
                ext = Path(image_source.split('?')[0]).suffix.lower()
            else:
                with open(image_source, 'rb') as f:
                    image_bytes = f.read()
                ext = Path(image_source).suffix.lower()

            media_type = 'image/png' if ext == '.png' else 'image/jpeg'
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            response = self.client.chat.completions.create(
                model=Config.ANALYSIS_MODEL, 
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this UI screenshot. Describe the page name, all buttons/fields, and any visible errors in concise prose. No markdown headers."},
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{base64_image}"}}
                    ]
                }],
                max_tokens=500
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"    [Error] VLM API call failed: {e}")
            return "[Visual analysis unavailable]"

    def augment_markdown(self, markdown_text: str, base_path: str) -> str:
        """Finds images, checks persistent cache, and appends VLM descriptions."""
        img_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        
        def _replacer(match):
            alt_text = match.group(1)
            img_path = match.group(2)
            source = img_path if img_path.startswith('http') else str(Path(base_path) / img_path)
            
            if source not in self.vlm_cache:
                print(f"  Processing image (New API Call): {img_path[:50]}...")
                self.vlm_cache[source] = self.analyze_image_vlm(source)
                self._save_cache() 
            
            description = self.vlm_cache[source]
            return f"\n\n![{alt_text}]({img_path})\n**VISUAL CONTENT:** {description}\n\n"

        return re.sub(img_pattern, _replacer, markdown_text)

    def generate_semantic_chunks(self, augmented_text: str, book_name: str) -> List[Dict]:
        """Step 2: Semantic Chunking using INTERPRETATION_MODEL"""
        system_prompt = f"""
        You are a Document Architect. Break the document into logical chunks.
        Return ONLY a JSON object: {{"chunks": [ {{"content": "...", "metadata": {{...}} }} ] }}
        """
        response = self.client.chat.completions.create(
            model=Config.INTERPRETATION_MODEL, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Book Name: {book_name}\n\nDocument:\n{augmented_text}"}
            ],
            response_format={"type": "json_object"}
        )
        
        try:
            data = json.loads(response.choices[0].message.content)
            raw_chunks = data.get("chunks", [])
            refined_chunks = []
            for chunk in raw_chunks:
                if not isinstance(chunk, dict): continue
                if "content" not in chunk: chunk["content"] = str(chunk)
                if "metadata" not in chunk:
                    chunk["metadata"] = {"section": "General", "intent": "reference"}
                refined_chunks.append(chunk)
            return refined_chunks
        except Exception:
            return [{"content": augmented_text, "metadata": {"section": "Manual"}}]

class VectorStore:
    def __init__(self, path: str):
        self.client = chromadb.PersistentClient(path=path, settings=Settings(allow_reset=True))
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2",
            device=device
        )
        self.collection = self.client.get_or_create_collection("bookstack_db", embedding_function=self.ef)

    def save_chunks(self, chunks: List[Dict], book_name: str):
        ids, docs, metas = [], [], []
        book_id = re.sub(r'[^a-zA-Z0-9]', '_', book_name).lower()
        for i, chunk in enumerate(chunks):
            content_hash = hashlib.md5(chunk['content'].encode()).hexdigest()[:8]
            ids.append(f"{book_id}_{i}_{content_hash}")
            docs.append(chunk['content'])
            meta = chunk.get('metadata', {})
            meta.update({"book": book_name, "authority": "hard_fact"})
            cleaned_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v for k, v in meta.items()}
            metas.append(cleaned_meta)

        if ids:
            self.collection.add(ids=ids, documents=docs, metadatas=metas)
            print(f"  Stored {len(ids)} chunks for {book_name}.")

    def delete_book(self, book_name: str):
        """Removes all chunks associated with a specific book name."""
        try:
            # ChromaDB uses a 'where' filter for metadata-based deletion
            before = self.collection.count()
            self.collection.delete(where={"book": book_name})
            after = self.collection.count()
            deleted_count = before - after
            if deleted_count > 0:
                print(f"✅ Successfully deleted {deleted_count} chunks for book: '{book_name}'")
            else:
                print(f"⚠️ No chunks found for book: '{book_name}'. Nothing was deleted.")
        
        except Exception as e:
            print(f"  [Error] Failed to delete chunks for '{book_name}': {e}")

def process_file(file_path: Path, architect: DocumentArchitect, db: VectorStore):
    """Core logic to process a single file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        raw_text = f.read()
    
    h1_match = re.search(r'^#\s+(.+)$', raw_text, re.MULTILINE)
    book_name = h1_match.group(1).strip() if h1_match else file_path.stem
    
    print(f"\n--- Processing: {book_name} ---")
    print(f"Pass 1: VLM Analysis...")
    augmented_doc = architect.augment_markdown(raw_text, str(file_path.parent))
    
    print(f"Pass 2: Semantic Chunking...")
    semantic_chunks = architect.generate_semantic_chunks(augmented_doc, book_name)
    
    print(f"Pass 3: Finalizing DB...")
    db.save_chunks(semantic_chunks, book_name)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", help="Path to a single Markdown file")
    parser.add_argument("--folder", help="Path to a directory of Markdown files")
    parser.add_argument("--dump", help="Dump collection to JSON file")
    parser.add_argument("--delete", help="Delete all chunks for a specific book name")
    args = parser.parse_args()

    db = VectorStore(CHROMA_PATH)

    if args.dump:
        data = db.collection.get()
        records = [{"id": i, "metadata": m, "document": d} for i, m, d in zip(data['ids'], data['metadatas'], data['documents'])]
        with open(args.dump, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2)
        print(f"Dumping complete: {args.dump}")
        return
    
    if args.delete:
        print(f"Attempting to delete book: {args.delete}...")
        db.delete_book(args.delete)
        return

    if not OPENAI_API_KEY:
        print("Error: Config.OPENAI_API_KEY is not set.")
        return

    architect = DocumentArchitect(OPENAI_API_KEY)

    # Mode: Folder
    if args.folder:
        folder_path = Path(args.folder)
        if not folder_path.is_dir():
            print(f"Error: {args.folder} is not a directory.")
            return
        
        md_files = list(folder_path.glob("*.md"))
        print(f"Found {len(md_files)} files in {args.folder}")
        for md_file in md_files:
            process_file(md_file, architect, db)
        print("\nAll folder processing complete.")

    # Mode: Single File
    elif args.file:
        process_file(Path(args.file), architect, db)
    
    else:
        print("Error: Provide a file path or use --folder.")

if __name__ == "__main__":
    main()