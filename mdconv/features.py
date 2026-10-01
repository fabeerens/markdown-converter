"""Welke onderdelen deze installatie aanbiedt.

"Zonder AI" betekent: geen AI-opschoning, vertalen, "Opmaken voor Obsidian",
wiskunde-modus (OCR) en geen AI-instellingen. De gewone conversie naar
markdown — jurisprudentie, wetgeving, documentupload, tekst plakken, lijsten
plakken — blijft gewoon werken.

**Eén bron van waarheid: de omgevingsvariabele `MDCONV_AI`** (via `.env`).
Twee manieren om die te zetten, met een bewuste rangorde:

1. **Vóór het opstarten**, handmatig in `.env` of de omgeving (bv. door een
   organisatie). Staat AI dan op uit, dan registreert `create_app()` het hele
   AI-/instellingenblueprint niet eens — het ⚙-paneel verdwijnt helemaal, en
   er is geen route om AI via de UI weer aan te zetten. Dat is de garantie
   voor een installatie die bewust zonder AI draait: alleen door het bestand
   zelf (buiten de UI om) aan te passen en de server te herstarten, komt AI
   terug.
2. **Tijdens het draaien**, met de schakelaar "AI-functies" in het
   instellingenpaneel (alleen zichtbaar als AI niet al zo vergrendeld was).
   Die schakelaar schrijft rechtstreeks in `.env` — dus dezelfde variabele,
   niet een aparte instelling — én meteen in `os.environ` van dit proces,
   zodat de wijziging vanaf het volgende verzoek geldt, zonder herstart.
   Uitzetten schrijft `MDCONV_AI=off`; aanzetten verwijdert die regel weer
   (leeg = standaard = aan, dezelfde conventie als de rest van de
   instellingen in `.deploy-state/settings.json`).

Bewust geen tweede opslagplek (bv. `settings.json`) voor deze ene schakelaar:
één variabele die je zowel voor het opstarten handmatig kunt zetten als
tijdens het draaien via de UI kunt wijzigen, in plaats van twee plekken die
uit elkaar kunnen lopen.
"""

from __future__ import annotations

import os

from .errors import ConversionError
from .state import base_dir

_OFF = {"0", "off", "false", "no", "uit", "nee"}
_ENV_KEY = "MDCONV_AI"

# De map met `.env`. Een gewone module-level waarde (net als `state._BASE_DIR`)
# — tests monkeypatchen dit rechtstreeks naar een tmp_path, zodat ze nooit het
# echte projectbestand aanraken.
_ENV_DIR = base_dir()


def _env_path() -> str:
    return os.path.join(_ENV_DIR, ".env")


def ai_locked_off() -> bool:
    """Staat AI nu uit? Wordt zowel bij het opstarten gebruikt (om te bepalen
    of het AI-blueprint bestaat) als live, per verzoek (de schakelaar werkt
    daardoor meteen door, zonder herstart)."""
    return (os.environ.get(_ENV_KEY) or "").strip().lower() in _OFF


def ai_enabled() -> bool:
    """Het omgekeerde van `ai_locked_off()`, voor leesbaarheid op de plekken
    waar "is AI aan" natuurlijker leest dan "is AI niet uit"."""
    return not ai_locked_off()


def set_ai_enabled(enabled: bool) -> None:
    """Schakelaar "AI-functies": zet `MDCONV_AI` in `.env` én in `os.environ`
    van dit proces. Bewaart alle andere regels in `.env` (bv.
    `OPENROUTER_API_KEY`) ongemoeid; maakt het bestand aan als het nog niet
    bestaat.
    """
    path = _env_path()
    try:
        existing = open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else []
    except OSError as e:
        raise ConversionError(f"Kon .env niet lezen: {e}") from e

    kept = [line for line in existing if not _is_key_line(line)]
    if not enabled:
        kept.append(f"{_ENV_KEY}=off")

    try:
        os.makedirs(_ENV_DIR, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))
        os.replace(tmp, path)
    except OSError as e:
        raise ConversionError(
            f"Kon .env niet wegschrijven: {e}. Is het bestand niet beschrijfbaar "
            f"(bv. een vergrendelde installatie), zet {_ENV_KEY} dan handmatig."
        ) from e

    # Meteen ook voor dit proces, zonder herstart: serve.sh laadt .env alleen
    # bij het opstarten in de omgeving, dus een bestandswijziging alleen zou
    # pas bij de volgende herstart doorwerken.
    if enabled:
        os.environ.pop(_ENV_KEY, None)
    else:
        os.environ[_ENV_KEY] = "off"


def _is_key_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(f"{_ENV_KEY}=") or stripped.startswith(f"{_ENV_KEY} =")
