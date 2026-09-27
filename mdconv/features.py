"""Welke onderdelen deze installatie aanbiedt.

"Zonder AI" betekent: geen AI-opschoning, vertalen, "Opmaken voor Obsidian",
wiskunde-modus (OCR) en geen AI-instellingen. De gewone conversie naar
markdown — jurisprudentie, wetgeving, documentupload, tekst plakken, lijsten
plakken — blijft gewoon werken.

Twee lagen, met een bewuste rangorde:

1. **`MDCONV_AI=off` in de omgeving is een harde vergrendeling.** De AI-routes
   worden dan niet eens geregistreerd, en het instellingenpaneel (met de
   schakelaar hieronder) verdwijnt helemaal. Zo kan een gebruiker op een
   installatie die bewust zonder AI draait (bv. binnen een organisatie) AI niet
   zelf weer aanzetten.
2. **Anders beslist de schakelaar "AI-functies" in het instellingenpaneel**
   (`ai_enabled` in `settings.json`, standaard aan). Uit = dezelfde UI als bij
   de vergrendeling, behalve dat ⚙ blijft staan met alleen die schakelaar,
   zodat je AI weer aan kunt zetten.

Bewust een schakelaar in dezelfde codebase en geen aparte branch waar de code
uit is gesloopt: de AI-code verandert vaak, en een branch zonder die code zou
bij elke wijziging op main merge-conflicten opleveren.
"""

from __future__ import annotations

import os

_OFF = {"0", "off", "false", "no", "uit", "nee"}


def ai_locked_off() -> bool:
    """Staat AI via de omgeving vast op uit? (Standaard niet.)"""
    return (os.environ.get("MDCONV_AI") or "").strip().lower() in _OFF


def ai_enabled(locked_off: bool) -> bool:
    """Is AI nu daadwerkelijk aan: niet vergrendeld én de schakelaar staat aan."""
    if locked_off:
        return False
    from . import cleanup  # lui: alleen nodig als AI niet vergrendeld is
    return cleanup.get_ai_enabled()
