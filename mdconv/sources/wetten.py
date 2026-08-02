"""Nederlandse wetgeving van wetten.overheid.nl.

Invoer mag een volledige wetten.overheid.nl-URL zijn of een BWB-identifier
(eventueel met versiedatum):
  https://wetten.overheid.nl/BWBR0040940/2021-07-01
  BWBR0040940
  BWBR0040940/2021-07-01

Er is geen bruikbare XML-export; de portal-HTML is server-rendered en bevat de
volledige tekst in een `#regeling`-container met echte kop-tags (h1 titel,
h3 hoofdstuk, h4 artikel), dus die converteert schoon.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .. import net
from ..errors import ConversionError
from ..render import container_to_markdown

# BWB-identifiers: BWBR (regelingen), BWBV (verdragen), BWBW, BWBS, …
_BWB_RE = re.compile(r"BWB[A-Z]\d+", re.I)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Portal-melding die per artikel wordt herhaald; geen onderdeel van de wettekst.
_NOTICE_RE = re.compile(r"Wijziging\(en\) zonder datum inwerkingtreding aanwezig", re.I)

_TIMEOUT = 60
_MIN_USEFUL_LENGTH = 40
_NOTICE_MAX_LENGTH = 140


def matches(query: str) -> bool:
    """Hoort deze invoer bij wetten.overheid.nl?"""
    return "wetten.overheid.nl" in query.lower() or bool(_BWB_RE.search(query))


def fetch(query: str) -> tuple[str, str]:
    """Haal een regeling op; geeft (markdown, bronvermelding)."""
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

    # Een fragment (#Hoofdstuk16, #Artikel5, …) betekent: alleen dat onderdeel.
    anchor = unquote(query.split("#", 1)[1]).strip() if "#" in query else None

    r = net.documents().get(url, timeout=_TIMEOUT)
    if r.status_code != 200 or not r.text.strip():
        raise ConversionError(f"Kon regeling {bwb} niet ophalen (status {r.status_code}).")

    markdown = _html_to_markdown(net.decoded_text(r), anchor)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Geen leesbare wettekst gevonden voor {bwb}.")
    label = f"wetten.overheid.nl • {path}" + (f" #{anchor}" if anchor else "")
    return markdown, label


def _html_to_markdown(html: str, anchor: str | None = None) -> str:
    soup = BeautifulSoup(html, "lxml")

    if anchor:
        # Alleen het aangeklikte onderdeel (hoofdstuk/afdeling/artikel).
        container = soup.find(id=anchor)
        if container is None:
            raise ConversionError(
                f"Onderdeel '#{anchor}' niet gevonden in de regeling. "
                f"Controleer het anker in de link."
            )
    else:
        container = soup.select_one("#regeling") or soup.select_one("#content") or soup.body
    if container is None:
        return ""

    # Scripts/styles, werkbalk- en navigatieruis en het status-meldingenblok
    # bovenaan ("Geraadpleegd op…", "Geldend van…") zijn portal-meldingen.
    for tag in container(["script", "style", "noscript"]):
        tag.decompose()
    for el in container.select(
        '[class*="action--"], .visually-hidden, .regeling-toestand-meldingen'
    ):
        el.decompose()

    # De per-artikel herhaalde melding "[Wijziging(en) zonder datum
    # inwerkingtreding aanwezig. Zie het wijzigingenoverzicht.]" staat als los
    # tekstblokje zonder eigen class. Matchen op tekst, met een lengtegrens
    # zodat alleen de melding sneuvelt en niet een heel artikel.
    for el in container.find_all(["p", "div", "span"]):
        txt = el.get_text(" ", strip=True)
        if txt and _NOTICE_RE.search(txt) and len(txt) < _NOTICE_MAX_LENGTH:
            el.decompose()

    return container_to_markdown(container)
