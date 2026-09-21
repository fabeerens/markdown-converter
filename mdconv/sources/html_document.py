"""Een HTML-pagina of -bestand als document voor de kennisbank.

Anders dan `render.html_to_markdown()` — dat kent de containers van EUR-Lex en
HUDOC en valt daarna terug op `soup.body or soup` — mág deze route mislukken.
Een willekeurige pagina heeft navigatie, cookiebanners en zijbalken, en die
laten meekomen is een stille fout die niemand meer terugvindt. Daarom kiest de
route een container op een vaste ladder (`<main>`, `<article>`, `[role=main]`)
en weigert met de reden als die ladder geen eenduidig antwoord geeft.

**De bewaarde bron is de gekozen container, niet de hele pagina.** De kennisbank
herberekent de tabellen uit de bewaarde bytes en legt ze naast de markdown. Stond
de hele pagina erin, dan telde een tabel in de zijbalk mee als inhoudstabel en
zou de controle een correcte omzetting afwijzen. De hash van de hele pagina gaat
wel mee in de herkomst (`extra.pagina_sha256`), zodat te zien blijft welke pagina
dit was.

Tabellen lopen door `table_structure.normalize_data_tables`, met dezelfde
weigeringen als elke andere HTML-route: samengevoegde cellen zonder sluitend
raster, meerdere koplagen en een tabel in een tabel.
"""

from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

from ..errors import ConversionError
from ..render import tidy
from ..source_structure import record_html
from ..table_structure import normalize_data_tables

_MIN_USEFUL_LENGTH = 40

# Elementen die nooit tekst van het document zijn.
_WEG = ("script", "style", "noscript", "template")

# De ladder. Elke trede telt alleen als hij precies één element oplevert: twee
# `<article>`s zijn een lijst van berichten en geen document, en welke van de twee
# bedoeld is valt niet te raden.
_LADDER = (
    ("main", lambda soup: soup.find_all("main")),
    ("article", lambda soup: soup.find_all("article")),
    ("[role=main]", lambda soup: soup.select("[role=main]")),
)


def kies_container(soup):
    """Het inhoudselement, of een weigering met wat de pagina wél bevat."""
    geteld = []
    for naam, zoek in _LADDER:
        gevonden = zoek(soup)
        if len(gevonden) == 1:
            return naam, gevonden[0]
        geteld.append(f"{len(gevonden)}× {naam}")
    raise ConversionError(
        "Kan de inhoud van deze pagina niet eenduidig aanwijzen (" + ", ".join(geteld) +
        "); een pagina zonder één <main>, <article> of [role=main] wordt niet geraden. "
        "Sla de pagina op als PDF of Word-bestand, of lever de officiële bron.")


def convert(data: bytes | str, *, source_url: str | None = None,
            identifier: str | None = None) -> tuple[str, tuple[str, ...], dict]:
    """Zet een HTML-pagina om; geeft (markdown, waarschuwingen, extra) terug."""
    pagina = data if isinstance(data, bytes) else data.encode("utf-8")
    soup = BeautifulSoup(pagina, "lxml")
    for tag in soup(list(_WEG)):
        tag.decompose()

    selector, container = kies_container(soup)
    waarschuwingen: list[str] = []
    afbeeldingen = container.find_all("img")
    if afbeeldingen:
        # Een afbeelding kan inhoud dragen (een schema, een handtekening). De keten
        # heeft er geen route voor, dus het verlies wordt gemeld en niet verzwegen.
        for img in afbeeldingen:
            img.decompose()
        waarschuwingen.append(f"{len(afbeeldingen)} afbeelding(en) niet overgenomen")

    # De bron zoals hij is vóór het omzetten: normalize_data_tables herschrijft de
    # tabellen (samengevoegde cellen worden uitgeschreven), en het bewijs moet de
    # oorspronkelijke vorm dragen.
    fragment = str(container)
    normalize_data_tables(container)
    markdown = tidy(_markdownify(str(container), heading_style="ATX", strip=["a"], bullets="-"))
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError("Geen leesbare tekst gevonden in de gekozen inhoud van de pagina.")

    record_html(fragment, source_url=source_url or "", identifier=identifier)
    extra = {"selectie": selector,
             "pagina_sha256": hashlib.sha256(pagina).hexdigest()}
    return markdown, tuple(waarschuwingen), extra
