"""Bestanden → Markdown.

PDF gaat eerst door **pdf-inspector** (Rust-library van Firecrawl): dat levert
layout-bewuste Markdown met koppen, lijsten en tabellen, en heeft de
regeleinde-reflow van hieronder niet nodig. Heeft de PDF geen tekstlaag
(gescand/afbeelding) of mislukt de extractie, dan valt de conversie terug op
**MarkItDown** — dat doet evenmin OCR, maar het is het bestaande gedrag.

Alle andere formaten (Word, Excel, PowerPoint, HTML, CSV, JSON, EPUB, …) gaan
altijd via MarkItDown; pdf-inspector kent alleen PDF.

Beide engines worden **lui** geladen: `import markitdown` kost honderden
milliseconden en trekt een flinke afhankelijkhedenboom mee. Dat gebeurde eerder
bij het importeren van de app, waardoor elke serverstart erop wachtte — ook als
er nooit een bestand werd geüpload.
"""

from __future__ import annotations

import io
import os
import re
import threading

from ..errors import ConversionError

# Extensies die MarkItDown zinnig kan omzetten; wordt aan de UI getoond.
SUPPORTED_EXTENSIONS = [
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml", ".txt", ".md",
    ".epub", ".rtf", ".msg",
]

# Formaten waarvan de tekst regel-per-regel wordt geëxtraheerd en die dus
# gereflowd moeten worden. PDF staat hier voor de MarkItDown-terugval;
# pdf-inspector levert al nette alinea's.
_REFLOW_EXTENSIONS = {".pdf", ".txt"}

# pdf-inspector's classificatie voor een PDF zonder bruikbare tekstlaag.
_NO_TEXT_LAYER = {"scanned", "image_based"}

ENGINE_PDF_INSPECTOR = "pdf-inspector"
ENGINE_MARKITDOWN = "MarkItDown"

_engine_lock = threading.Lock()
_markitdown = None


def _markitdown_engine():
    """De gedeelde MarkItDown-instantie; pas bij eerste gebruik geladen."""
    global _markitdown
    if _markitdown is None:
        with _engine_lock:
            if _markitdown is None:
                from markitdown import MarkItDown
                _markitdown = MarkItDown()
    return _markitdown


def convert(data: bytes, filename: str = "") -> tuple[str, str]:
    """Zet bestandsbytes om naar Markdown.

    Geeft (markdown, engine) terug: welke engine het daadwerkelijk deed, zodat
    de UI dat in het bronveld kan tonen. Gooit `ConversionError` als er geen
    tekst uit te halen is.
    """
    ext = os.path.splitext(filename)[1].lower() or None

    if ext == ".pdf":
        markdown = _convert_pdf(data)
        if markdown is not None:
            return markdown, ENGINE_PDF_INSPECTOR

    try:
        result = _markitdown_engine().convert_stream(io.BytesIO(data), file_extension=ext)
    except Exception as e:  # noqa: BLE001 — MarkItDown gooit uiteenlopende fouten
        raise ConversionError(f"markitdown kon dit bestand niet omzetten: {e}") from e

    text = (result.text_content or "").strip()
    if not text:
        raise ConversionError("Geen tekst uit het bestand kunnen halen.")

    if ext in _REFLOW_EXTENSIONS:
        text = reflow(text)
    return text.strip() + "\n", ENGINE_MARKITDOWN


def _convert_pdf(data: bytes) -> str | None:
    """PDF via pdf-inspector. None betekent: val terug op MarkItDown."""
    try:
        import pdf_inspector
    except ImportError:  # pragma: no cover — dependency ontbreekt
        return None
    try:
        result = pdf_inspector.process_pdf_bytes(data)
    except Exception:  # noqa: BLE001 — corrupte PDF: laat MarkItDown het proberen
        return None
    if result.pdf_type in _NO_TEXT_LAYER:
        return None
    markdown = (result.markdown or "").strip()
    return markdown + "\n" if markdown else None


