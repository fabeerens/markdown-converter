"""Tel de koppen vóór en ná de conversie.

Een conversie kan stil kapotgaan: de bron verandert, een selector matcht niet
meer, en wat eruit komt oogt als een geldig document. Zo verdwenen zes
artikelkoppen uit de Algemene wet bestuursrecht zonder dat iets dat meldde — de
leden gingen onder het vórige artikel hangen, en een verwijzing wees daarmee
naar de verkeerde bepaling.

Deze module staat bewust níét in `render.py`. Die is bronloos, en een poort dáár
zou op EUR-Lex vals alarm geven: `promote_headings()` maakt koppen ván
alinearegels, dus "na" is daar terecht groter dan "voor". De bronmodule weet
welke container hij heeft gekozen en haakt zelf aan.

De vergelijking is eenzijdig — méér koppen is nooit een fout — en kent twee
uitkomsten:

- **nul koppen terwijl de bron er wél had**: dat is niet één kop die wegviel maar
  de hele selectie die misging. `ConversionError`; een bron zonder koppen komt
  stilzwijgend door elke integriteitscheck heen en levert pas honderden ankers
  verderop een verschil.
- **minder koppen dan de bron**: een waarschuwing die met het document meereist
  tot in het herkomstbestand, zodat een afnemer er alsnog op kan afketsen. De
  markdown wordt wél geleverd, zodat de gebruiker kan kijken wat er mis is.
"""

from __future__ import annotations

import re

from .errors import ConversionError

_KOP_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Een kopregel in Markdown: hekjes, één spatie, en dan iets dat geen witruimte
# is. Die laatste eis is nodig omdat een lege <h4> door markdownify een kaal
# "####" wordt — een regel die als kop telt maar er geen is.
_MD_KOP_RE = re.compile(r"(?m)^#{1,6} \S")


def tel_koppen_html(container) -> int:
    """Koppen in de container die na conversie ook echt een Markdown-kop worden.

    Twee uitzonderingen, allebei gemeten en niet bedacht: een lege kop levert een
    kaal `####` op (geen kop meer), en een kop binnen een `<table>` wordt een
    tabelcel. Ze meetellen zou de poort op elk document met een tabelkop laten
    afgaan.
    """
    return sum(
        1
        for h in container.find_all(_KOP_TAGS)
        if h.get_text(strip=True) and h.find_parent("table") is None
    )


def tel_koppen_markdown(markdown: str) -> int:
    """Kopregels in de omgezette tekst."""
    return len(_MD_KOP_RE.findall(markdown))


def controleer_koppen(container, markdown: str, *, bron: str) -> tuple[str, ...]:
    """Vergelijk het aantal koppen vóór en ná de conversie.

    Geeft de waarschuwingen terug (leeg = in orde) en gooit `ConversionError` als
    er helemaal geen kop meer over is.
    """
    voor = tel_koppen_html(container)
    na = tel_koppen_markdown(markdown)

    if voor and not na:
        raise ConversionError(
            f"Geen enkele kop in het resultaat terwijl de bron er {voor} had. "
            f"De paginaopbouw van {bron} is waarschijnlijk gewijzigd; "
            f"de omzetting is geweigerd."
        )
    if na < voor:
        return (
            f"{voor - na} van de {voor} koppen zijn bij het omzetten verdwenen. "
            f"De tekst is mogelijk onvolledig; controleer het resultaat voordat "
            f"je het opslaat.",
        )
    return ()
