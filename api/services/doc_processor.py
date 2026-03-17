"""modules.doc_processor

Local (non-LLM) document extraction for attachments that are NOT plain images.

Primary targets
---------------
- .docx : usually screenshots + a few lines describing the issue
- .pdf : text extraction + OCR for scanned pages + table detection

For .docx we do:
- extract paragraph text
- extract docx tables (if any)
- extract embedded images and run modules.image_processor.ImageProcessor on each
  (BLIP caption + OCR, depending on what you have installed)

For .pdf we do:
- extract text via pypdf (per-page)
- render first N pages to images and run OCR + caption
- detect table-like text blocks and summarize via TablesProcessor

Main API
--------
- DocProcessor.process_bytes(file_bytes, filename)
- DocProcessor.process_path(path)

Returns dict with:
- ok: bool
- filename, kind, warnings
- text: extracted text (bounded)
- tables_markdown: list[str]
- images: list[dict] {name, size, description}
- combined_text: str (ready to append into prompt context)
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.image_processor import ImageProcessor
    from api.services.tables_processor import TablesProcessor

logger = logging.getLogger(__name__)


def _clip_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 200] + "\n...\n[TRUNCATED]\n" + text[-200:]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _df_like_to_markdown(rows: List[List[str]], headers: List[str], max_rows: int = 15, max_cols: int = 10) -> str:
    # very small markdown builder to avoid pandas dependency here
    headers = (headers or [])[:max_cols]
    out_rows = [r[:max_cols] for r in (rows or [])[:max_rows]]
    if not headers and out_rows:
        headers = [f"col{i+1}" for i in range(len(out_rows[0]))]
    if not headers:
        return ""

    def esc(x: Any) -> str:
        s = "" if x is None else str(x)
        s = s.replace("\n", " ").strip()
        return s

    # header
    lines = []
    lines.append("| " + " | ".join(esc(h) for h in headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in out_rows:
        # pad
        rr = list(r) + [""] * max(0, len(headers) - len(r))
        rr = rr[: len(headers)]
        lines.append("| " + " | ".join(esc(c) for c in rr) + " |")
    return "\n".join(lines)


@dataclass
class DocProcessorConfig:
    max_text_chars: int = 12000
    max_combined_chars: int = 18000
    max_images: int = 12
    max_tables: int = 8
    max_table_rows: int = 15
    max_table_cols: int = 10
    # PDF-specific settings
    max_pdf_pages: int = 10
    max_pdf_ocr_pages: int = 6
    pdf_render_dpi: int = 200


class DocProcessor:
    def __init__(
        self,
        image_processor: Optional["ImageProcessor"] = None,
        tables_processor: Optional["TablesProcessor"] = None,
        config: Optional[DocProcessorConfig] = None,
    ):
        """Create a document processor.

        image_processor: expected to look like modules.image_processor.ImageProcessor
            - must expose process_image(image_bytes)->str
        tables_processor: expected to look like modules.tables_processor.TablesProcessor
            - must expose summarize_dataframe(df, name)->dict
        """
        self.image_processor = image_processor
        self.tables_processor = tables_processor
        self.config = config or DocProcessorConfig()

    def process_path(self, path: str) -> Dict[str, Any]:
        with open(path, "rb") as f:
            return self.process_bytes(f.read(), filename=path)

    def process_bytes(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        filename = filename or "document_attachment"
        lower = filename.lower()

        if lower.endswith(".docx"):
            return self._process_docx(file_bytes, filename)
        if lower.endswith(".pdf"):
            return self._process_pdf(file_bytes, filename)

        # best-effort docx first then pdf text
        try:
            return self._process_docx(file_bytes, filename)
        except Exception:
            return self._process_pdf(file_bytes, filename)

    # -------------------------
    # DOCX
    # -------------------------

    def _process_docx(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        warnings: List[str] = []
        text_lines: List[str] = []
        tables_md: List[str] = []
        images: List[Dict[str, Any]] = []

        # 1) extract paragraphs and tables
        try:
            from docx import Document  # python-docx
            doc = Document(io.BytesIO(file_bytes))

            for p in doc.paragraphs:
                t = _norm(p.text)
                if not t:
                    continue
                style = getattr(p.style, "name", "") if getattr(p, "style", None) else ""
                if style and "heading" in style.lower():
                    text_lines.append(f"\n## {t}")
                else:
                    # keep bullets-ish
                    if t.endswith(":") or t.endswith("-"):
                        text_lines.append(f"\n{t}")
                    else:
                        text_lines.append(f"- {t}")

            # docx tables
            for ti, table in enumerate(doc.tables[: self.config.max_tables]):
                rows: List[List[str]] = []
                for row in table.rows[: self.config.max_table_rows]:
                    rows.append([_norm(cell.text) for cell in row.cells[: self.config.max_table_cols]])
                headers = rows[0] if rows else []
                body = rows[1:] if len(rows) > 1 else []
                md = _df_like_to_markdown(body, headers, max_rows=self.config.max_table_rows, max_cols=self.config.max_table_cols)
                if md:
                    tables_md.append(md)

        except Exception as e:
            warnings.append(f"Failed to read docx text/tables: {e}")

        # 2) extract embedded images via zip structure
        try:
            zf = zipfile.ZipFile(io.BytesIO(file_bytes))
            media = [n for n in zf.namelist() if n.startswith("word/media/")]
            if len(media) > self.config.max_images:
                warnings.append(f"Docx contains {len(media)} images; only processing first {self.config.max_images}.")
            media = media[: self.config.max_images]

            for n in media:
                b = zf.read(n)
                img_info: Dict[str, Any] = {"name": n.split("/")[-1], "description": ""}

                # size (best-effort)
                try:
                    from PIL import Image
                    im = Image.open(io.BytesIO(b))
                    img_info["size"] = f"{im.size[0]}x{im.size[1]}"
                    im.close()
                except Exception:
                    img_info["size"] = "unknown"

                if self.image_processor is not None:
                    try:
                        desc = self.image_processor.process_image(b) or ""
                        img_info["description"] = desc.strip()
                    except Exception as e:
                        img_info["description"] = ""
                        warnings.append(f"Image processing failed for {img_info['name']}: {e}")
                images.append(img_info)

        except Exception as e:
            warnings.append(f"Failed to extract docx images: {e}")

        extracted_text = "\n".join(text_lines).strip()
        extracted_text = _clip_text(extracted_text, self.config.max_text_chars)

        combined = self._build_combined_text(filename, "docx", extracted_text, tables_md, images)
        return {
            "ok": True,
            "filename": filename,
            "kind": "docx",
            "warnings": warnings,
            "text": extracted_text,
            "tables_markdown": tables_md,
            "images": images,
            "combined_text": combined,
        }

    # -------------------------
    # PDF (enhanced with OCR + table detection)
    # -------------------------

    def _process_pdf(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        warnings: List[str] = []
        text_pages: List[str] = []
        tables_md: List[str] = []
        images: List[Dict[str, Any]] = []

        # 1) Extract text per page via pypdf
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(file_bytes))
            total_pages = len(reader.pages)
            pages_to_process = min(total_pages, self.config.max_pdf_pages)
            
            if total_pages > self.config.max_pdf_pages:
                warnings.append(f"PDF has {total_pages} pages; only processing first {self.config.max_pdf_pages}.")
            
            for i, page in enumerate(reader.pages[:pages_to_process]):
                try:
                    page_text = page.extract_text() or ""
                    page_text = page_text.strip()
                    if page_text:
                        text_pages.append(f"[Page {i+1}]\n{page_text}")
                except Exception:
                    continue
                    
        except Exception as e1:
            # fallback PyPDF2
            try:
                from PyPDF2 import PdfReader  # type: ignore
                reader = PdfReader(io.BytesIO(file_bytes))
                total_pages = len(reader.pages)
                pages_to_process = min(total_pages, self.config.max_pdf_pages)
                
                if total_pages > self.config.max_pdf_pages:
                    warnings.append(f"PDF has {total_pages} pages; only processing first {self.config.max_pdf_pages}.")
                
                for i, page in enumerate(reader.pages[:pages_to_process]):
                    try:
                        page_text = page.extract_text() or ""
                        page_text = page_text.strip()
                        if page_text:
                            text_pages.append(f"[Page {i+1}]\n{page_text}")
                    except Exception:
                        continue
                        
            except Exception as e2:
                warnings.append(f"No PDF reader available or failed to parse PDF: {e1} / {e2}")

        extracted_text = "\n\n".join(text_pages)
        extracted_text = _clip_text(_norm(extracted_text), self.config.max_text_chars)

        # 2) Render pages to images for OCR (if pdf2image available)
        ocr_pages = min(len(text_pages), self.config.max_pdf_ocr_pages)
        if self.image_processor is not None and ocr_pages > 0:
            try:
                from pdf2image import convert_from_bytes  # type: ignore
                
                pil_images = convert_from_bytes(
                    file_bytes,
                    first_page=1,
                    last_page=ocr_pages,
                    dpi=self.config.pdf_render_dpi
                )
                
                for i, pil_img in enumerate(pil_images):
                    try:
                        # Convert PIL image to PNG bytes
                        img_byte_arr = io.BytesIO()
                        pil_img.save(img_byte_arr, format='PNG')
                        img_bytes = img_byte_arr.getvalue()
                        pil_img.close()
                        
                        # Process via ImageProcessor
                        desc = self.image_processor.process_image(img_bytes) or ""
                        
                        if desc:
                            images.append({
                                "name": f"pdf_page_{i+1}.png",
                                "size": f"{pil_img.width}x{pil_img.height}" if hasattr(pil_img, 'width') else "unknown",
                                "description": desc.strip()
                            })
                            
                    except Exception as e:
                        warnings.append(f"OCR failed for page {i+1}: {e}")
                        
            except Exception as e:
                warnings.append(f"pdf2image unavailable or failed: {e}")

        # 3) Table detection from text (heuristic)
        if self.tables_processor is not None and text_pages:
            try:
                tables_found = self._detect_and_summarize_pdf_tables(text_pages)
                tables_md.extend(tables_found)
            except Exception as e:
                warnings.append(f"PDF table detection failed: {e}")

        combined = self._build_combined_text(filename, "pdf", extracted_text, tables_md, images)
        return {
            "ok": True,
            "filename": filename,
            "kind": "pdf",
            "warnings": warnings,
            "text": extracted_text,
            "tables_markdown": tables_md,
            "images": images,
            "combined_text": combined,
        }

    def _detect_and_summarize_pdf_tables(self, text_pages: List[str]) -> List[str]:
        """
        Detect table-like blocks in PDF text and summarize them.
        
        Heuristic approach:
        - Look for consecutive lines with similar column separators (2+ spaces, |, tabs)
        - Parse into DataFrame and pass to TablesProcessor
        
        Returns:
            List of markdown table strings
        """
        tables_md = []
        
        if not self.tables_processor:
            return tables_md
        
        try:
            import pandas as pd
        except ImportError:
            return tables_md
        
        for page_idx, page_text in enumerate(text_pages[:self.config.max_pdf_pages]):
            lines = page_text.split('\n')
            table_blocks = self._find_table_blocks(lines)
            
            for block_idx, block_lines in enumerate(table_blocks[:self.config.max_tables]):
                try:
                    # Try to parse as fixed-width or delimited
                    df = self._parse_table_block(block_lines)
                    
                    if df is not None and not df.empty:
                        # Use TablesProcessor to summarize
                        summary = self.tables_processor.summarize_dataframe(
                            df, 
                            name=f"pdf_page_{page_idx+1}_table_{block_idx+1}"
                        )
                        
                        if summary and summary.get("combined_text"):
                            tables_md.append(summary["combined_text"])
                            
                except Exception as e:
                    logger.debug(f"Failed to parse table block: {e}")
                    continue
        
        return tables_md

    def _find_table_blocks(self, lines: List[str]) -> List[List[str]]:
        """
        Find consecutive lines that look like table rows.
        
        Criteria:
        - At least 3 consecutive lines
        - Similar number of "columns" (split by 2+ spaces or |)
        - At least 2 columns per line
        """
        if len(lines) < 3:
            return []
        
        blocks = []
        current_block = []
        expected_cols = 0
        
        for line in lines:
            line = line.strip()
            if not line:
                if len(current_block) >= 3:
                    blocks.append(current_block)
                current_block = []
                expected_cols = 0
                continue
            
            # Count columns (split by 2+ spaces or |)
            cols_pattern1 = re.split(r'\s{2,}', line)
            cols_pattern2 = re.split(r'\|', line)
            
            # Choose the pattern that gives more columns
            if len(cols_pattern2) > len(cols_pattern1):
                cols = [c.strip() for c in cols_pattern2 if c.strip()]
            else:
                cols = [c.strip() for c in cols_pattern1 if c.strip()]
            
            col_count = len(cols)
            
            if col_count >= 2:
                if expected_cols == 0:
                    expected_cols = col_count
                    current_block = [line]
                elif abs(col_count - expected_cols) <= 1:
                    current_block.append(line)
                else:
                    if len(current_block) >= 3:
                        blocks.append(current_block)
                    current_block = [line]
                    expected_cols = col_count
            else:
                if len(current_block) >= 3:
                    blocks.append(current_block)
                current_block = []
                expected_cols = 0
        
        # Don't forget last block
        if len(current_block) >= 3:
            blocks.append(current_block)
        
        return blocks

    def _parse_table_block(self, block_lines: List[str]) -> Optional[Any]:
        """Parse a table block into a DataFrame."""
        try:
            import pandas as pd
        except ImportError:
            return None
        
        if not block_lines:
            return None
        
        # Try parsing as fixed-width
        try:
            block_text = "\n".join(block_lines)
            df = pd.read_fwf(io.StringIO(block_text))
            if df is not None and not df.empty and len(df.columns) >= 2:
                return df
        except Exception:
            pass
        
        # Try parsing with | as delimiter
        try:
            parsed = []
            for line in block_lines:
                cols = [c.strip() for c in re.split(r'\|', line) if c.strip()]
                if cols:
                    parsed.append(cols)
            
            if parsed and len(parsed) >= 2:
                # Assume first row is header
                headers = parsed[0]
                rows = parsed[1:]
                # Pad rows to match header length
                max_cols = len(headers)
                rows_padded = [r + [''] * (max_cols - len(r)) for r in rows]
                df = pd.DataFrame(rows_padded, columns=headers)
                return df
        except Exception:
            pass
        
        # Try parsing with 2+ spaces as delimiter
        try:
            parsed = []
            for line in block_lines:
                cols = [c.strip() for c in re.split(r'\s{2,}', line) if c.strip()]
                if cols:
                    parsed.append(cols)
            
            if parsed and len(parsed) >= 2:
                headers = parsed[0]
                rows = parsed[1:]
                max_cols = len(headers)
                rows_padded = [r + [''] * (max_cols - len(r)) for r in rows]
                df = pd.DataFrame(rows_padded, columns=headers)
                return df
        except Exception:
            pass
        
        return None

    # -------------------------
    # combined output
    # -------------------------

    def _build_combined_text(
        self,
        filename: str,
        kind: str,
        extracted_text: str,
        tables_md: List[str],
        images: List[Dict[str, Any]],
    ) -> str:
        parts: List[str] = []
        parts.append(f"[Attachment: {filename}] ({kind})")

        if extracted_text:
            parts.append("\nDocument text:")
            parts.append(extracted_text)

        if tables_md:
            parts.append("\nDocument tables (summary):")
            for i, md in enumerate(tables_md[: self.config.max_tables], start=1):
                parts.append(f"\nTable {i}:")
                parts.append(md)

        if images:
            parts.append("\nDocument images (OCR + visual analysis):")
            for i, img in enumerate(images[: self.config.max_images], start=1):
                name = img.get("name", f"image{i}")
                size = img.get("size", "unknown")
                desc = (img.get("description") or "").strip()
                if desc:
                    parts.append(f"- {name} ({size}): {desc}")
                else:
                    parts.append(f"- {name} ({size}): [no text detected]")
        combined = "\n".join(parts).strip() + "\n"
        return _clip_text(combined, self.config.max_combined_chars)