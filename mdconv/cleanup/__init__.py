"""AI-opschoning: kostenraming en het daadwerkelijk opschonen.

Publieke ingangen: `estimate()` voor de raming die de UI toont, en `clean()`
voor het echte werk. De rest van de app hoeft niets van OpenRouter, chunking of
profielen te weten.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from ..errors import ConversionError
from . import cancel as _cancel
from . import chunking, config, openrouter, prompts

# Publieke namen die de API-laag gebruikt.
PROFILES = prompts.PROFILES
get_model_choices = config.get_model_choices
get_chunk_tokens = config.get_chunk_tokens
get_prompt = config.get_prompt
settings_payload = config.settings_payload
update_settings = config.update_settings
is_available = config.is_available
cancel_request = _cancel.request
Usage = openrouter.Usage
Progress = openrouter.Progress

# Meer dan een paar gelijktijdige verzoeken heeft geen zin: OpenRouter
# rate-limit't, en bij één document zijn het er meestal toch maar één of twee.
_MAX_PARALLEL_CHUNKS = 3

# Hoeveel tekens er tussen twee voortgangsmeldingen moeten zijn geproduceerd
# (streaming). Te vaak = onnodig veel frames door de body; te grof = een
# voortgangsbalk die met sprongen beweegt.
_PROGRESS_STEP_CHARS = 400


def estimate(markdown: str, profile: str = "generic", model: str | None = None) -> dict:
    """Schat delen, tokens en kosten voor het opschonen van `markdown`.

    Tokenaantallen zijn benaderingen (≈ 4 tekens per token): genoeg om de kosten
    in te schatten, geen exacte facturering.
    """
    resolved = config.resolve_model(model)
    chunks = chunking.chunks_for(markdown, profile, resolved)
    n = len(chunks)

    system_tokens = len(config.get_prompt(profile)) // config.CHARS_PER_TOKEN
    content_tokens = sum(len(c) for c in chunks) // config.CHARS_PER_TOKEN
    # Elk deel stuurt de systeemprompt opnieuw mee, plus wat berichtoverhead.
    input_tokens = content_tokens + (system_tokens + 20) * n
    output_tokens = int(content_tokens * config.OUTPUT_RATIO.get(profile, 1.0))

    pricing = openrouter.get_pricing(resolved) if config.is_available() else None
    cost = None
    if pricing:
        cost = input_tokens * pricing["prompt"] + output_tokens * pricing["completion"]

    return {
        "available": config.is_available(),
        "model": resolved,
        "chunks": n,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "pricing": pricing,
        "cost_usd": cost,
    }


def _ensure_available() -> None:
    if not config.is_available():
        raise ConversionError(
            "AI-opschoning niet beschikbaar: geen OpenRouter API-sleutel. "
            "Zet de omgevingsvariabele OPENROUTER_API_KEY en herstart de tool."
        )


def _sum_usage(usages) -> dict:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
    for u in usages:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[key] += int(u.get(key, 0) or 0)
        total["cost"] += float(u.get("cost", 0) or 0)
    return total


def clean(markdown: str, profile: str = "generic", model: str | None = None) -> tuple[str, dict]:
    """Schoon `markdown` op met het gekozen profiel en model.

    Geeft (opgeschoonde markdown, opgeteld tokengebruik) terug. Bij meerdere
    delen worden die parallel verwerkt en daarna in de oorspronkelijke
    volgorde weer aan elkaar geplakt — dat scheelt bij een lang document
    reële wachttijd, omdat elk deel een aparte, minuten durende API-aanroep is.
    """
    if not markdown.strip():
        return markdown, {}

    _ensure_available()
    resolved = config.resolve_model(model)
    system = config.get_prompt(profile)
    chunks = chunking.chunks_for(markdown, profile, resolved)

    def run(chunk: str) -> tuple[str, dict]:
        return openrouter.clean_chunk(chunk, model=resolved, system=system, profile=profile)

    if len(chunks) == 1:
        results = [run(chunks[0])]
    else:
        workers = min(_MAX_PARALLEL_CHUNKS, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # `map` behoudt de volgorde, dus de delen komen goed terug.
            results = list(pool.map(run, chunks))

    cleaned = "\n\n".join(c for c, _ in results if c).strip() + "\n"
    return cleaned, _sum_usage(u for _, u in results)


def clean_stream(
    markdown: str, profile: str = "generic", model: str | None = None, request_id: str | None = None
) -> Iterator[str | openrouter.Usage | openrouter.Progress]:
    """Als `clean()`, maar levert de opgeschoonde tekst als een reeks stukjes
    op, zoals het model ze genereert — met tussendoor `Progress`-markers en
    aan het eind één opgeteld `Usage`-marker.

    Bij meerdere delen worden die **na elkaar** gestreamd, niet parallel zoals
    in `clean()`: bij live meelezen moet de tekst van boven naar onder groeien
    in documentvolgorde. Dat kost bij een lang document iets meer wachttijd in
    ruil voor een bruikbare live-weergave.

    `request_id` is optioneel: geeft de aanroeper (`/api/clean/cancel`) een
    aangrijpingspunt om deze generator vroegtijdig — en stil, geen fout — te
    laten stoppen. Zonder `request_id` kan niet geannuleerd worden.
    """
    if not markdown.strip():
        return

    _ensure_available()
    resolved = config.resolve_model(model)
    system = config.get_prompt(profile)
    chunks = chunking.chunks_for(markdown, profile, resolved)

    # Verwachte totale uitvoer, voor de voortgangsbalk: dezelfde schatting als
    # estimate() gebruikt voor de kostenraming (invoergrootte × OUTPUT_RATIO).
    total_input_chars = sum(len(c) for c in chunks)
    expected_output_tokens = max(
        1, round(total_input_chars * config.OUTPUT_RATIO.get(profile, 1.0) / config.CHARS_PER_TOKEN)
    )
    produced_chars = 0
    last_reported_chars = 0
    totals: list[dict] = []

    _cancel.clear(request_id)
    try:
        for i, chunk in enumerate(chunks):
            if _cancel.is_cancelled(request_id):
                return
            if i > 0:
                yield "\n\n"
            pieces = openrouter.stream_chunk(
                chunk, model=resolved, system=system, profile=profile, request_id=request_id
            )
            if profile == "obsidian":
                pieces = openrouter.strip_fence_stream(pieces)
            for piece in pieces:
                if isinstance(piece, openrouter.Usage):
                    totals.append(piece)
                    continue
                produced_chars += len(piece)
                yield piece
                if produced_chars - last_reported_chars >= _PROGRESS_STEP_CHARS:
                    last_reported_chars = produced_chars
                    yield openrouter.Progress(
                        produced_tokens=produced_chars // config.CHARS_PER_TOKEN,
                        expected_tokens=expected_output_tokens,
                    )
        if _cancel.is_cancelled(request_id):
            return
        yield openrouter.Usage(_sum_usage(totals))
    finally:
        _cancel.clear(request_id)
