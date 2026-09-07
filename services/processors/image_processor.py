"""
Image Processor - Cloud Integrated (OpenAI Vision)

MIGRATED:
- Removed local BLIP and EasyOCR to free up system RAM.
- Rerouted all Vision/OCR tasks to OpenAI API (gpt-4o-mini / gpt-4o).
- Adheres to local CPU compute policy (no local GPU/CUDA).

Design principles:
- Lightweight image preprocessing (normalization/resizing) on CPU.
- Delegated heavy lifting to OpenAI Vision via OpenAIAgent.
- Noise filtering remains local to save tokens/API calls.
"""

import io
import os
import logging
import base64
import hashlib
import sqlite3
import threading
from PIL import Image
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.connectors.openai_agent import OpenAIAgent

logger = logging.getLogger(__name__)


class VisionCache:
    """
    Persistent SQLite + LRU memory cache for OpenAI Vision analysis results.
    Indexed by deterministic decoded pixel hashes, stripping all EXIF/metadata.
    """
    def __init__(self, db_path: str = "data/vision_cache.sqlite", max_mem_entries: int = 500):
        self.db_path = db_path
        self.max_mem_entries = max_mem_entries
        self._lock = threading.Lock()
        self._mem_cache = {}
        self._init_db()

    def _init_db(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS vision_cache (
                            cache_key TEXT PRIMARY KEY,
                            pixel_hash TEXT NOT NULL,
                            model TEXT NOT NULL,
                            analysis TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            hit_count INTEGER DEFAULT 1,
                            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_pixel_hash ON vision_cache(pixel_hash)")
                    conn.commit()
        except Exception as e:
            logger.warning(f"Failed to initialize vision cache DB: {e}")

    def get(self, cache_key: str) -> Optional[str]:
        with self._lock:
            if cache_key in self._mem_cache:
                return self._mem_cache[cache_key]

        try:
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT analysis FROM vision_cache WHERE cache_key = ?", (cache_key,))
                    row = cursor.fetchone()
                    if row:
                        analysis = row[0]
                        with self._lock:
                            if len(self._mem_cache) >= self.max_mem_entries:
                                try:
                                    self._mem_cache.pop(next(iter(self._mem_cache)))
                                except (StopIteration, KeyError):
                                    pass
                            self._mem_cache[cache_key] = analysis
                        cursor.execute(
                            "UPDATE vision_cache SET hit_count = hit_count + 1, last_accessed = CURRENT_TIMESTAMP WHERE cache_key = ?",
                            (cache_key,)
                        )
                        conn.commit()
                        return analysis
        except Exception as e:
            logger.warning(f"Vision cache get error: {e}")
        return None

    def set(self, cache_key: str, pixel_hash: str, model: str, analysis: str):
        with self._lock:
            if len(self._mem_cache) >= self.max_mem_entries:
                try:
                    self._mem_cache.pop(next(iter(self._mem_cache)))
                except (StopIteration, KeyError):
                    pass
            self._mem_cache[cache_key] = analysis

        try:
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    conn.execute("""
                        INSERT INTO vision_cache (cache_key, pixel_hash, model, analysis)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            analysis = excluded.analysis,
                            hit_count = vision_cache.hit_count + 1,
                            last_accessed = CURRENT_TIMESTAMP
                    """, (cache_key, pixel_hash, model, analysis))
                    conn.commit()
        except Exception as e:
            logger.warning(f"Vision cache set error: {e}")


class ImageProcessor:
    """
    Image processor using OpenAI Vision for visual analysis and OCR.
    """
    
    def __init__(self, ai_agent: Optional["OpenAIAgent"] = None, cache_db_path: str = "data/vision_cache.sqlite"):
        """
        Initialize image processor.
        
        Args:
            ai_agent: Shared OpenAIAgent instance with Vision capabilities.
            cache_db_path: Path to persistent SQLite cache database.
        """
        self.ai = ai_agent
        self.cache = VisionCache(db_path=cache_db_path)
        
        # Noise filtering keywords (Pure noise if no signal is found)
        self.NOISE_KEYWORDS = [
            "logo", "icon", "symbol", "trademark", "social media",
            "button", "banner", "copyright", "signature", 
            "a man in a white shirt and tie"
        ]

        # High-signal keywords: If these are present, the image is NEVER noise
        self.SIGNAL_KEYWORDS = [
            "error", "screenshot", "transaction", "financial", "table", 
            "data", "chart", "graph", "invoice", "receipt", "statement",
            "log", "code", "text", "message", "details", "user interface"
        ]
        
        logger.info("ImageProcessor initialized (Cloud Vision mode with Pixel Cache)")
    
    def set_ai_agent(self, ai_agent: "OpenAIAgent"):
        """Attach AI agent if not provided during init"""
        self.ai = ai_agent

    def _compute_pixel_hash(self, image: Image.Image) -> str:
        """
        Compute a deterministic SHA-256 hash of decoded RGB raster pixels.
        All EXIF, IPTC, XMP, timestamps, author, and container headers are stripped.
        """
        rgb_img = image.convert("RGB")
        return hashlib.sha256(rgb_img.tobytes()).hexdigest()

    def process_image(self, image_bytes: bytes, filename: str = "", is_complex_table: bool = False) -> str:
        """
        Process a single image using OpenAI Vision with metadata-free pixel caching.
        
        Strategy:
        1. Basic local normalization.
        2. Decode to raw raster & compute EXIF-stripped pixel hash.
        3. Check persistent vision cache (bypasses OpenAI API on hit).
        4. Reroute to OpenAI on cache miss & update cache.
        5. Multi-tier filtering (Noise vs Signal).
        """
        if not self.ai:
            logger.warning("ImageProcessor: AI agent not attached. Skipping image.")
            return ""
            
        try:
            # Step 0: Check filename for noise (e.g. image001.png is usually signature noise)
            fn = filename.lower()
            if any(x in fn for x in ["image001", "image002", "image003", "image004"]):
                 # Only skip if it's also small
                 if len(image_bytes) < 50000: # < 50KB
                    logger.info(f"⏭️  Skipping suspected signature image (filename match + small size): {filename}")
                    return ""

            if not is_complex_table:
                if any(x in fn for x in ["table", "excel", "sheet", "csv", "data", "report", "stats"]):
                    is_complex_table = True
                    logger.info(f"Detected potential table hint in filename '{filename}'; using gpt-4o")

            # Step 1: Pre-filter by byte size (Logos are usually < 20KB)
            if len(image_bytes) < 20480: # 20KB
                logger.info(f"⏭️  Skipping small image ({len(image_bytes)} bytes) - likely logo/icon noise.")
                return ""

            # Step 2: Load and validate image locally (CPU)
            image_bytes = self._normalize_image_bytes(image_bytes)
            image = self._load_pil_image(image_bytes)
            
            if image is None:
                return "" # Already logged as too small/invalid in _load_pil_image

            # Compute metadata-stripped pixel hash from decoded raster
            pixel_hash = self._compute_pixel_hash(image)

            # Step 3: Choose model
            model = self.ai.ANALYSIS_MODEL if is_complex_table else self.ai.INTERPRETATION_MODEL
            cache_key = f"vision:{model}:{pixel_hash}"

            # Step 4: Check Vision Cache
            cached_analysis = self.cache.get(cache_key)
            if cached_analysis:
                logger.info(f"⚡ Vision cache HIT for '{filename}' (pixel hash: {pixel_hash[:10]}...)")
                analysis = cached_analysis
            else:
                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format='JPEG')
                processed_bytes = img_byte_arr.getvalue()

                # Call OpenAI Vision
                logger.info(f"📤 Vision cache MISS. Rerouting image to OpenAI ({model})...")
                analysis = self.ai.analyze_image(processed_bytes, model=model)
                
                if not analysis:
                    return ""

                # Populate persistent cache
                self.cache.set(cache_key, pixel_hash, model, analysis)

            image.close()

            # Step 5: Refined local filtering (Signal-Aware)
            import re
            analysis_lower = analysis.lower()
            
            # Use regex for whole-word matching to avoid "log" matching "logo"
            def has_word(text, word):
                return re.search(r'\b' + re.escape(word) + r'\b', text) is not None

            # Check for high-signal keywords first
            has_signal = any(has_word(analysis_lower, kw) for kw in self.SIGNAL_KEYWORDS)
            
            # Check for noise keywords
            contains_noise = any(has_word(analysis_lower, kw) for kw in self.NOISE_KEYWORDS)
            
            # DECISION: Only filter if it has noise AND lacks any signal
            if contains_noise and not has_signal:
                logger.info(f"Filtered image classified as PURE NOISE (no signal found): '{analysis[:50]}...'")
                return ""
            
            result = f"[Attachment: {filename}] [Visual Analysis: {analysis.strip()}]"
            logger.info(f"Image processed successfully via {model}")
            logger.info(f"Result: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Image processing error: {e}")
            return ""
    
    def _normalize_image_bytes(self, data: bytes) -> bytes:
        """Normalize image bytes (handle base64 if needed)."""
        try:
            if data[:10].startswith((b"/9j/", b"iVBOR", b"R0lGOD")):
                return base64.b64decode(data)
            return data
        except Exception:
            return data
    
    def _load_pil_image(self, image_bytes: bytes) -> Optional[Image.Image]:
        """Load image bytes into PIL Image with basic validation."""
        try:
            bio = io.BytesIO(image_bytes)
            img = Image.open(bio)
            
            # Dimension validation
            width, height = img.size
            if width < 100 or height < 100:
                logger.debug(f"Skipped image (too small: {width}x{height})")
                return None
            
            # Convert to RGB
            img = img.convert("RGB")
            
            # Resize large images to stay within payload limits
            max_dimension = 2048
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                logger.debug(f"Resized image to {img.size}")
            
            return img
        
        except Exception as e:
            logger.error(f"PIL load failed: {e}")
            return None
    
    def cleanup(self):
        """No local models to clean up anymore."""
        pass