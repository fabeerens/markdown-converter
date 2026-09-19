"""Nederlandse wetgeving van wetten.overheid.nl.

Invoer mag een volledige wetten.overheid.nl-URL zijn of een BWB-identifier
(eventueel met versiedatum):
  https://wetten.overheid.nl/BWBR0040940/2021-07-01
  BWBR0040940
  BWBR0040940/2021-07-01

Er is geen bruikbare XML-export; de portal-HTML is server-rendered en bevat de
volledige tekst met echte kop-tags. De opbouw van de pagina:

    div#regeling
    ├── h1                                de citeertitel
    ├── div.article__header--main
    │   └── p.regeling-toestand-meldingen "Geraadpleegd op … Geldend van … t/m …"
    └── div.wetgeving                     de regeling zelf

De omzetting neemt `div.wetgeving` plus die `h1`. Daarmee valt het statusblok
vanzelf buiten de tekst — het is geen wettekst maar wel de enige plek waar staat
wélke versie dit is, dus het gaat naar het herkomstbestand.

Binnen de regeling staat achter elke kop een `ul[aria-label="Lijst met mogelijke
acties …"]` met zes portalknoppen. Die gaan eruit op hun aria-label. Eerder
gebeurde dat op klassenaam plus een tekstlengtegrens, en dat is precies
misgegaan: de kop en de melding "[Wijziging(en) zonder datum inwerkingtreding
aanwezig …]" staan in dezelfde div, en zodra de knoppen eruit waren paste die
div onder de grens. `decompose()` nam de `<h4>` mee. Vijftien artikelkoppen uit
het Wetboek van Strafrecht en zes uit de Awb verdwenen zo, met hun leden onder
het vórige artikel.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .. import net, verify
from ..herkomst import Herkomst, vandaag
from ..source_structure import source_structure, bind_structure
from ..errors import ConversionError
from ..render import collapse_ws, container_to_markdown
from .wetten_footnotes import prepare_footnotes

# BWB-identifiers: BWBR (regelingen), BWBV (verdragen), BWBW, BWBS, …
_BWB_RE = re.compile(r"BWB[A-Z]\d+", re.I)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# De portalknoppen achter elke kop ("Toon relaties in LiDO", "Druk af", …).
_MENU_SELECTOR = 'ul[aria-label^="Lijst met mogelijke acties"]'

_TIMEOUT = 60
_MIN_USEFUL_LENGTH = 40


def matches(query: str) -> bool:
    """Hoort deze invoer bij wetten.overheid.nl?"""
    return "wetten.overheid.nl" in query.lower() or bool(_BWB_RE.search(query))


def fetch(query: str) -> tuple[str, str, Herkomst]:
    """Haal een regeling op; geeft (markdown, bronvermelding, herkomst)."""
    m = _BWB_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig BWB-nummer of wetten.overheid.nl-link herkend "
            "(bv. BWBR0040940 of https://wetten.overheid.nl/BWBR0040940/2021-07-01)."
        )
    bwb = m.group(0).upper()
    date = _DATE_RE.search(query)
    path = bwb + (f"/{date.group(0)}" if date else "")
    url = f"https://wetten.overheid.nl/{path}"

    # Een fragment (#Hoofdstuk1_Titeldeel1.1_Artikel1:1, …) betekent: alleen dat
    # onderdeel. De ids zijn volledige paden, niet kale artikelnummers.
    anchor = unquote(query.split("#", 1)[1]).strip() if "#" in query else None

    r = net.documents().get(url, timeout=_TIMEOUT)
    if r.status_code != 200 or not r.text.strip():
        raise ConversionError(f"Kon regeling {bwb} niet ophalen (status {r.status_code}).")

    html = net.decoded_text(r)
    soup = BeautifulSoup(html, "lxml")

    # Een kale BWB lost op naar de versie die vandaag geldt; die datum staat
    # alleen in de opgeloste URL. Eerder werd het label uit de *gevraagde*
    # string gebouwd, en dan noemt de bronvermelding de versie niet.
    opgeloste_url = getattr(r, "url", "") or url
    versie = _versie_uit_url(opgeloste_url, bwb)
    getoond_pad = f"{bwb}/{versie}" if versie else path

    original_container = soup.select_one("div.wetgeving") or soup.select_one("#regeling")
    if original_container is not None and anchor:
        original_container = original_container.find(id=anchor)
    evidence = source_structure(html, original_container, selector="div.wetgeving", anchor=anchor) if original_container is not None else None
    if evidence is not None:
        evidence.update(source_url=opgeloste_url, identifier=bwb, language="nl", role="document")
    markdown, tellingen, waarschuwingen = _soup_to_markdown(soup, anchor, bron=url)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Geen leesbare wettekst gevonden voor {bwb}.")

    label = f"wetten.overheid.nl • {getoond_pad}" + (f" #{anchor}" if anchor else "")
    provenance = _herkomst(soup, bwb, versie, opgeloste_url, url, tellingen, waarschuwingen)
    if evidence is not None:
        provenance = bind_structure(provenance, markdown, [evidence])
    return markdown, label, provenance


def _versie_uit_url(url: str, bwb: str) -> str | None:
    m = re.search(rf"/{re.escape(bwb)}/(\d{{4}}-\d{{2}}-\d{{2}})", url, re.I)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Herkomst: wat de pagina over deze versie zegt
# --------------------------------------------------------------------------

_GELDEND_RE = re.compile(r"Geldend van (\d{2}-\d{2}-\d{4}) t/m (heden|\d{2}-\d{2}-\d{4})")
_GERAADPLEEGD_RE = re.compile(r"Geraadpleegd op (\d{2}-\d{2}-\d{4})")


def _iso(datum: str) -> str | None:
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", datum)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _toestand(soup) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    """Lees `p.regeling-toestand-meldingen`: geldigheid, raadpleegdatum, rest.

    Dit blok staat in `#regeling` maar buiten `div.wetgeving`, dus het komt niet
    in de tekst terecht — en het is de enige plek op de pagina die zegt wélke
    versie je voor je hebt.
    """
    blok = soup.select_one(".regeling-toestand-meldingen")
    if blok is None:
        return None, None, None, ()
    tekst = collapse_ws(blok.get_text(" ", strip=True))

    geldend = _GELDEND_RE.search(tekst)
    geraadpleegd = _GERAADPLEEGD_RE.search(tekst)
    van = _iso(geldend.group(1)) if geldend else None
    tot = _iso(geldend.group(2)) if geldend and geldend.group(2) != "heden" else None

    # Wat er verder staat ("Wijziging(en) zonder datum inwerkingtreding
    # aanwezig.") is een melding over de versie, geen wettekst. Letterlijk mee.
    meldingen = tuple(
        zin.strip()
        for zin in re.split(r"(?<=\.)\s+", tekst)
        if zin.strip() and not zin.startswith(("Geldend van", "Geraadpleegd op"))
    )
    return van, tot, (_iso(geraadpleegd.group(1)) if geraadpleegd else None), meldingen


def _dcterms(soup) -> dict:
    """De dcterms-metatags uit de `<head>` (creator, type, modified, …)."""
    uit = {}
    for meta in soup.select('meta[name^="dcterms:"]'):
        naam = (meta.get("name") or "")[len("dcterms:"):]
        inhoud = (meta.get("content") or "").strip()
        if naam and inhoud:
            uit[naam] = inhoud
    return uit


def _herkomst(soup, bwb, versie, opgeloste_url, gevraagde_url, tellingen, waarschuwingen):
    van, tot, geraadpleegd, meldingen = _toestand(soup)
    dcterms = _dcterms(soup)
    h1 = soup.select_one("#regeling h1")
    koppen_bron, koppen_markdown = tellingen
    return Herkomst(
        format="wetten-nl",
        bwb=bwb,
        title=collapse_ws(h1.get_text(" ", strip=True)) if h1 else dcterms.get("title"),
        language=dcterms.get("language") or "nl",
        versie=versie,
        geldend_van=van,
        geldend_tot=tot,
        geraadpleegd=geraadpleegd or vandaag(),
        source_url=f"https://wetten.overheid.nl/{bwb}/{versie}" if versie else opgeloste_url,
        requested_url=gevraagde_url,
        toestand_meldingen=meldingen,
        koppen_bron=koppen_bron,
        koppen_markdown=koppen_markdown,
        waarschuwingen=waarschuwingen,
        extra={"dcterms": dcterms} if dcterms else {},
    )


# --------------------------------------------------------------------------
# De omzetting
# --------------------------------------------------------------------------

def _soup_to_markdown(
    soup, anchor: str | None = None, *, bron: str = "wetten.overheid.nl"
) -> tuple[str, tuple[int, int], tuple[str, ...]]:
    waarschuwingen: list[str] = []

    body = soup.select_one("div.wetgeving")
    gebruikt_terugval = body is None
    if gebruikt_terugval:
        # Geen bekende opbouw meer. Doorgaan met #regeling is beter dan niets
        # teruggeven, maar nooit stilzwijgend: dan bevat de uitvoer ook het
        # statusblok en mogelijk portalruis.
        body = soup.select_one("#regeling")
        if body is None:
            raise ConversionError(
                "Geen wettekst gevonden op deze pagina. Controleer het BWB-nummer "
                "of de link."
            )
        for el in body.select(".regeling-toestand-meldingen"):
            el.decompose()
        waarschuwingen.append(
            "Onverwachte paginaopbouw: geen div.wetgeving gevonden. De hele "
            "#regeling-container is omgezet; controleer of er geen portalruis in staat."
        )

    if anchor:
        # Binnen de regeling zoeken, niet in de hele pagina: een id uit de
        # inhoudsopgave of een modaal venster is geen wettekst.
        container = body.find(id=anchor)
        if container is None:
            raise ConversionError(
                f"Onderdeel '#{anchor}' niet gevonden in de regeling. "
                f"Controleer het anker in de link."
            )
    else:
        container = body

    for tag in container(["script", "style", "noscript"]):
        tag.decompose()

    # De actiemenu's achter elke kop zijn navigatie van de portal. Ze gaan eruit
    # op hun aria-label en niet op tekstlengte: een lengtegrens raakt ook de
    # kopblokken zelf — precies hoe vijftien artikelkoppen verdwenen.
    for el in container.select(_MENU_SELECTOR):
        el.decompose()
    for el in container.select(".visually-hidden"):
        el.decompose()

    # De per-artikel herhaalde melding "[Wijziging(en) zonder datum
    # inwerkingtreding aanwezig. Zie het wijzigingenoverzicht.]" blijft bewust
    # staan. Het is tekst van de bron, en wat ermee gebeurt is een keuze van de
    # afnemer — niet van de omzetter.

    prepare_footnotes(container)
    markdown = container_to_markdown(container)
    koppen_bron = verify.tel_koppen_html(container)
    waarschuwingen.extend(verify.controleer_koppen(container, markdown, bron=bron))

    # De titel staat als h1 buiten div.wetgeving. Bij een fragment hoort hij er
    # niet bij: dat is een onderdeel, geen regeling. Bij de terugval zit hij al
    # in de container, dus dan niet nog een keer.
    if anchor is None and not gebruikt_terugval:
        h1 = soup.select_one("#regeling > h1") or soup.select_one("#regeling h1")
        if h1 is not None:
            titel = collapse_ws(h1.get_text(" ", strip=True))
            if titel:
                markdown = f"# {titel}\n\n{markdown}"
                # De h1 komt uit de bron (hij staat in #regeling), dus hij telt
                # aan beide kanten mee. Anders meldt het zijbestand één kop meer
                # geleverd dan de bron had, en dat leest als winst uit het niets.
                koppen_bron += 1

    return markdown, (koppen_bron, verify.tel_koppen_markdown(markdown)), tuple(waarschuwingen)


def _html_to_markdown(html: str, anchor: str | None = None) -> str:
    """Alleen de markdown, voor wie de herkomst niet nodig heeft (tests)."""
    return _soup_to_markdown(BeautifulSoup(html, "lxml"), anchor)[0]
