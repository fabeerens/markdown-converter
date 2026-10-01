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
from concurrent.futures import ThreadPoolExecutor

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
# een bewust-trage modus in toom.
_env_dpi = os.environ.get("OCR_DPI")
_DPI = int(_env_dpi) if _env_dpi and _env_dpi.isdigit() else 200
_MAX_PAGES = 100

# Hoeveel verzoeken (elk met `config.get_ocr_pages_per_request()` pagina's)
# tegelijk lopen. Meer dan een paar lokt rate-limiting uit; `OCR_PARALLEL` stelt
# het bij. Zelfde idee als `cleanup._MAX_PARALLEL_CHUNKS`.
_env_par = os.environ.get("OCR_PARALLEL")
_MAX_PARALLEL_BATCHES = int(_env_par) if _env_par and _env_par.isdigit() else 3


def _sum_usage(usages: list[dict]) -> dict:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
    for u in usages:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[key] += int(u.get(key, 0) or 0)
        total["cost"] += float(u.get("cost", 0) or 0)
    return total


def _batches(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def ocr_pdf_stream(
    pdf_bytes: bytes, *, model: str | None = None, request_id: str | None = None
) -> Iterator[str | Usage | Progress]:
    """Transcribeer `pdf_bytes` en lever de Markdown streamend op.

    De pagina's gaan in groepen van `config.get_ocr_pages_per_request()` naar
    het model (minder round-trips, minder rate-limit-druk); tot
    `_MAX_PARALLEL_BATCHES` van die verzoeken lopen **parallel**, maar de tekst
    komt **in documentvolgorde** naar buiten — een groep die eerder klaar is
    wacht op zijn beurt. Per groep één `Progress`-marker (pagina's tot nu toe),
    één opgeteld `Usage` aan het eind. `request_id` geeft `/api/clean/cancel`
    een aangrijpingspunt om stil (geen fout) te stoppen; een fout halverwege
    stopt de nog lopende groepen ook.
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
    groups = _batches(pages, config.get_ocr_pages_per_request())
    totals: list[dict] = []

    def run_group(images: list[bytes]) -> tuple[str, dict | None]:
        text_parts: list[str] = []
        usage: dict | None = None
        for piece in openrouter.ocr_pages_stream(
            images, model=resolved, system=system, request_id=request_id
        ):
            if isinstance(piece, openrouter.Usage):
                usage = piece
            else:
                text_parts.append(piece)
        return "".join(text_parts).strip(), usage

    _cancel.clear(request_id)
    done_pages = 0
    try:
        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_BATCHES, len(groups))) as pool:
            futures = [pool.submit(run_group, g) for g in groups]
            try:
                for i, (fut, group) in enumerate(zip(futures, groups)):
                    if _cancel.is_cancelled(request_id):
                        return
                    text, usage = fut.result()
                    if i > 0 and text:
                        yield "\n\n"
                    if text:
                        yield text
                    if usage is not None:
                        totals.append(usage)
                    done_pages += len(group)
                    yield openrouter.Progress(produced_tokens=done_pages, expected_tokens=total)
            except BaseException:
                # Fout, client weg, of annulering: de nog lopende groepen bij de
                # eerstvolgende SSE-regel laten stoppen i.p.v. door te betalen.
                _cancel.request(request_id)
                raise
        if _cancel.is_cancelled(request_id):
            return
        yield openrouter.Usage(_sum_usage(totals))
    finally:
        _cancel.clear(request_id)
