"""EUR-Lex ophalen op CELEX-nummer, ELI-link of EU-ECLI.

Twee strategieën, in deze volgorde:

1. De officiële documentinhoud uit het **Cellar**-archief van het
   Publicatiebureau via content negotiation (``application/xhtml+xml``). Dit is
   het betrouwbare programmatische endpoint en respecteert ``Accept-Language``.
2. De gerenderde HTML van de EUR-Lex portal. Die blokkeert bots grotendeels en
   antwoordt met HTTP 202 zolang de pagina nog wordt opgebouwd, vandaar de
   herhaalpogingen.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import net
from ..errors import ConversionError
from ..render import html_to_markdown

# Een CELEX: sectorcijfer + jaar + documenttypeletter(s) + nummer.
# Bv. 32016R0679, 32011L0083, 62019CJ0311, 52021PC0206
_CELEX_RE = re.compile(r"^[0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?$", re.I)

# Een EU-ECLI (Hof van Justitie / Gerecht), bv. ECLI:EU:C:2025:645.
_EU_ECLI_RE = re.compile(r"ECLI:EU:[A-Z]{1,2}:\d{4}:\d+", re.I)

# ELI-URL's (European Legislation Identifier), bv.
#   https://eur-lex.europa.eu/eli/reg/2016/679/oj?locale=NL
# Het ELI-documenttype bepaalt de CELEX-descriptorletter; de rest van de CELEX
# (sector 3, jaar, viercijferig nummer) is voor wetgevingshandelingen vast.
# Cellar resolvet een ELI namelijk niet zelf (404) en de portal blokkeert.
_ELI_RE = re.compile(r"/eli/([a-z_]+)/(\d{4})/(\d+)", re.I)
_ELI_TYPE = {
    "reg": "R", "reg_impl": "R", "reg_del": "R",
    "dir": "L", "dir_impl": "L", "dir_del": "L",
    "dec": "D", "dec_impl": "D", "dec_del": "D",
    "reco": "H", "recommendation": "H",
}

_CELLAR_TIMEOUT = 45
_PORTAL_TIMEOUT = 30
_PORTAL_ATTEMPTS = 6


def extract_celex(text: str) -> str | None:
    """Haal een CELEX-identifier uit een losse string of een EUR-Lex URL."""
    text = text.strip()
    if not text:
        return None

    if _CELEX_RE.match(text):
        return text.upper()

    # Uit een URL: kijk naar de `uri`-queryparameter of het pad.
    parsed = urlparse(text)
    if parsed.query:
        qs = parse_qs(parsed.query)
        for key in ("uri", "CELEX", "celex"):
            if key in qs:
                val = unquote(qs[key][0])
                if re.search(r"CELEX[:%]*3?([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4})", val, re.I):
                    return _clean(val)
                if _CELEX_RE.match(val):
                    return val.upper()

    m = re.search(r"CELEX[:/]([0-9A-Z()]+)", text, re.I)
    if m and _CELEX_RE.match(m.group(1)):
        return m.group(1).upper()

    m = re.search(r"/([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4})", text, re.I)
    if m:
        return m.group(1).upper()

    return None


def _clean(uri_value: str) -> str:
    m = re.search(r"([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?)", uri_value, re.I)
    return m.group(1).upper() if m else uri_value.upper()


def eli_to_celex(text: str) -> str | None:
    """Leid een CELEX-nummer af uit een ELI-URL/identifier, of None."""
    m = _ELI_RE.search(text)
    if not m:
        return None
    typ, year, num = m.group(1).lower(), m.group(2), m.group(3)
    letter = _ELI_TYPE.get(typ)
    if not letter:
        return None
    return f"3{year}{letter}{int(num):04d}"


def fetch_and_convert(text: str, lang: str = "NL") -> tuple[str, str]:
    """Los de invoer op naar een document; geeft (markdown, bronvermelding)."""
    lang = (lang or "NL").upper()

    # EU-rechtspraak op ECLI: via het Cellar-ECLI-endpoint.
    ecli_m = _EU_ECLI_RE.search(text)
    if ecli_m:
        ecli = ecli_m.group(0).upper()
        html = _fetch_cellar_ecli(ecli, lang)
        if html:
            markdown = html_to_markdown(html)
            if len(markdown.strip()) > 80:
                return markdown, f"EUR-Lex (Cellar) • {ecli} • {lang}"
        raise ConversionError(
            f"Kon {ecli} niet ophalen bij EUR-Lex (mogelijk niet beschikbaar in taal {lang})."
        )

    celex = extract_celex(text) or eli_to_celex(text)
    if not celex:
        raise ConversionError(
            "Geen geldig CELEX-nummer of EUR-Lex URL herkend. "
            "Voorbeeld: 32016R0679 of "
            "https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32016R0679"
        )

    # Strategie 1: officiële inhoud uit Cellar (betrouwbaar).
    try:
        html = _fetch_cellar(celex, lang)
        if html:
            markdown = html_to_markdown(html)
            if len(markdown.strip()) > 80:
                return markdown, f"EUR-Lex (Cellar) • CELEX:{celex} • {lang}"
    except ConversionError:
        raise
    except Exception:
        pass  # netwerkprobleem bij Cellar: probeer de portal

    # Strategie 2: de EUR-Lex portal (met herhaalpogingen bij HTTP 202).
    html = _fetch_portal_html(celex, lang)
    return html_to_markdown(html), f"EUR-Lex portal • CELEX:{celex} • {lang}"


def _cellar_headers(lang: str) -> dict[str, str]:
    # `Accept: application/xhtml+xml` is wat de tekst oplevert; zonder dat (of
    # met notice=object) krijg je alleen metadata. Accept-Language kiest de taal.
    return {"Accept": "application/xhtml+xml", "Accept-Language": lang.lower()}


def _fetch_cellar(celex: str, lang: str) -> str | None:
    """Documentinhoud uit Cellar via content negotiation."""
    url = f"http://publications.europa.eu/resource/celex/{celex}"
    r = net.documents().get(
        url, headers=_cellar_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
    )
    if r.status_code == 300:
        # Meerdere keuzes zonder taalmatch.
        raise ConversionError(
            f"Document niet beschikbaar in taal {lang} (CELEX:{celex}). Probeer een andere taal."
        )
    if r.status_code != 200:
        return None
    return net.decoded_text(r)


def _fetch_cellar_ecli(ecli: str, lang: str) -> str | None:
    """EU-rechtspraak uit Cellar, geadresseerd op ECLI.

    De ECLI moet url-encoded (`ECLI%3AEU%3AC%3A…`), anders geeft Cellar 404.
    """
    url = f"http://publications.europa.eu/resource/ecli/{quote(ecli, safe='')}"
    r = net.documents().get(
        url, headers=_cellar_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
    )
    if r.status_code != 200:
        return None
    return net.decoded_text(r)


def _fetch_portal_html(celex: str, lang: str) -> str:
    url = f"https://eur-lex.europa.eu/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"
    session = net.documents()
    r = None
    for _ in range(_PORTAL_ATTEMPTS):
        r = session.get(url, timeout=_PORTAL_TIMEOUT)
        if r.status_code == 200 and r.text.strip():
            break
        if r.status_code == 202:  # EUR-Lex rendert de pagina nog
            time.sleep(2)
            continue
        break
    if r is None or r.status_code != 200 or not r.text.strip():
        code = r.status_code if r is not None else "?"
        raise ConversionError(
            f"Kon het document niet ophalen van EUR-Lex (status {code}) voor CELEX:{celex} "
            f"in taal {lang}. Controleer het nummer/de taal, of download de Formex-XML en "
            f"upload die via het andere tabblad."
        )
    return net.decoded_text(r)
