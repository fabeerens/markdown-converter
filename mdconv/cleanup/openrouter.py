"""OpenRouter-client: prijzen opvragen en een deel laten opschonen.

OpenRouter spreekt de OpenAI-compatibele API; we gebruiken gewoon `requests`
via de gedeelde sessie (zonder automatische retries, zie `mdconv.net`).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator

import requests

from .. import net
from ..errors import ConfigError, ConversionError
from . import config, prompts

# Het obsidian-profiel levert zijn antwoord in één ```markdown-codeblok; dat
# eraf halen zodat de editor platte Markdown toont en geen codeblok.
_FENCE_RE = re.compile(r"^```(?:markdown)?\s*\n(.*?)\n```\s*$", re.S)
_FENCE_OPEN_RE = re.compile(r"^```(?:markdown)?\n")

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


def _truncation_message(profile: str) -> str:
    if profile in config.NO_CHUNK_PROFILES:
        return (
            "AI-opschoning mislukt: het antwoord van het model werd afgekapt omdat dit "
            "document te groot is voor het profiel 'Opmaken voor Obsidian' — dat profiel "
            "verwerkt de hele tekst altijd in één stuk, dus de deelgrootte-instelling helpt "
            "hier niet. Gebruik voor dit document het standaardprofiel."
        )
    return (
        "AI-opschoning mislukt: het antwoord van het model werd afgekapt omdat dit deel te "
        "groot is. Verlaag de deelgrootte bij Instellingen en probeer opnieuw."
    )


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
        choice = data["choices"][0]
        content = (choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ConversionError(
            f"AI-opschoning gaf een onverwacht antwoord: {str(data)[:200]}"
        ) from e

    # "length" betekent: het model liep tegen max_tokens aan en is midden in de
    # tekst afgebroken. Dat leverde eerder stilletjes een afgekapt document op
    # — met name bij het obsidian-profiel (dat nooit chunkt) verdween daardoor
    # het laatste deel van een groot document zonder enige melding.
    if choice.get("finish_reason") == "length":
        raise ConversionError(_truncation_message(profile))

    if profile == "obsidian":
        content = strip_markdown_fence(content)
    return content


def stream_chunk(chunk: str, *, model: str, system: str, profile: str) -> Iterator[str]:
    """Als `clean_chunk`, maar levert de tekst als een reeks stukjes op, zoals
    OpenRouter ze genereert (Server-Sent Events, `stream: true`)."""
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
                "X-Title": "Markdown converter",
            },
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": config.MAX_OUTPUT_TOKENS,
                "stream": True,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": template.format(chunk=chunk)},
                ],
            },
            timeout=_REQUEST_TIMEOUT,
            stream=True,
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

    piece = None
    # OpenRouter streamt als text/event-stream zonder expliciete charset; requests
    # valt dan voor text/* terug op ISO-8859-1 (HTTP-standaard). De SSE-payload is
    # echter UTF-8 (JSON met daarin de brontekst), dus zonder deze correctie worden
    # bytes als 0xE2 0x80 0x98 ('U+2018') als latin-1 gelezen → mojibake ('â€˜').
    resp.encoding = "utf-8"
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        choice = (event.get("choices") or [{}])[0]
        delta_piece = choice.get("delta", {}).get("content")
        if delta_piece:
            piece = delta_piece
            yield piece
        # Zie de toelichting bij dezelfde check in clean_chunk() — hier komt
        # finish_reason binnen als aparte SSE-event, los van de laatste delta.
        if choice.get("finish_reason") == "length":
            raise ConversionError(_truncation_message(profile))
    if piece is None:
        # Geen enkele delta binnengekomen: OpenRouter stuurde een lege stream
        # zonder foutstatus. Zonder deze check zou dat stilletjes "niets"
        # opleveren in plaats van een duidelijke melding.
        raise ConversionError("AI-opschoning gaf geen inhoud terug.")


def strip_fence_stream(pieces: Iterator[str]) -> Iterator[str]:
    """Streaming-versie van `strip_markdown_fence`.

    Het obsidian-profiel wrapt zijn antwoord verplicht in één ```markdown-
    codeblok; dat mag niet even zichtbaar zijn tijdens het live meelezen. De
    openingsregel wordt herkend zodra er een newline binnen is (of de buffer
    lang genoeg is om zeker te weten dat het geen fence is); de sluitende
    ``` wordt vastgehouden in een kleine staart totdat de stream stopt, zodat
    hij nooit als tekst wordt doorgestuurd.
    """
    HOLDBACK = 8  # ruim genoeg voor "\n```" plus wat marge
    buf = ""
    started = False
    tail = ""
    for piece in pieces:
        if not started:
            buf += piece
            if "\n" not in buf and len(buf) < 20:
                continue  # nog niet genoeg om de openingsregel te herkennen
            m = _FENCE_OPEN_RE.match(buf)
            tail = buf[m.end():] if m else buf
            started = True
        else:
            tail += piece
        if len(tail) > HOLDBACK:
            yield tail[:-HOLDBACK]
            tail = tail[-HOLDBACK:]
    if not started:
        tail = buf  # de stream stopte al vóór de openingsregel compleet was
    stripped = tail.rstrip()
    if stripped.endswith("```"):
        stripped = stripped[:-3].rstrip()
    if stripped:
        yield stripped


def _error_detail(resp: requests.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", "") or resp.text[:200]
    except Exception:  # noqa: BLE001
        return resp.text[:200]