def convert_pdf_pages(data: bytes) -> list[str] | None:
    """Als `_convert_pdf`, maar geeft de Markdown per pagina terug i.p.v.
    samengevoegd — nodig om een afbeelding op de pagina te kunnen plaatsen
    waar hij ook echt uit kwam (`sources._attach_pdf_images_inline`), i.p.v.
    alles onderaan het document te dumpen. None betekent hetzelfde als bij
    `_convert_pdf`: geen bruikbare tekstlaag, of pdf-inspector ontbreekt —
    de aanroeper valt dan terug op de gewone (samengevoegde) route.

    Zelfde classificatie-gate als `_convert_pdf` (`pdf_type` via
    `classify_pdf_bytes`, niet via `process_pdf_bytes`, want dat laatste zou
    de PDF een tweede keer volledig laten extraheren — puur voor de
    ja/nee-vraag "heeft dit een tekstlaag" is de losse, lichte classificatie
    genoeg).
    """
    try:
        import pdf_inspector
    except ImportError:  # pragma: no cover — dependency ontbreekt
        return None
    try:
        classification = pdf_inspector.classify_pdf_bytes(data)
    except Exception:  # noqa: BLE001 — corrupte PDF: laat de aanroeper terugvallen
        return None
    if classification.pdf_type in _NO_TEXT_LAYER:
        return None
    try:
        result = pdf_inspector.extract_pages_markdown_bytes(data)
    except Exception:  # noqa: BLE001
        return None
    # `.page` is 0-gebaseerd en oplopend; de lijstvolgorde is al de juiste
    # paginavolgorde, dus de aanroeper mag gewoon "index + 1" als 1-gebaseerd
    # paginanummer gebruiken (zelfde telling als pdfimages -list).
    pages = [(pm.markdown or "").strip() for pm in result.pages]
    return pages if any(pages) else None


# --------------------------------------------------------------------------
# Reflow: zachte regeleindes binnen een alinea weer samenvoegen
# --------------------------------------------------------------------------

# Niet-gemapte glyphs (bv. bullettekens) die pdfminer als "(cid:NNN)" uitspuugt.
_CID_RE = re.compile(r"\(cid:\d+\)")

# Een regel die een structuurelement begint (kop, lijstitem, tabel, citaat):
# die mag nooit met de vorige regel worden samengevoegd, en niet de volgende
# opslokken.
_STRUCT_RE = re.compile(r"^(#{1,6}\s|[-*•‣◦]\s|\d+[.)]\s|\|\s?|>\s)")

_MIN_WRAP_WIDTH = 45
_WRAP_RATIO = 0.6


def reflow(text: str) -> str:
    """Voeg zacht afgebroken regels binnen elke alinea weer samen.

    Alinea's worden gescheiden door witregels (die MarkItDown behoudt). Binnen
    een alinea geldt een regeleinde als zachte afbreking — en wordt het
    samengevoegd — alleen als de regel "vol" is (dicht bij de breedste regel van
    die alinea) en noch die regel noch de volgende een structuurelement is.
    Korte regels (koppen, lijstlabels, de laatste regel van een alinea) blijven
    dus staan.
    """
    text = _CID_RE.sub("", text)
    out_blocks = []

    for block in re.split(r"\n[ \t]*\n", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            out_blocks.append(lines[0])
            continue

        # Een regel is vermoedelijk zacht afgebroken als hij bijna de volle
        # blokbreedte haalt.
        max_len = max(len(ln) for ln in lines)
        threshold = max(_MIN_WRAP_WIDTH, int(max_len * _WRAP_RATIO))

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
            # Woord over twee regels met een afbreekstreepje: aan elkaar plakken.
            if cur.endswith("-") and len(cur) >= 2 and cur[-2].isalpha() and nxt[:1].islower():
                merged[-1] = cur[:-1] + nxt
            else:
                merged[-1] = cur + " " + nxt

        out_blocks.append("\n".join(merged))

    return "\n\n".join(out_blocks)
