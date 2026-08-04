"""Franse rechtspraak — dispatcher voor Conseil constitutionnel + Cour de cassation.

Frankrijk heeft geen gemeenschappelijke, sleutelloze bron: het Conseil
constitutionnel (ECLI:FR:CC:...) publiceert op zijn eigen site zonder
blokkade; de Cour de cassation (ECLI:FR:CCASS:...) heeft de officiële,
OAuth2-beveiligde Judilibre-API (zie `fr_judilibre.py`). Deze module is het
registratiepunt voor `_NATIONAL_SOURCES["FR"]` en routeert op het
gerecht-onderdeel van de ECLI; overige Franse gerechten (Conseil d'État,
cours d'appel, ...) krijgen een duidelijke foutmelding in plaats van een gok.

**Waarom niet via Légifrance.** De publieke Légifrance-website (waar de Cour
de cassation en de Conseil d'État hun uitspraken publiceren) staat achter een
Cloudflare-JS-challenge en is dus niet met plain `requests` te scrapen. De
Conseil d'État heeft geen vergelijkbare eigen-site-route of publieke API.

**Conseil constitutionnel — deterministische URL.** Rechtstreeks uit de ECLI
af te leiden, analoog aan `eli_to_celex()` voor EUR-Lex:

  ECLI:FR:CC:2021:2021.931.QPC
  → https://www.conseil-constitutionnel.fr/decision/2021/2021931QPC.htm
    (jaar = het 4e ECLI-onderdeel; de rest van de URL is het 5e ECLI-onderdeel
    met de punten eraf)

Geverifieerd op twee besluittypes (QPC en DC) — beide bevestigen de ECLI
letterlijk terug in de paginatekst, dus deze afleiding is niet een gok maar
gecontroleerd.
"""

from __future__ import annotations

import re

from .. import net
from ..errors import ConversionError
from ..render import container_to_markdown, tidy
from . import fr_judilibre

ECLI_RE = re.compile(r"ECLI:FR:[A-Za-z0-9]+:\d{4}:[A-Za-z0-9.]+", re.I)
_CC_ECLI_RE = re.compile(r"ECLI:FR:CC:(\d{4}):([A-Za-z0-9.]+)", re.I)

_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40


def fetch(query: str) -> tuple[str, str]:
    """Haal een Frans besluit/uitspraak op; routeert op het gerecht in de ECLI."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig Frans ECLI-nummer herkend (bv. ECLI:FR:CC:2021:2021.931.QPC "
            "of ECLI:FR:CCASS:2019:C100589)."
        )
    ecli = m.group(0).upper()

    if fr_judilibre.ECLI_RE.fullmatch(ecli):
        return fr_judilibre.fetch(ecli)

    cc_match = _CC_ECLI_RE.search(ecli)
    if not cc_match:
        raise ConversionError(
            f"Alleen het Conseil constitutionnel (ECLI:FR:CC:…) en de Cour de cassation "
            f"(ECLI:FR:CCASS:…) worden ondersteund voor Franse rechtspraak — {ecli} niet. "
            f"De Conseil d'État en de cours d'appel staan (ook) op Légifrance, dat achter "
            f"een bot-blokkade zit."
        )

    year, slug = cc_match.group(1), cc_match.group(2).replace(".", "")
    url = f"https://www.conseil-constitutionnel.fr/decision/{year}/{slug}.htm"

    r = net.documents().get(url, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise ConversionError(f"Kon besluit {ecli} niet ophalen (status {r.status_code}).")

    markdown = _html_to_markdown(net.decoded_text(r))
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Besluit {ecli} bevat geen leesbare tekst.")
    return markdown, f"Conseil constitutionnel • {ecli}"


def _html_to_markdown(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    title = soup.find("h1", class_="title")
    heading = title.get_text(strip=True) if title else "Décision"

    subtitle_field = soup.select_one(".field--name-field-titre-complet p")
    subtitle = subtitle_field.get_text(strip=True) if subtitle_field else None

    body = soup.select_one(".field--name-field-contenu-original")
    if body is None:
        return ""
    for tag in body(["script", "style"]):
        tag.decompose()

    parts = [f"# {heading}"]
    if subtitle:
        parts.append(f"## {subtitle}")
    parts.append(container_to_markdown(body))
    return tidy("\n\n".join(parts))
