"""Instellingen voor de AI-opschoning: ingebouwde standaarden + overschrijvingen.

De gebruiker kan via het instellingenpaneel de modellenlijst en de prompts
aanpassen. Dat wordt opgeslagen in `.deploy-state/settings.json` (buiten git,
in Docker als volume gemount).

**Deelgrootte is per AI-endpoint**, geen centrale instelling: elk model kan
een andere effectieve contextvenster/prijsverhouding hebben, dus staat de
`chunk_tokens`-waarde op het model-rijtje zelf (`models: [{id, label,
chunk_tokens}, …]`) in plaats van als los top-level veld. Een leeg/ontbrekend
`chunk_tokens` op een endpoint valt terug op `DEFAULT_CHUNK_TOKENS` — precies
dezelfde "leeg = standaard"-conventie als de rest van dit bestand.

Twee eigenschappen die het gedrag bepalen:

- **Alleen gewijzigde sleutels staan in het bestand.** Een ontbrekende of lege
  waarde betekent "gebruik de standaard". Daardoor kan het paneel per veld een
  "Standaard"-knop aanbieden zonder apart endpoint: leeg opslaan = terugzetten.
- **Een wijziging werkt meteen, zonder herstart.** De leeslaag (`StateFile`)
  cachet op `(mtime, grootte)`, dus de getters kosten één `os.stat()` in plaats
  van het bestand telkens opnieuw te parseren — wat eerder meerdere keren per
  opschoonverzoek gebeurde.
"""

from __future__ import annotations

import os

from ..state import StateFile
from . import prompts

DEFAULT_MODEL = "~anthropic/claude-haiku-latest"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Streefgrootte per deel: ~55.000 tokens (≈ 4 tekens per token). De meeste
# documenten passen in één verzoek. Opschoning levert ongeveer evenveel uitvoer
# als invoer op, dus een deel van deze grootte blijft ruim onder het
# uitvoerplafond van het model.
DEFAULT_CHUNK_TOKENS = 55_000
MIN_CHUNK_TOKENS = 5_000
MAX_CHUNK_TOKENS = 190_000       # laat ruimte onder een 200k-contextvenster
MAX_OUTPUT_TOKENS = 64_000       # Haiku's uitvoerplafond
CHARS_PER_TOKEN = 4              # ruwe schatting, genoeg voor kosten en delen

# Profielen die altijd ongesplitst moeten draaien: frontmatter en analyse mogen
# maar één keer voorkomen, dus chunken zou onzin opleveren.
NO_CHUNK_PROFILES = frozenset({"obsidian"})

# Verhouding uitvoer/invoer per profiel, voor de kostenraming. De reformat-
# profielen houden de lengte ongeveer gelijk; "obsidian" zet frontmatter, een
# inhoudsopgave en een uitgebreide analyse bóven de volledige verbatim tekst;
# een Nederlandse vertaling is doorgaans iets langer dan de brontekst.
OUTPUT_RATIO = {"obsidian": 1.35, "translate_nl": 1.15}

# Selecteerbare modellen, allemaal via dezelfde OpenRouter-sleutel. ":nitro"
# kiest de snelste provider. De prijzen in de labels zijn indicatief — de UI
# haalt de actuele prijs op via de OpenRouter-catalogus.
# `chunk_tokens: None` op elk item = "gebruik DEFAULT_CHUNK_TOKENS" (zie
# get_chunk_tokens hieronder); expliciet meegegeven zodat elk model-dict — of
# het nu hier vandaan komt of uit settings.json — dezelfde vorm heeft.
DEFAULT_MODEL_CHOICES = [
    {"id": "~anthropic/claude-haiku-latest", "label": "Claude Haiku (latest) — standaard",
     "chunk_tokens": None},
    {"id": "z-ai/glm-5.2:nitro", "label": "GLM 5.2 (nitro) — $0,93 / $3 per 1M",
     "chunk_tokens": None},
    {"id": "openai/gpt-5.6-luna:nitro", "label": "GPT-5.6 Luna (nitro) — $1 / $6 per 1M",
     "chunk_tokens": None},
    {"id": "deepseek/deepseek-v4-flash:nitro",
     "label": "DeepSeek V4 Flash (nitro) — $0,09 / $0,18 per 1M", "chunk_tokens": None},
    {"id": "anthropic/claude-haiku-4.5:nitro",
     "label": "Claude Haiku 4.5 (nitro) — $1 / $5 per 1M", "chunk_tokens": None},
    {"id": "anthropic/claude-sonnet-5:nitro",
     "label": "Claude Sonnet 5 (nitro) — $2 / $10 per 1M", "chunk_tokens": None},
    {"id": "openai/gpt-oss-120b:nitro",
     "label": "GPT-OSS 120B (nitro) — $0,036 / $0,18 per 1M", "chunk_tokens": None},
]

_store = StateFile("settings.json")


def _stored() -> dict:
    return _store.read()


# --------------------------------------------------------------------------
# Lezen
# --------------------------------------------------------------------------

