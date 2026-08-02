"""OpenRouter-client: prijzen opvragen en een deel laten opschonen.

OpenRouter spreekt de OpenAI-compatibele API; we gebruiken gewoon `requests`
via de gedeelde sessie (zonder automatische retries, zie `mdconv.net`).
"""

from __future__ import annotations

import re
import threading
import time

import requests

from .. import net
from ..errors import ConfigError, ConversionError
from . import config, prompts

# Het obsidian-profiel levert zijn antwoord in één ```markdown-codeblok; dat
# eraf halen zodat de editor platte Markdown toont en geen codeblok.
_FENCE_RE = re.compile(r"^```(?:markdown)?\s*\n(.*?)\n```\s*$", re.S)

_REQUEST_TIMEOUT = 600          # opschonen van een groot deel kan minuten duren
_PRICING_TTL = 3600             # prijzen veranderen zelden; één uur is ruim
_PRICING_TIMEOUT = 20

_pricing_lock = threading.Lock()
_pricing_cache: dict[str, tuple[float, dict | None]] = {}


def strip_markdown_fence(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text


def get_pricing(model: str) -> dict | None:
    """{'prompt': $/token, 'completion': $/token} voor een model, of None.

    De catalogus van OpenRouter kent alleen kale model-id's: een routeringssuffix
    als ":nitro" of ":floor" staat er niet als apart item in (het kiest een
    provider, niet een ander geprijsd model). Daarom matchen we ook op het id
    zonder dat suffix — anders zou elk :nitro-model als "prijs onbekend"
    verschijnen.

    Het resultaat wordt een uur gecachet, inclusief een mislukte poging, zodat
    de kostenraming niet bij elke toetsaanslag het netwerk op gaat.
    """
    now = time.monotonic()
    with _pricing_lock:
        cached = _pricing_cache.get(model)
        if cached and now - cached[0] < _PRICING_TTL:
            return cached[1]

    result = _fetch_pricing(model)
    with _pricing_lock:
        _pricing_cache[model] = (now, result)
    return result


def _fetch_pricing(model: str) -> dict | None:
    base_id = model.split(":", 1)[0]
    try:
        resp = net.llm().get(f"{config.base_url()}/models", timeout=_PRICING_TIMEOUT)
        if resp.status_code != 200:
            return None
        for entry in resp.json().get("data", []):
            if entry.get("id") in (model, base_id):
                p = entry.get("pricing") or {}
                return {
                    "prompt": float(p.get("prompt", 0) or 0),
                    "completion": float(p.get("completion", 0) or 0),
                }
    except Exception:  # noqa: BLE001 — geen prijs is niet fataal
        return None
    return None


def clear_pricing_cache() -> None:
    with _pricing_lock:
        _pricing_cache.clear()


def clean_chunk(chunk: str, *, model: str, system: str, profile: str) -> str:
    """Laat één deel opschonen en geef de opgeschoonde Markdown terug."""
    key = config.api_key()
    if not key:
        raise ConfigError(
            "AI-opschoning niet beschikbaar: geen OpenRouter API-sleutel. "
            "Zet de omgevingsvariabele OPENROUTER_API_KEY en herstart de tool."
        )

    template = prompts.USER_PROMPTS.get(profile, prompts.DEFAULT_USER_PROMPT)
    try:
        resp = net.llm().post(
            f"{config.base_url()}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                # Attributie-header die OpenRouter voor zijn ranglijst gebruikt.
                "X-Title": "Markdown converter",
            },
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": config.MAX_OUTPUT_TOKENS,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": template.format(chunk=chunk)},
                ],
            },
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise ConversionError(f"AI-opschoning mislukt (verbindingsfout): {e}") from e

    if resp.status_code == 401:
        raise ConfigError(
            "AI-opschoning niet beschikbaar: ongeldige of ontbrekende OpenRouter API-sleutel. "
            "Controleer OPENROUTER_API_KEY en herstart de tool."
        )
    if resp.status_code != 200:
        raise ConversionError(
            f"AI-opschoning mislukt (OpenRouter {resp.status_code}): {_error_detail(resp)}"
        )

    data = resp.json()
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ConversionError(
            f"AI-opschoning gaf een onverwacht antwoord: {str(data)[:200]}"
        ) from e

    if profile == "obsidian":
        content = strip_markdown_fence(content)
    return content


def _error_detail(resp: requests.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", "") or resp.text[:200]
    except Exception:  # noqa: BLE001
        return resp.text[:200]
