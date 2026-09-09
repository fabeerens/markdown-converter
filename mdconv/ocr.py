"""Wiskunde-modus: een PDF pagina-voor-pagina door een vision-LLM naar Markdown.

De gewone tekstextractie (`mdconv/sources/files.py` → pdf-inspector/MarkItDown)
leest de tekstlaag lineair; wiskunde uit een LaTeX/Beamer-PDF komt daar
onbruikbaar uit (sub-/superscripts weg, grote accolades en `\\underbrace` als
glyph-brij, en de index *i* als Private-Use-teken). De semantische wiskunde
staat niet in de tekstlaag — die is alleen terug te halen door de gerenderde
pagina's *visueel* te herlezen.

Dit is een **opt-in** route (checkbox op Documentupload), alleen voor PDF en
alleen met een OpenRouter-sleutel. Elke pagina wordt met poppler (`pdftoppm`,
zie `mdconv/sources/pdf_images.render_pages`) naar een PNG gerasterd en door een
multimodaal model (`DEFAULT_OCR_MODELS` in `cleanup/config.py`) naar Markdown
met LaTeX (`$...$` / `$$...$$`) getranscribeerd. Het resultaat streamt, met
dezelfde annuleer-/voortgangsinfrastructuur als de AI-opschoning
(`mdconv.cleanup.cancel`, de `Progress`/`Usage`-markers).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from .cleanup import cancel as _cancel
from .cleanup import config, openrouter
from .errors import ConversionError
from .sources import pdf_images

Usage = openrouter.Usage
Progress = openrouter.Progress

get_ocr_models = config.get_ocr_models
resolve_ocr_model = config.resolve_ocr_model
is_available = config.is_available

# Rasterresolutie en paginagrens. 200 dpi is genoeg voor kleine sub-/superscripts;
# `OCR_DPI` kan het bijstellen als de kosten/omvang knellen. De paginagrens houdt
# een bewust-trage modus in toom (elke pagina = één apart vision-verzoek).
_env_dpi = os.environ.get("OCR_DPI")
_DPI = int(_env_dpi) if _env_dpi and _env_dpi.isdigit() else 200
_MAX_PAGES = 100


def _sum_usage(usages: list[dict]) -> dict:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
    for u in usages:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[key] += int(u.get(key, 0) or 0)
        total["cost"] += float(u.get("cost", 0) or 0)
    return total


def ocr_pdf_stream(
    pdf_bytes: bytes, *, model: str | None = None, request_id: str | None = None
) -> Iterator[str | Usage | Progress]:
    """Transcribeer elke pagina van `pdf_bytes` en lever de Markdown streamend op.

    Spiegelt `cleanup.clean_stream`: pagina's worden **na elkaar** verwerkt
    (documentvolgorde bij live meelezen), met een `Progress`-marker per pagina
    en één opgeteld `Usage`-marker aan het eind. `request_id` geeft
    `/api/clean/cancel` een aangrijpingspunt om stil (geen fout) te stoppen.
    """
    if not config.is_available():
        raise ConversionError(
            "Wiskunde-modus niet beschikbaar: geen OpenRouter API-sleutel. "
            "Zet de omgevingsvariabele OPENROUTER_API_KEY en herstart de tool."
        )

    resolved = config.resolve_ocr_model(model)
    system = config.get_ocr_prompt()
    pages = pdf_images.render_pages(pdf_bytes, dpi=_DPI, max_pages=_MAX_PAGES)
    total = len(pages)
    totals: list[dict] = []

    _cancel.clear(request_id)
    try:
        for i, png in enumerate(pages):
            if _cancel.is_cancelled(request_id):
                return
            if i > 0:
                yield "\n\n"
            for piece in openrouter.ocr_page_stream(
                png, model=resolved, system=system, request_id=request_id
            ):
                if isinstance(piece, openrouter.Usage):
                    totals.append(piece)
                else:
                    yield piece
            yield openrouter.Progress(produced_tokens=i + 1, expected_tokens=total)
        if _cancel.is_cancelled(request_id):
            return
        yield openrouter.Usage(_sum_usage(totals))
    finally:
        _cancel.clear(request_id)
