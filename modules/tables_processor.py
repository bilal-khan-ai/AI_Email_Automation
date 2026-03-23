"""modules.tables_processor

General-purpose (non-LLM) table extraction + summarization for attachments.

This module is intentionally **domain-agnostic**.
It turns table attachments (Excel/CSV/TSV) into compact, high-signal text that
can be appended to your LLM prompt context later.

It does NOT assume a specific customer format.

Supported formats
-----------------
- .xlsx / .xlsm (via pandas + openpyxl)
- .csv / .tsv (via pandas)

Main API
--------
- TablesProcessor.process_bytes(file_bytes, filename)
- TablesProcessor.process_path(path)
- TablesProcessor.summarize_dataframe(df, name="Sheet1")  # NEW: for programmatic use

Output contract
---------------
Returns a dict with:
- ok: bool
- filename: str
- kind: str ("xlsx", "csv", "tsv", "unknown")
- warnings: list[str]
- sheets: list[dict] (for xlsx; for csv/tsv it's a single pseudo-sheet)
- combined_text: str (ready to append into prompt context)

Design notes
------------
- Keeps output bounded (config.max_combined_chars)
- Avoids dumping entire datasets
- Gives schema + signals + a small preview (if enabled)
- Tries to recover from messy headers (merged headers / blank top rows)
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# -------------------------
# helpers
# -------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _clip_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 200] + "\n...\n[TRUNCATED]\n" + text[-200:]


def _make_unique_columns(cols: List[str]) -> List[str]:
    """Deduplicate repeated column names by appending suffixes."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for c in cols:
        base = (c or "").strip()
        if base == "":
            base = "col"
        k = base
        if k in seen:
            seen[k] += 1
            k = f"{base}__{seen[base]}"
        else:
            seen[k] = 1
        out.append(k)
    return out


def _df_to_markdown(df: pd.DataFrame, max_rows: int = 20, max_cols: int = 12) -> str:
    """Convert a dataframe head into a bounded markdown table."""
    if df is None or df.empty:
        return ""
    d = df.copy()
    if d.shape[1] > max_cols:
        d = d.iloc[:, :max_cols]
    if d.shape[0] > max_rows:
        d = d.head(max_rows)

    # Make values safe/compact for prompt context
    def _fmt(v: Any) -> str:
        if pd.isna(v):
            return ""
        if isinstance(v, float):
            # avoid scientific notation spam
            return (f"{v:.6f}").rstrip("0").rstrip(".")
        return str(v)

    for c in d.columns:
        d[c] = d[c].map(_fmt)

    try:
        return d.to_markdown(index=False)
    except Exception:
        return d.to_csv(index=False)


def _guess_delimiter(filename: str) -> str:
    fn = (filename or "").lower()
    if fn.endswith(".tsv"):
        return "\t"
    return ","


def _looks_like_bad_header(columns: List[Any]) -> bool:
    """Heuristic: header row is likely wrong/empty/merged."""
    if not columns:
        return True

    cols = [str(c) for c in columns]
    unnamed_ratio = sum(_norm(c).startswith("unnamed") for c in cols) / max(1, len(cols))
    numeric_like_ratio = sum(_norm(c).isdigit() for c in cols) / max(1, len(cols))

    # Common cases:
    # - Excel with merged header results in many "Unnamed: x"
    # - CSV read without header gives 0,1,2... columns
    return unnamed_ratio >= 0.5 or numeric_like_ratio >= 0.7


def _score_header_row(row_values: List[Any]) -> float:
    """Score a potential header row: prefer many non-empty strings."""
    cleaned = []
    for v in row_values:
        if pd.isna(v):
            cleaned.append("")
        else:
            s = str(v).strip()
            cleaned.append(s)

    non_empty = [s for s in cleaned if s]
    if not non_empty:
        return 0.0

    str_like = [s for s in non_empty if not re.fullmatch(r"[-+]?\d+(\.\d+)?", s)]

    # reward strings, penalize purely numeric rows
    score = len(str_like) * 2.0 + len(non_empty) * 0.5

    # penalize rows that look like repeated "unnamed" placeholders
    score -= sum(_norm(s).startswith("unnamed") for s in non_empty) * 3.0

    # reward uniqueness a bit
    score += len(set(_norm(s) for s in str_like)) * 0.3

    return max(score, 0.0)


