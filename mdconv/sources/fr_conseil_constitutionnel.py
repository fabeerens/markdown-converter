"""Franse rechtspraak — alleen het Conseil constitutionnel (ECLI:FR:CC:...).

**Waarom alleen het Conseil constitutionnel.** De publieke Légifrance-website
(waar de Cour de cassation en de Conseil d'État hun uitspraken publiceren)
staat achter een Cloudflare-JS-challenge en is dus niet met plain `requests`
te scrapen. De officiële API voor de Cour de cassation (Judilibre) vereist
verplichte registratie via het PISTE-portaal (OAuth2/API-sleutel) én heeft
geen ECLI-zoekparameter — alleen een intern MongoDB-`id`. Beide zijn buiten
bereik van dit project (geen browserautomatisering, geen verplichte
accountregistratie namens de gebruiker).

Het Conseil constitutionnel publiceert daarentegen op zijn **eigen site**
(niet Légifrance), zonder enige blokkade, met een **deterministische URL**
die rechtstreeks uit de ECLI is af te leiden — analoog aan `eli_to_celex()`
voor EUR-Lex:

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

# Beperkt tot CC: andere Franse gerechten (CCASS, CE, …) hebben geen
# auth-vrije route (zie moduledocstring) en krijgen een eigen, duidelijke
# foutmelding in plaats van hier een match te veinzen.
ECLI_RE = re.compile(r"ECLI:FR:[A-Za-z0-9]+:\d{4}:[A-Za-z0-9.]+", re.I)
_CC_ECLI_RE = re.compile(r"ECLI:FR:CC:(\d{4}):([A-Za-z0-9.]+)", re.I)

_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40


def fetch(query: str) -> tuple[str, str]:
    """Haal een besluit van het Conseil constitutionnel op."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig Frans ECLI-nummer herkend (bv. ECLI:FR:CC:2021:2021.931.QPC)."
        )
    ecli = m.group(0).upper()

    cc_match = _CC_ECLI_RE.search(ecli)
    if not cc_match:
        raise ConversionError(
            f"Alleen het Conseil constitutionnel (ECLI:FR:CC:…) wordt ondersteund voor "
            f"Franse rechtspraak — {ecli} niet. De Cour de cassation en de Conseil d'État "
            f"staan op Légifrance, dat achter een bot-blokkade zit; de officiële API van de "
            f"Cour de cassation (Judilibre) vereist een verplichte accountregistratie."
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
