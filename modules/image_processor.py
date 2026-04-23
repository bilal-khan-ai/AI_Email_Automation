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
import logging
import base64
from PIL import Image
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.openai_agent import OpenAIAgent

logger = logging.getLogger(__name__)


class ImageProcessor:
    """
    Image processor using OpenAI Vision for visual analysis and OCR.
    """
    
    def __init__(self, ai_agent: Optional["OpenAIAgent"] = None):
        """
        Initialize image processor.
        
        Args:
            ai_agent: Shared OpenAIAgent instance with Vision capabilities.
        """
        self.ai = ai_agent
        
        # Noise filtering keywords (keep local to skip low-quality images)
        self.NOISE_KEYWORDS = [
            "logo", "icon", "symbol", "trademark", "social media",
            "button", "banner", "copyright", "signature", 
            "a man in a white shirt and tie"
        ]
        
        logger.info("ImageProcessor initialized (Cloud Vision mode)")
    
    def set_ai_agent(self, ai_agent: "OpenAIAgent"):
        """Attach AI agent if not provided during init"""
        self.ai = ai_agent

    def process_image(self, image_bytes: bytes, filename: str = "", is_complex_table: bool = False) -> str:
        """
        Process a single image using OpenAI Vision.
        
        Strategy:
        1. Basic local normalization/filtering.
        2. Reroute to OpenAI (gpt-4o for tables, gpt-4o-mini for others).
        
        Args:
            image_bytes: Raw image data
            filename: Original filename (for context hints)
            is_complex_table: If True, uses gpt-4o for better table extraction
            
        Returns:
            str: Analysis result or empty string
        """
        if not self.ai:
            logger.warning("ImageProcessor: AI agent not attached. Skipping image.")
            return ""
            
        try:
            # Step 0: Check filename for hints if is_complex_table is false
            fn = filename.lower()
            if not is_complex_table:
                if any(x in fn for x in ["table", "excel", "sheet", "csv", "data", "report", "stats"]):
                    is_complex_table = True
                    logger.info(f"Detected potential table hint in filename '{filename}'; using gpt-4o")

            # Step 1: Load and validate image locally (CPU)
            image_bytes = self._normalize_image_bytes(image_bytes)
            image = self._load_pil_image(image_bytes)
            
            if image is None:
                return ""
            
            # Re-get bytes if changed (resized)
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG')
            processed_bytes = img_byte_arr.getvalue()
            image.close()

            # Step 2: Choose model
            model = self.ai.ANALYSIS_MODEL if is_complex_table else self.ai.INTERPRETATION_MODEL
            
            # Step 3: Call OpenAI Vision
            logger.info(f"📤 Rerouting image to OpenAI ({model})...")
            analysis = self.ai.analyze_image(processed_bytes, model=model)
            
            if not analysis:
                return ""

            # Step 4: Simple local filtering
            analysis_lower = analysis.lower()
            if any(keyword in analysis_lower for keyword in self.NOISE_KEYWORDS):
                logger.info(f"Filtered image classified as noise by AI: '{analysis[:50]}...'")
                return ""
            
            result = f"[Attachment: {filename}] [Visual Analysis: {analysis.strip()}]"
            logger.info(f"Image processed successfully via {model}")
            
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
            if width < 50 or height < 50:
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