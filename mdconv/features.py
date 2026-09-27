"""Welke onderdelen deze installatie aanbiedt.

`MDCONV_AI=off` levert een versie zónder alles wat een taalmodel aanroept:
AI-opschoning, vertalen, "Opmaken voor Obsidian", de wiskunde-modus (OCR) en
het instellingenpaneel (dat alleen die functies instelt). De gewone conversie
naar markdown — jurisprudentie, wetgeving, documentupload, tekst plakken,
lijsten plakken — blijft gewoon werken.

Bewust een schakelaar in dezelfde codebase en geen aparte branch waar de code
uit is gesloopt: de AI-code verandert vaak, en een branch zonder die code zou
bij elke wijziging op main merge-conflicten opleveren. Nu zet een installatie
alleen deze variabele.
"""

from __future__ import annotations

import os

_OFF = {"0", "off", "false", "no", "uit", "nee"}


def ai_enabled() -> bool:
    """Standaard aan; alleen een expliciete "uit"-waarde schakelt AI uit."""
    return (os.environ.get("MDCONV_AI") or "").strip().lower() not in _OFF
