"""
Image Processor with Strict VRAM Management

CRITICAL VRAM CONSTRAINTS:
- VRAM is extremely tight - any leaks cause OOM
- Strategy: Aggressive unload-after-use (not persistent caching)
- BLIP and embedding models NEVER overlap on GPU
- Every load/unload cycle has explicit cleanup boundaries

Design principles:
- Context managers for guaranteed cleanup
- No hidden references (del + gc.collect + empty_cache)
- CPU fallback for OCR (never uses GPU)
- Clear separation between BLIP inference and embedding
"""

import torch
import io
import logging
import gc
import base64
import numpy as np
from PIL import Image
from contextlib import contextmanager
from typing import Optional

# Lazy imports to avoid heavy module loading
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    logging.warning("easyocr not available")

logger = logging.getLogger(__name__)


class VRAMManager:
    """
    Centralized VRAM cleanup manager.
    
    Ensures aggressive cleanup after every GPU operation.
    Tracks GPU state to prevent overlapping model loads.
    """
    
    def __init__(self):
        self.gpu_available = torch.cuda.is_available()
        self.gpu_permanently_disabled = False
        self.current_model_on_gpu = None  # Track what's loaded
    
    def cleanup(self, force: bool = False):
        """
        Force VRAM cleanup.
        
        Args:
            force: If True, run cleanup even if GPU disabled
        """
        if self.gpu_permanently_disabled and not force:
            return
        
        if not self.gpu_available:
            return
        
        try:
            # Python garbage collection
            gc.collect()
            
            # PyTorch VRAM cleanup
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            logger.debug("VRAM cleanup completed")
        
        except RuntimeError as e:
            logger.error(f"VRAM cleanup failed: {e}")
            self.gpu_permanently_disabled = True
    
    def disable_gpu(self):
        """Permanently disable GPU (after fatal error)"""
        self.gpu_permanently_disabled = True
        self.current_model_on_gpu = None
        logger.warning("GPU permanently disabled due to errors")
    
    def can_use_gpu(self) -> bool:
        """Check if GPU can be used"""
        return self.gpu_available and not self.gpu_permanently_disabled


@contextmanager
def blip_context(vram_manager: VRAMManager, force_cpu: bool = False):
    """
    Context manager for BLIP model with guaranteed cleanup.
    
    Usage:
        with blip_context(vram_manager) as (processor, model, device):
            if model is None:
                # BLIP not available
                return
            # Use BLIP...
        # Automatic cleanup here
    
    Guarantees:
    - Model is unloaded when context exits
    - VRAM is freed
    - No dangling references
    """
    processor = None
    model = None
    device = None
    
    try:
        # Determine device
        if force_cpu or not vram_manager.can_use_gpu():
            device = "cpu"
        else:
            device = "cuda"
        
        # Track that BLIP is being loaded
        if vram_manager.current_model_on_gpu:
            logger.warning(f"Loading BLIP while {vram_manager.current_model_on_gpu} already on GPU")
        vram_manager.current_model_on_gpu = "BLIP"
        
        # Lazy import (avoid loading at module level)
        from transformers import BlipProcessor, BlipForConditionalGeneration
        
        logger.info(f"Loading BLIP on {device}...")
        processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
        model.to(device)
        model.eval()
        
        logger.info("BLIP loaded successfully")
        
        # Yield to caller
        yield processor, model, device
    
    except Exception as e:
        logger.error(f"BLIP context error: {e}")
        if "cuda" in str(e).lower() or "out of memory" in str(e).lower():
            vram_manager.disable_gpu()
        yield None, None, None
    
    finally:
        # CRITICAL: Explicit cleanup to free VRAM
        logger.info("Cleaning up BLIP resources...")
        
        # Move model to CPU before deleting (frees VRAM faster)
        if model is not None and device == "cuda":
            try:
                model.to("cpu")
            except Exception:
                pass
        
        # Delete references
        if processor is not None:
            del processor
        if model is not None:
            del model
        
        # Mark as unloaded
        vram_manager.current_model_on_gpu = None
        
        # Force cleanup
        vram_manager.cleanup(force=True)
        
        logger.info("BLIP cleanup complete")


