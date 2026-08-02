"""Eén fouttype voor de hele domeinlaag, met een vaste HTTP-afbeelding.

De converters en de opschoonlaag gooien `ConversionError` met een Nederlandse
boodschap die rechtstreeks aan de gebruiker wordt getoond. De HTTP-laag
(`mdconv.api`) vertaalt dat naar een JSON-antwoord; domeincode weet dus niets
over Flask, en foutmeldingen worden op één plek naar de gebruiker gebracht.
"""

from __future__ import annotations


class ConversionError(Exception):
    """Een verwachte fout met een boodschap die de gebruiker mag zien.

    `status` is de HTTP-status die de API-laag gebruikt (standaard 400: de
    invoer of de bron klopt niet, niet de server).
    """

    status = 400

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status


class ConfigError(ConversionError):
    """Ontbrekende of ongeldige configuratie (bv. geen OpenRouter-sleutel)."""


class UpstreamError(ConversionError):
    """Een externe bron (EUR-Lex, HUDOC, …) gaf geen bruikbaar antwoord."""
