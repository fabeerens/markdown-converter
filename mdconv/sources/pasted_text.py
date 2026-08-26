"""Handmatig geplakte tekst — kaal of verrijkt (opmaak uit het klembord).

De front-end stuurt zowel de platte tekst (`text`, uit `element.innerText`)
als de rijke HTML (`html`, uit `element.innerHTML`) van een `contenteditable`
vak mee. Alleen als de HTML echte structuur bevat (koppen, lijsten, tabellen,
nadruk, regeleindes) is die de moeite waard om te gebruiken; anders is de
platte tekst betrouwbaarder. Reden: sommige plak-bronnen leveren voor kale
tekst een klembord-HTML die niets meer is dan één `<span>`/`<div>` om de hele
tekst heen, met de regeleindes als kale `\n`-tekens in plaats van `<br>`/`<p>`
— de HTML-route zou daar de eigen regelindeling van de gebruiker verliezen
(markdownify normaliseert witruimte binnen zo'n inline-element).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..errors import ConversionError
from ..render import container_to_markdown, tidy

_STRUCTURE_TAGS = (
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "tr",
    "td", "th", "strong", "b", "em", "i", "u", "blockquote", "br", "img", "hr",
)

# Puur om de soort (rechtspraak/document) af te leiden als de geplakte tekst
# een ECLI bevat — dezelfde vorm als de per-land ECLI_RE's elders, maar hier
# alleen ter herkenning, niet om ergens op te routeren.
_ECLI_RE = re.compile(r"ECLI:[A-Z]{2}:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+", re.I)

_MIN_LENGTH = 1


def convert(html: str | None, text: str | None) -> tuple[str, str]:
    """Zet geplakte tekst om; geeft (markdown, bronvermelding)."""
    html = (html or "").strip()
    text = (text or "").strip()

    markdown = _from_html(html) if _has_structure(html) else tidy(text)
    if len(markdown.strip()) < _MIN_LENGTH and text:
        # De HTML-route leverde niets bruikbaars op (bv. opmaak zonder tekst) —
        # val terug op de platte tekst voordat we opgeven.
        markdown = tidy(text)
    if len(markdown.strip()) < _MIN_LENGTH:
        raise ConversionError("Plak eerst tekst in het vak.")

    source = "Geplakte tekst"
    m = _ECLI_RE.search(markdown)
    if m:
        source = f"{source} • {m.group(0).upper()}"
    return markdown, source


def _has_structure(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    return any(soup.find(tag) for tag in _STRUCTURE_TAGS)


def _from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "meta", "link", "head"]):
        tag.decompose()
    return container_to_markdown(soup.body or soup)