def _clean_chunk_tokens(value) -> int | None:
    """Een geldige deelgrootte binnen de grenzen, anders None ("gebruik de standaard")."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if MIN_CHUNK_TOKENS <= n <= MAX_CHUNK_TOKENS else None


def _clean_models(value) -> list[dict]:
    """Alleen rijen met een niet-lege id; label mag leeg zijn. `chunk_tokens`
    is optioneel per rij (None = "gebruik DEFAULT_CHUNK_TOKENS voor dit endpoint")."""
    if not isinstance(value, list):
        return []
    return [
        {
            "id": str(m.get("id", "")).strip(),
            "label": str(m.get("label", "")).strip(),
            "chunk_tokens": _clean_chunk_tokens(m.get("chunk_tokens")),
        }
        for m in value
        if isinstance(m, dict) and str(m.get("id", "")).strip()
    ]


def get_model_choices() -> list[dict]:
    """De opties in de modellenlijst: eigen lijst, anders de standaardlijst."""
    return _clean_models(_stored().get("models")) or DEFAULT_MODEL_CHOICES


def valid_model_ids() -> set[str]:
    return {m["id"] for m in get_model_choices()}


def get_chunk_tokens(model: str | None = None) -> int:
    """Deelgrootte in tokens voor `model`: diens eigen waarde als die gezet is,
    anders `DEFAULT_CHUNK_TOKENS`. Zonder `model` (of een onbekend model) altijd
    de standaard — er is geen centrale instelling meer om op terug te vallen."""
    for m in get_model_choices():
        if m["id"] == model:
            return m.get("chunk_tokens") or DEFAULT_CHUNK_TOKENS
    return DEFAULT_CHUNK_TOKENS


def get_chunk_chars(model: str | None = None) -> int:
    return get_chunk_tokens(model) * CHARS_PER_TOKEN


def get_prompt(profile: str) -> str:
    """De systeemprompt voor een profiel: eigen tekst, anders de standaard."""
    stored = _stored().get("prompts")
    if isinstance(stored, dict):
        override = stored.get(profile)
        if isinstance(override, str) and override.strip():
            return override
    return prompts.DEFAULTS.get(profile, prompts.GENERIC)


def resolve_model(override: str | None = None) -> str:
    """Het te gebruiken model: keuze uit de UI, anders LLM_MODEL, anders standaard.

    Een expliciete keuze moet in de geconfigureerde lijst staan; zo kan een
    willekeurige waarde uit een verzoek nooit een onbekend model aanroepen.
    """
    if override and override in valid_model_ids():
        return override
    # `or DEFAULT` zodat een lege env-var (bv. Docker's ${VAR:-}) terugvalt.
    return os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or None


def is_available() -> bool:
    """Is er een OpenRouter-sleutel geconfigureerd?"""
    return bool(api_key())


def base_url() -> str:
    """De API-basis, genormaliseerd.

    De code plakt zelf `/chat/completions` of `/models` erachter, dus een door
    de gebruiker ingestelde URL die dat pad al bevat wordt ingekort.
    """
    url = (os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


# --------------------------------------------------------------------------
# Schrijven
# --------------------------------------------------------------------------

def settings_payload() -> dict:
    """Alles wat het instellingenpaneel nodig heeft: huidige waarden + standaarden.

    Geen los `chunk_tokens`-veld meer: dat staat nu per item in `models`
    (zie `_clean_models`). `min_chunk_tokens`/`max_chunk_tokens` blijven wél
    top-level — dat zijn de grenzen die voor élk endpoint gelden, voor de
    validatie van het invoerveld per rij.
    """
    return {
        "models": get_model_choices(),
        "prompts": {p: get_prompt(p) for p in prompts.PROFILES},
        "defaults": {
            "models": DEFAULT_MODEL_CHOICES,
            "chunk_tokens": DEFAULT_CHUNK_TOKENS,
            "prompts": dict(prompts.DEFAULTS),
            "min_chunk_tokens": MIN_CHUNK_TOKENS,
            "max_chunk_tokens": MAX_CHUNK_TOKENS,
        },
    }


def update_settings(payload: dict) -> dict:
    """Werk de instellingen bij en geef het nieuwe payload terug.

    Een sleutel die niet in `payload` zit blijft ongemoeid. Een lege of
    ongeldige waarde (lege lijst, lege prompt, deelgrootte buiten de grenzen)
    wist die sleutel juist, zodat de standaardwaarde weer geldt — dat is wat de
    "Standaard"-knoppen in het paneel doen.
    """

    def change(data: dict) -> None:
        if "models" in payload:
            cleaned = _clean_models(payload["models"])
            if cleaned:
                data["models"] = cleaned
            else:
                data.pop("models", None)

        if isinstance(payload.get("prompts"), dict):
            stored = dict(data.get("prompts") or {})
            for profile in prompts.PROFILES:
                if profile not in payload["prompts"]:
                    continue
                text = payload["prompts"][profile]
                if isinstance(text, str) and text.strip():
                    stored[profile] = text
                else:
                    stored.pop(profile, None)
            if stored:
                data["prompts"] = stored
            else:
                data.pop("prompts", None)

    _store.mutate(change)
    return settings_payload()