def _find_best_header_row(sample_grid: pd.DataFrame, max_scan_rows: int = 25) -> Optional[int]:
    """Given a header=None grid, find the most plausible header row index."""
    if sample_grid is None or sample_grid.empty:
        return None

    scan_rows = min(max_scan_rows, sample_grid.shape[0])
    best_i: Optional[int] = None
    best_score = 0.0

    for i in range(scan_rows):
        row = sample_grid.iloc[i].tolist()
        score = _score_header_row(row)
        if score > best_score:
            best_score = score
            best_i = i

    # Require a minimum score to avoid picking a random data row
    if best_score < 6.0:
        return None

    return best_i


def _detect_semantic_columns(columns: List[str]) -> Dict[str, List[str]]:
    """Lightweight semantic grouping (still generic)."""
    groups = {
        "date": [],
        "amount": [],
        "quantity": [],
        "diff": [],
        "id": [],
        "code": [],
        "status": [],
        "currency": [],
    }

    for col in columns:
        n = _norm(col)

        if any(k in n for k in ["date", "dt", "timestamp", "time", "as on", "as_of", "asof"]):
            groups["date"].append(col)

        if any(k in n for k in ["amount", "amt", "value", "price", "cost", "valuation", "balance", "total"]):
            groups["amount"].append(col)

        if any(k in n for k in ["qty", "quantity", "units", "unit", "shares", "count"]):
            groups["quantity"].append(col)

        if any(k in n for k in ["diff", "difference", "delta", "variance", "mismatch"]):
            groups["diff"].append(col)

        if any(k in n for k in ["id", "ticket", "ref", "reference", "folio", "account", "acc", "client"]):
            groups["id"].append(col)

        if any(k in n for k in ["code", "isin", "ifsc", "pan", "aadhaar", "aadhar", "prod", "product", "inv"]):
            groups["code"].append(col)

        if any(k in n for k in ["status", "state", "result", "error", "remark", "reason"]):
            groups["status"].append(col)

        if any(k in n for k in ["currency", "ccy", "inr", "usd", "eur", "gbp"]):
            groups["currency"].append(col)

    return {k: v for k, v in groups.items() if v}


# -------------------------
# configuration
# -------------------------

@dataclass
class TablesProcessorConfig:
    # display / size bounds
    max_preview_rows: int = 25
    max_preview_cols: int = 12
    max_markdown_chars: int = 9000
    max_combined_chars: int = 18000

    # xlsx specifics
    max_sheets: int = 10

    # summarization limits
    max_numeric_cols: int = 10
    max_categorical_cols: int = 8
    top_values_per_col: int = 5
    max_outlier_rows: int = 8

    # performance guards
    max_rows_full_scan: int = 50000   # above this, we sample for stats
    sample_rows_for_stats: int = 5000
    header_scan_rows: int = 25
    
    # output format controls
    include_preview: bool = False
    preview_rows: int = 8


