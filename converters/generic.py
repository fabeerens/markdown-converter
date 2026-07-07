"""Convert arbitrary document formats to Markdown via Microsoft MarkItDown.

Used as the fallback for the file-upload route: EUR-Lex Formex XML gets the
dedicated structural parser (converters/formex.py); everything else — PDF,
Word, Excel, PowerPoint, HTML, CSV, JSON, EPUB, … — is handled here.

PDF (and plain-text) extraction inserts a hard line break at every visual
line, so a single paragraph arrives split across many lines. `_reflow`
re-joins those soft-wrapped lines back into paragraphs.
"""

from __future__ import annotations

import io
import os
import re

from markitdown import MarkItDown

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


def convert_with_markitdown(data: bytes, filename: str = "") -> str:
    """Convert raw file bytes to Markdown. Raises ValueError on failure."""
    ext = os.path.splitext(filename)[1].lower() or None
    try:
        result = _ENGINE.convert_stream(io.BytesIO(data), file_extension=ext)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"markitdown kon dit bestand niet omzetten: {e}") from e

    text = (result.text_content or "").strip()
    if not text:
        raise ValueError("Geen tekst uit het bestand kunnen halen.")

    if ext in _REFLOW_EXTENSIONS:
        text = _reflow(text)
    return text.strip() + "\n"


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