class ImageProcessor:
    """
    Image processing with strict VRAM management.
    
    Processing strategy:
    1. Load BLIP (if needed) â†’ Inference â†’ Unload immediately
    2. Run OCR on CPU (independent of BLIP)
    3. Never keep models loaded between images
    
    This ensures:
    - No VRAM leaks
    - Predictable memory usage
    - Safe operation under tight VRAM constraints
    """
    
    def __init__(self, force_cpu: bool = False):
        """
        Initialize image processor.
        
        Args:
            force_cpu: Force all operations to CPU (no GPU)
        """
        self.force_cpu = force_cpu
        self.vram_manager = VRAMManager()
        
        # Initialize OCR (CPU-only)
        self.ocr_reader = None
        if EASYOCR_AVAILABLE:
            try:
                self.ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
                logger.info("EasyOCR initialized (CPU-only)")
            except Exception as e:
                logger.error(f"Failed to init EasyOCR: {e}")
        
        # Noise filtering keywords
        self.NOISE_KEYWORDS = [
            "logo", "icon", "symbol", "trademark", "social media",
            "button", "banner", "copyright", "signature", 
            "a man in a white shirt and tie", "Beyond Beyond"
        ]
    
    def process_image(self, image_bytes: bytes) -> str:
        """
        Process a single image with BLIP + OCR.
        
        VRAM safety:
        - BLIP is loaded, used, and unloaded within this method
        - OCR runs on CPU (independent)
        - No models remain in VRAM after return
        
        Args:
            image_bytes: Raw image data
            
        Returns:
            str: Combined caption and OCR text, or empty string if filtered/failed
        """
        caption = ""
        ocr_text = ""
        
        try:
            # Step 1: Load and validate image
            image_bytes = self._normalize_image_bytes(image_bytes)
            image = self._load_pil_image(image_bytes)
            
            if image is None:
                return ""
            
            # Step 2: BLIP Inference (with automatic cleanup)
            with blip_context(self.vram_manager, self.force_cpu) as (processor, model, device):
                if processor is not None and model is not None:
                    caption = self._run_blip_inference(image, processor, model, device)
            
            # At this point, BLIP is completely unloaded and VRAM is freed
            
            # Step 3: OCR Inference (CPU-only, independent)
            if self.ocr_reader is not None:
                ocr_text = self._run_ocr_inference(image)
            
            # Step 4: Filter noise
            combined_context = (caption + " " + ocr_text).lower()
            
            if any(keyword in combined_context for keyword in self.NOISE_KEYWORDS):
                logger.info(f"Filtered image classified as noise: '{caption}'")
                return ""
            
            if len(ocr_text.split()) == 1 and ("www." in ocr_text or ".com" in ocr_text):
                logger.info(f"Filtered image classified as signature URL: '{ocr_text}'")
                return ""
            
            # Step 5: Format result
            parts = []
            if caption:
                parts.append(f"[Visual: {caption}]")
            if ocr_text.strip():
                parts.append(f"[OCR Text: {ocr_text.strip()}]")
            
            result = " ".join(parts)
            if result:
                logger.info(f"Image processed: {result}")
            
            return result
        
        except Exception as e:
            logger.error(f"Image processing error: {e}")
            return ""
        
        finally:
            # Final cleanup (defensive)
            if 'image' in locals() and image is not None:
                try:
                    image.close()
                except Exception:
                    pass
    
    def _run_blip_inference(self, image: Image.Image, processor, model, device: str) -> str:
        """
        Run BLIP inference on a single image.
        
        Args:
            image: PIL Image
            processor: BLIP processor
            model: BLIP model (already on target device)
            device: 'cuda' or 'cpu'
            
        Returns:
            str: Caption text, or empty string on error
        """
        try:
            with torch.no_grad():
                # Prepare inputs
                inputs = processor(images=image, return_tensors="pt")
                inputs = {k: v.to(device) for k, v in inputs.items()}
                
                # Generate caption
                output = model.generate(**inputs)
                caption = processor.decode(output[0].cpu(), skip_special_tokens=True)
                
                # Clean up intermediate tensors
                del inputs
                del output
                
                # Force VRAM cleanup if on GPU
                if device == "cuda":
                    torch.cuda.empty_cache()
                
                return caption
        
        except Exception as e:
            logger.error(f"BLIP inference failed: {e}")
            if "cuda" in str(e).lower() or "out of memory" in str(e).lower():
                self.vram_manager.disable_gpu()
            return ""
    
    def _run_ocr_inference(self, image: Image.Image) -> str:
        """
        Run OCR on a single image (CPU-only).
        
        Args:
            image: PIL Image
            
        Returns:
            str: OCR text, or empty string on error
        """
        try:
            # Convert to numpy array
            np_img = np.array(image)
            
            # Run OCR
            ocr_results = self.ocr_reader.readtext(np_img)
            ocr_text = " ".join(res[1] for res in ocr_results if len(res) > 1)
            
            return ocr_text
        
        except Exception as e:
            logger.error(f"OCR failed: {e}")
            return ""
    
    def _normalize_image_bytes(self, data: bytes) -> bytes:
        """
        Normalize image bytes (handle base64 if needed).
        
        Args:
            data: Raw bytes or base64 encoded bytes
            
        Returns:
            bytes: Normalized image data
        """
        try:
            # Check if already base64 encoded
            if data[:10].startswith((b"/9j/", b"iVBOR", b"R0lGOD")):
                return base64.b64decode(data)
            return data
        except Exception:
            return data
    
    def _load_pil_image(self, image_bytes: bytes) -> Optional[Image.Image]:
        """
        Load image bytes into PIL Image with validation.
        
        Filters:
        - Too small (< 50x50)
        - Extreme aspect ratios
        - Resizes large images to prevent VRAM issues
        
        Args:
            image_bytes: Raw image data
            
        Returns:
            PIL.Image or None if filtered/failed
        """
        try:
            bio = io.BytesIO(image_bytes)
            img = Image.open(bio)
            
            # Dimension validation
            width, height = img.size
            if width < 50 or height < 50:
                logger.debug(f"Skipped image (too small: {width}x{height})")
                return None
            
            aspect_ratio = width / height
            if aspect_ratio > 8 or aspect_ratio < 0.2:
                logger.debug(f"Skipped image (extreme aspect ratio: {aspect_ratio:.2f})")
                return None
            
            # Convert to RGB
            img = img.convert("RGB")
            
            # Resize large images to prevent VRAM issues
            max_dimension = 1280
            if max(img.size) > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                logger.debug(f"Resized image to {img.size}")
            
            return img
        
        except Exception as e:
            logger.error(f"PIL load failed: {e}")
            return None
    
    def cleanup(self):
        """
        Force cleanup of all resources.
        
        Call this during shutdown or after processing a batch.
        """
        logger.info("ImageProcessor cleanup requested")
        
        # Clean up OCR if needed
        if self.ocr_reader is not None:
            try:
                del self.ocr_reader
            except Exception:
                pass
            self.ocr_reader = None
        
        # Final VRAM cleanup
        self.vram_manager.cleanup(force=True)
        
        logger.info("ImageProcessor cleanup complete")