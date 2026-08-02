"""AI-opschoning: kostenraming en het daadwerkelijk opschonen.

Publieke ingangen: `estimate()` voor de raming die de UI toont, en `clean()`
voor het echte werk. De rest van de app hoeft niets van OpenRouter, chunking of
profielen te weten.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..errors import ConversionError
from . import chunking, config, openrouter, prompts

# Publieke namen die de API-laag gebruikt.
PROFILES = prompts.PROFILES
get_model_choices = config.get_model_choices
get_chunk_tokens = config.get_chunk_tokens
get_prompt = config.get_prompt
settings_payload = config.settings_payload
update_settings = config.update_settings
is_available = config.is_available

# Meer dan een paar gelijktijdige verzoeken heeft geen zin: OpenRouter
# rate-limit't, en bij één document zijn het er meestal toch maar één of twee.
_MAX_PARALLEL_CHUNKS = 3


def estimate(markdown: str, profile: str = "generic", model: str | None = None) -> dict:
    """Schat delen, tokens en kosten voor het opschonen van `markdown`.

    Tokenaantallen zijn benaderingen (≈ 4 tekens per token): genoeg om de kosten
    in te schatten, geen exacte facturering.
    """
    chunks = chunking.chunks_for(markdown, profile)
    n = len(chunks)

    system_tokens = len(config.get_prompt(profile)) // config.CHARS_PER_TOKEN
    content_tokens = sum(len(c) for c in chunks) // config.CHARS_PER_TOKEN
    # Elk deel stuurt de systeemprompt opnieuw mee, plus wat berichtoverhead.
    input_tokens = content_tokens + (system_tokens + 20) * n
    output_tokens = int(content_tokens * config.OUTPUT_RATIO.get(profile, 1.0))

    resolved = config.resolve_model(model)
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


def clean(markdown: str, profile: str = "generic", model: str | None = None) -> str:
    """Schoon `markdown` op met het gekozen profiel en model.

    Bij meerdere delen worden die parallel verwerkt en daarna in de
    oorspronkelijke volgorde weer aan elkaar geplakt — dat scheelt bij een lang
    document reële wachttijd, omdat elk deel een aparte, minuten durende
    API-aanroep is.
    """
    if not markdown.strip():
        return markdown

    if not config.is_available():
        raise ConversionError(
            "AI-opschoning niet beschikbaar: geen OpenRouter API-sleutel. "
            "Zet de omgevingsvariabele OPENROUTER_API_KEY en herstart de tool."
        )

    resolved = config.resolve_model(model)
    system = config.get_prompt(profile)
    chunks = chunking.chunks_for(markdown, profile)

    def run(chunk: str) -> str:
        return openrouter.clean_chunk(chunk, model=resolved, system=system, profile=profile)

    if len(chunks) == 1:
        cleaned = [run(chunks[0])]
    else:
        workers = min(_MAX_PARALLEL_CHUNKS, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # `map` behoudt de volgorde, dus de delen komen goed terug.
            cleaned = list(pool.map(run, chunks))

    return "\n\n".join(c for c in cleaned if c).strip() + "\n"
