"""Convert arbitrary document formats to Markdown.

Used as the fallback for the file-upload route: EUR-Lex Formex XML gets the
dedicated structural parser (converters/formex.py). PDF's go through
**pdf-inspector** first (layout-aware Markdown, no reflow-hack needed);
everything else — and any PDF pdf-inspector can't handle (scanned/image-only,
or a parse error) — falls back to **Microsoft MarkItDown**.

PDF (and plain-text) extraction via MarkItDown inserts a hard line break at
every visual line, so a single paragraph arrives split across many lines.
`_reflow` re-joins those soft-wrapped lines back into paragraphs. pdf-inspector
does its own layout-aware Markdown conversion, so its output does not need
this treatment.
"""

from __future__ import annotations

import io
import os
import re

from markitdown import MarkItDown

try:
    import pdf_inspector
except ImportError:  # pragma: no cover
    pdf_inspector = None

# One shared instance; MarkItDown is stateless per-conversion.
_ENGINE = MarkItDown()

# Extensions MarkItDown can meaningfully convert. Advertised to the UI.
SUPPORTED_EXTENSIONS = [
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml", ".txt", ".md",
    ".epub", ".rtf", ".msg",
]

# Formats whose text is extracted line-by-line and benefits from reflowing.
_REFLOW_EXTENSIONS = {".pdf", ".txt"}

# pdf-inspector's own classification for a PDF with no usable text layer —
# for these we fall back to MarkItDown rather than return empty/garbage output.
_NO_TEXT_LAYER = {"scanned", "image_based"}


def _convert_pdf_with_pdf_inspector(data: bytes) -> str | None:
    """Try pdf-inspector for a text-based PDF. None = fall back to MarkItDown."""
    if pdf_inspector is None:
        return None
    try:
        result = pdf_inspector.process_pdf_bytes(data)
    except Exception:  # noqa: BLE001
        return None
    if result.pdf_type in _NO_TEXT_LAYER:
        return None
    markdown = (result.markdown or "").strip()
    return markdown + "\n" if markdown else None


def convert_with_markitdown(data: bytes, filename: str = "") -> tuple[str, str]:
    """Convert raw file bytes to Markdown. Raises ValueError on failure.

    Returns (markdown, engine_label) — the label reflects which engine
    actually produced the output (pdf-inspector vs. MarkItDown), since a PDF
    may fall back from the former to the latter.
    """
    ext = os.path.splitext(filename)[1].lower() or None

    if ext == ".pdf":
        pdf_markdown = _convert_pdf_with_pdf_inspector(data)
        if pdf_markdown is not None:
            return pdf_markdown, "pdf-inspector"

    try:
        result = _ENGINE.convert_stream(io.BytesIO(data), file_extension=ext)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"markitdown kon dit bestand niet omzetten: {e}") from e

    text = (result.text_content or "").strip()
    if not text:
        raise ValueError("Geen tekst uit het bestand kunnen halen.")

    if ext in _REFLOW_EXTENSIONS:
        text = _reflow(text)
    return text.strip() + "\n", "MarkItDown"


# Unmapped glyphs (e.g. bullet characters) that pdfminer emits as "(cid:NNN)".
_CID_RE = re.compile(r"\(cid:\d+\)")

# A line that starts a structural element (heading, list item, table, quote):
# it must never be merged into the previous line, nor swallow the next one.
_STRUCT_RE = re.compile(r"^(#{1,6}\s|[-*•‣◦]\s|\d+[.)]\s|\|\s?|>\s)")


def _reflow(text: str) -> str:
    """Merge soft-wrapped lines within each paragraph back together.

    Paragraphs are delimited by blank lines (which MarkItDown preserves).
    Inside a paragraph, a line break is treated as a soft wrap — and joined —
    only when the line is "full" (close to the paragraph's widest line) and
    neither it nor the next line is a structural element. Short lines
    (headings, list labels, the last line of a paragraph) are left alone.
    """
    text = _CID_RE.sub("", text)
    blocks = re.split(r"\n[ \t]*\n", text)
    out_blocks = []

    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            out_blocks.append(lines[0])
            continue

        # A line is likely soft-wrapped if it reaches most of the block width.
        max_len = max(len(ln) for ln in lines)
        threshold = max(45, int(max_len * 0.6))

        merged = [lines[0]]
        for nxt in lines[1:]:
            cur = merged[-1]
            soft_wrap = (
                len(cur) >= threshold
                and not _STRUCT_RE.match(cur)
                and not _STRUCT_RE.match(nxt)
            )
            if not soft_wrap:
                merged.append(nxt)
                continue
            # Hyphenated word split across lines: glue it, drop the hyphen.
            if cur.endswith("-") and len(cur) >= 2 and cur[-2].isalpha() and nxt[:1].islower():
                merged[-1] = cur[:-1] + nxt
            else:
                merged[-1] = cur + " " + nxt

        out_blocks.append("\n".join(merged))

    return "\n\n".join(out_blocks)