class TablesProcessor:
    def __init__(self, config: Optional[TablesProcessorConfig] = None):
        # Allow Config to override include_preview and preview_rows
        base_config = config or TablesProcessorConfig()
        
        # Override from environment if available
        try:
            from config import Config
            base_config.include_preview = getattr(Config, 'TABLES_INCLUDE_PREVIEW', base_config.include_preview)
            base_config.preview_rows = getattr(Config, 'TABLES_PREVIEW_ROWS', base_config.preview_rows)
        except Exception:
            pass
        
        self.config = base_config

    def process_path(self, path: str) -> Dict[str, Any]:
        with open(path, "rb") as f:
            return self.process_bytes(f.read(), filename=path)

    def process_bytes(self, file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
        filename = filename or "table_attachment"
        lower = filename.lower()

        if lower.endswith((".xlsx", ".xlsm")):
            return self._process_xlsx(file_bytes, filename)
        if lower.endswith(".csv"):
            return self._process_delimited(file_bytes, filename, delimiter=",")
        if lower.endswith(".tsv"):
            return self._process_delimited(file_bytes, filename, delimiter="\t")

        # best-effort: try excel then csv
        try:
            return self._process_xlsx(file_bytes, filename)
        except Exception:
            try:
                return self._process_delimited(file_bytes, filename, delimiter=_guess_delimiter(filename))
            except Exception as e:
                logger.exception("TablesProcessor failed to parse bytes")
                return {
                    "ok": False,
                    "filename": filename,
                    "kind": "unknown",
                    "warnings": [f"Unsupported or unreadable table file: {e}"],
                    "sheets": [],
                    "combined_text": "",
                }

    # -------------------------
    # NEW: public method for programmatic use
    # -------------------------

    def summarize_dataframe(self, df: pd.DataFrame, name: str = "Sheet1") -> Dict[str, Any]:
        """
        Summarize a DataFrame without file I/O.
        
        This is useful for PDF table parsing and other programmatic uses.
        
        Args:
            df: Pandas DataFrame to summarize
            name: Name to use for the sheet/table
            
        Returns:
            dict with same structure as _summarize_df output
        """
        return self._summarize_df(df, sheet_name=name)

    # -------------------------
    # format handlers
    # -------------------------

    def _process_delimited(self, file_bytes: bytes, filename: str, delimiter: str = ",") -> Dict[str, Any]:
        warnings: List[str] = []

        def _read_csv(header: Any):
            bio = io.BytesIO(file_bytes)
            return pd.read_csv(bio, sep=delimiter, engine="python", header=header)

        try:
            df = _read_csv(header="infer")
        except Exception:
            # encoding fallback (common in customer dumps)
            try:
                bio = io.BytesIO(file_bytes)
                df = pd.read_csv(bio, sep=delimiter, engine="python", encoding="latin-1")
                warnings.append("CSV encoding fallback used: latin-1")
            except Exception as e2:
                return {
                    "ok": False,
                    "filename": filename,
                    "kind": "csv" if delimiter == "," else "tsv",
                    "warnings": [f"Failed to parse delimited file: {e2}"],
                    "sheets": [],
                    "combined_text": "",
                }

        # header recovery for messy CSV
        if _looks_like_bad_header(list(df.columns)):
            try:
                raw_head = pd.read_csv(io.BytesIO(file_bytes), sep=delimiter, engine="python", header=None, nrows=self.config.header_scan_rows)
                best = _find_best_header_row(raw_head, max_scan_rows=self.config.header_scan_rows)
                if best is not None:
                    df = _read_csv(header=best)
                    warnings.append(f"Header auto-detected from row {best + 1} (1-indexed).")
            except Exception as e:
                warnings.append(f"Header auto-detect failed (kept original header): {e}")

        sheet_info = self._summarize_df(df, sheet_name="Sheet1")
        combined_text = self._build_combined_text(filename, "csv" if delimiter == "," else "tsv", [sheet_info])

        return {
            "ok": True,
            "filename": filename,
            "kind": "csv" if delimiter == "," else "tsv",
            "warnings": warnings + sheet_info.get("warnings", []),
            "sheets": [sheet_info],
            "combined_text": combined_text,
        }

    def _process_xlsx(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        warnings: List[str] = []
        sheets: List[Dict[str, Any]] = []

        try:
            xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
        except Exception as e:
            return {
                "ok": False,
                "filename": filename,
                "kind": "xlsx",
                "warnings": [f"Failed to open xlsx: {e}"],
                "sheets": [],
                "combined_text": "",
            }

        sheet_names = xls.sheet_names[: self.config.max_sheets]
        if len(xls.sheet_names) > self.config.max_sheets:
            warnings.append(f"Too many sheets ({len(xls.sheet_names)}). Only processing first {self.config.max_sheets}.")

        for sh in sheet_names:
            try:
                df = pd.read_excel(xls, sheet_name=sh)
            except Exception as e:
                sheets.append({"sheet": sh, "ok": False, "warnings": [f"Failed reading sheet '{sh}': {e}"]})
                continue

            # header recovery for messy Excel
            if _looks_like_bad_header(list(df.columns)):
                try:
                    raw_head = pd.read_excel(xls, sheet_name=sh, header=None, nrows=self.config.header_scan_rows)
                    best = _find_best_header_row(raw_head, max_scan_rows=self.config.header_scan_rows)
                    if best is not None:
                        df = pd.read_excel(xls, sheet_name=sh, header=best)
                        warnings.append(f"Sheet '{sh}': header auto-detected from row {best + 1} (1-indexed).")
                except Exception as e:
                    warnings.append(f"Sheet '{sh}': header auto-detect failed (kept original header): {e}")

            sheets.append(self._summarize_df(df, sheet_name=sh))

        combined_text = self._build_combined_text(filename, "xlsx", sheets)

        all_warnings = warnings[:]
        for s in sheets:
            all_warnings.extend(s.get("warnings", []))

        return {
            "ok": True,
            "filename": filename,
            "kind": "xlsx",
            "warnings": all_warnings,
            "sheets": sheets,
            "combined_text": combined_text,
        }

    # -------------------------
    # summarization
    # -------------------------

    def _summarize_df(self, df: pd.DataFrame, sheet_name: str) -> Dict[str, Any]:
        warnings: List[str] = []

        if df is None or df.empty:
            return {
                "sheet": sheet_name,
                "ok": True,
                "shape": (0, 0),
                "columns": [],
                "key_columns": {},
                "preview_markdown": "",
                "anomalies": [],
                "warnings": ["Sheet appears empty."],
            }

        df2 = df.copy()
        df2 = df2.dropna(axis=1, how="all").dropna(axis=0, how="all")

        # normalize + make columns unique
        df2.columns = _make_unique_columns([str(c).strip() for c in df2.columns])

        if df2.shape[1] == 0:
            return {
                "sheet": sheet_name,
                "ok": True,
                "shape": (int(df2.shape[0]), 0),
                "columns": [],
                "key_columns": {},
                "preview_markdown": "",
                "anomalies": [],
                "warnings": ["No usable columns after cleanup."],
            }

        if _looks_like_bad_header(list(df2.columns)):
            warnings.append("Columns look auto-generated; header might be imperfect (file may have merged/blank header rows).")

        columns = list(df2.columns)
        key_columns = _detect_semantic_columns(columns)

        # compute signals using a performance-safe sample
        rows = int(df2.shape[0])
        use_full = rows <= self.config.max_rows_full_scan

        if not use_full:
            warnings.append(f"Large sheet ({rows} rows). Stats computed on a random sample of {min(self.config.sample_rows_for_stats, rows)} rows.")
            df_stats = df2.sample(n=min(self.config.sample_rows_for_stats, rows), random_state=42)
        else:
            df_stats = df2

        anomalies = self._detect_generic_signals(df_stats, df_full=df2 if use_full else None)

        preview = ""
        if self.config.include_preview:
            preview = _df_to_markdown(df2, max_rows=self.config.preview_rows, max_cols=self.config.max_preview_cols)
            preview = _clip_text(preview, self.config.max_markdown_chars)

        return {
            "sheet": sheet_name,
            "ok": True,
            "shape": (int(df2.shape[0]), int(df2.shape[1])),
            "columns": columns,
            "key_columns": key_columns,
            "preview_markdown": preview,
            "anomalies": anomalies,
            "warnings": warnings,
        }

    def _detect_generic_signals(self, df_stats: pd.DataFrame, df_full: Optional[pd.DataFrame] = None) -> List[str]:
        """Generic, schema-driven signals (no domain assumptions)."""
        signals: List[str] = []

        if df_stats is None or df_stats.empty:
            return signals

        # --- missingness ---
        try:
            miss = df_stats.isna().mean().sort_values(ascending=False)
            heavy = miss[miss >= 0.5]
            if not heavy.empty:
                top = ", ".join([f"{c} ({int(p*100)}%)" for c, p in heavy.head(6).items()])
                signals.append(f"High missingness columns (>=50%): {top}.")
        except Exception:
            pass

        # --- duplicates (full scan only if safe) ---
        try:
            if df_full is not None and not df_full.empty:
                dup = int(df_full.duplicated().sum())
                if dup:
                    signals.append(f"Duplicate rows detected: {dup}.")
            else:
                dup_s = int(df_stats.duplicated().sum())
                if dup_s:
                    signals.append(f"Duplicate rows detected (sample): {dup_s}.")
        except Exception:
            pass

        # --- infer datetimes (lightweight) ---
        date_cols: List[str] = []
        try:
            for c in df_stats.columns:
                if pd.api.types.is_datetime64_any_dtype(df_stats[c]):
                    date_cols.append(c)
                    continue

                if pd.api.types.is_object_dtype(df_stats[c]) or pd.api.types.is_string_dtype(df_stats[c]):
                    s = df_stats[c].dropna().astype(str)
                    if s.empty:
                        continue
                    probe = s.head(200)
                    parsed = pd.to_datetime(probe, errors="coerce", dayfirst=True, utc=False)
                    ok_ratio = float(parsed.notna().mean()) if len(parsed) else 0.0
                    if ok_ratio >= 0.7:
                        date_cols.append(c)

            date_cols = date_cols[:6]
            for c in date_cols:
                parsed_full = pd.to_datetime(df_stats[c], errors="coerce", dayfirst=True, utc=False)
                if parsed_full.notna().any():
                    dmin = parsed_full.min()
                    dmax = parsed_full.max()
                    signals.append(f"Date range ({c}): {dmin} → {dmax}.")
        except Exception:
            pass

        # --- numeric summaries + outliers (generic) ---
        try:
            num_cols = [c for c in df_stats.columns if pd.api.types.is_numeric_dtype(df_stats[c])]
            # also try to coerce a few object columns into numeric
            if len(num_cols) < self.config.max_numeric_cols:
                for c in df_stats.columns:
                    if c in num_cols:
                        continue
                    if pd.api.types.is_object_dtype(df_stats[c]) or pd.api.types.is_string_dtype(df_stats[c]):
                        coerced = pd.to_numeric(df_stats[c], errors="coerce")
                        if coerced.notna().mean() >= 0.8 and coerced.notna().sum() >= 10:
                            df_stats[c] = coerced
                            num_cols.append(c)
                    if len(num_cols) >= self.config.max_numeric_cols:
                        break

            num_cols = num_cols[: self.config.max_numeric_cols]

            for c in num_cols:
                s = pd.to_numeric(df_stats[c], errors="coerce")
                s = s.dropna()
                if s.empty:
                    continue

                q1 = float(s.quantile(0.25))
                q3 = float(s.quantile(0.75))
                iqr = q3 - q1
                if iqr == 0:
                    out_count = int((s != float(s.median())).sum())
                else:
                    lo = q1 - 1.5 * iqr
                    hi = q3 + 1.5 * iqr
                    out_count = int(((s < lo) | (s > hi)).sum())

                signals.append(
                    f"Numeric ({c}): min={float(s.min()):.6g}, max={float(s.max()):.6g}, median={float(s.median()):.6g}, outliers(IQR)~{out_count}."
                )

            # show a few outlier rows for the single most "spiky" numeric column
            if num_cols:
                best_col = None
                best_out = 0
                for c in num_cols:
                    s = pd.to_numeric(df_stats[c], errors="coerce").dropna()
                    if s.empty:
                        continue
                    q1 = float(s.quantile(0.25))
                    q3 = float(s.quantile(0.75))
                    iqr = q3 - q1
                    if iqr == 0:
                        out_count = int((s != float(s.median())).sum())
                    else:
                        lo = q1 - 1.5 * iqr
                        hi = q3 + 1.5 * iqr
                        out_count = int(((s < lo) | (s > hi)).sum())
                    if out_count > best_out:
                        best_out = out_count
                        best_col = c

                if best_col and best_out:
                    s = pd.to_numeric(df_stats[best_col], errors="coerce")
                    q1 = float(s.quantile(0.25))
                    q3 = float(s.quantile(0.75))
                    iqr = q3 - q1
                    if iqr != 0:
                        lo = q1 - 1.5 * iqr
                        hi = q3 + 1.5 * iqr
                        out_df = df_stats[(s < lo) | (s > hi)].copy()
                        if not out_df.empty:
                            # choose a few identifier columns (short strings / low missing)
                            id_cols = []
                            for c in df_stats.columns:
                                if c == best_col:
                                    continue
                                if pd.api.types.is_object_dtype(df_stats[c]) or pd.api.types.is_string_dtype(df_stats[c]):
                                    if df_stats[c].notna().mean() >= 0.7:
                                        id_cols.append(c)
                                if len(id_cols) >= 3:
                                    break

                            show_cols = (id_cols + [best_col])[: self.config.max_preview_cols]
                            out_df = out_df.head(self.config.max_outlier_rows)
                            signals.append("Example outlier rows (IQR) for column '" + best_col + "':\n" + _df_to_markdown(out_df[show_cols], max_rows=self.config.max_outlier_rows, max_cols=self.config.max_preview_cols))
        except Exception:
            pass

        # --- categorical top values (generic) ---
        try:
            cat_cols: List[str] = []
            for c in df_stats.columns:
                if c in date_cols:
                    continue
                if pd.api.types.is_object_dtype(df_stats[c]) or pd.api.types.is_string_dtype(df_stats[c]):
                    cat_cols.append(c)

            cat_cols = cat_cols[: self.config.max_categorical_cols]
            for c in cat_cols:
                s = df_stats[c].dropna().astype(str)
                if s.empty:
                    continue
                nunique = int(s.nunique(dropna=True))
                # only include if column is not extremely high-cardinality
                if nunique <= 50:
                    vc = s.value_counts().head(self.config.top_values_per_col)
                    top = ", ".join([f"{k} ({int(v)})" for k, v in vc.items()])
                    signals.append(f"Top values ({c}): {top}.")
                elif nunique >= max(100, int(len(s) * 0.9)):
                    signals.append(f"Column '{c}' looks like an identifier/high-cardinality field (unique~{nunique}).")
        except Exception:
            pass

        return [s for s in signals if s]

    # -------------------------
    # combined text builder (refactored for conciseness)
    # -------------------------

    def _build_combined_text(self, filename: str, kind: str, sheets: List[Dict[str, Any]]) -> str:
        cfg = self.config

        parts: List[str] = [f"[Attachment: {filename}] ({kind})"]

        for s in sheets:
            if not s.get("ok", True):
                parts.append(f"- Sheet '{s.get('sheet', 'unknown')}' failed: {', '.join(s.get('warnings', []))}")
                continue

            sheet = s.get("sheet", "Sheet")
            rows, cols = s.get("shape", (0, 0))
            
            # Concise header
            parts.append(f"\nSheet: {sheet} | {rows} rows, {cols} cols")

            # Column list (abbreviated)
            columns = s.get("columns", [])
            if columns:
                col_preview = ", ".join(columns[:12])
                if len(columns) > 12:
                    col_preview += f", ... ({len(columns)} total)"
                parts.append(f"Columns: {col_preview}")

            # Key column guess
            key_cols = s.get("key_columns", {})
            if key_cols:
                key_text = "; ".join([f"{k}=[{', '.join(v[:3])}{'...' if len(v) > 3 else ''}]" for k, v in sorted(key_cols.items())])
                parts.append(f"Key columns guess: {key_text}")

            # Signals (concise bullet list)
            anomalies = s.get("anomalies", [])
            if anomalies:
                parts.append("\nSignals:")
                for a in anomalies[:8]:  # Limit to top 8 signals
                    parts.append(f"- {a}")
                if len(anomalies) > 8:
                    parts.append(f"- ... ({len(anomalies) - 8} more signals)")

            # Preview only if enabled
            if cfg.include_preview:
                prev = s.get("preview_markdown", "")
                if prev:
                    parts.append(f"\nPreview (top {cfg.preview_rows} rows):")
                    parts.append(prev)

        combined = "\n".join(parts).strip() + "\n"
        return _clip_text(combined, cfg.max_combined_chars)