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

from bs4 import BeautifulSoup

from .. import net
from ..errors import ConversionError
from ..render import html_to_markdown

# Een CELEX: sectorcijfer + jaar + documenttypeletter(s) + nummer, optioneel een
# corrigendum-achtervoegsel `(01)` en — bij een geconsolideerde versie — de datum
# waarop die versie geldt.
# Bv. 32016R0679, 32011L0083, 62019CJ0311, 52021PC0206, 02014R0910-20241018.
# Bewust één patroon: dezelfde vorm stond hieronder vier keer los uitgeschreven
# en liep uit elkaar zodra de datum erbij kwam — die werd in de ene tak afgewezen
# en in de andere stil afgekapt, waarna Cellar 404'de op een CELEX die niet bestaat.
_CELEX_BODY = r"[0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?(?:-[0-9]{8})?"
_CELEX_RE = re.compile(rf"^{_CELEX_BODY}$", re.I)

# Een geconsolideerde versie staat in sector 0 en draagt de datum waarop de
# versie geldt. Zo'n CELEX is alléén mét die datum geldig: Cellar kent
# `02014R0910` niet, alleen `02014R0910-20241018`.
_CONSOLIDATED_RE = re.compile(
    r"^0[0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?-([0-9]{8})$", re.I
)

# Een EU-ECLI (Hof van Justitie / Gerecht), bv. ECLI:EU:C:2025:645.
_EU_ECLI_RE = re.compile(r"ECLI:EU:[A-Z]{1,2}:\d{4}:\d+", re.I)

# ELI-URL's (European Legislation Identifier), bv.
#   https://eur-lex.europa.eu/eli/reg/2016/679/oj?locale=NL
#   https://eur-lex.europa.eu/eli/reg/2014/910/2024-10-18   (geconsolideerd)
# Het ELI-documenttype bepaalt de CELEX-descriptorletter; de rest van de CELEX
# (sector, jaar, viercijferig nummer) is voor wetgevingshandelingen vast.
# Cellar resolvet een ELI namelijk niet zelf (404) en de portal blokkeert.
# Een vierde segment in datumvorm is de consolidatiedatum; `/oj` en andere
# segmenten matchen niet en leveren dus de oorspronkelijke handeling.
_ELI_RE = re.compile(r"/eli/([a-z_]+)/(\d{4})/(\d+)(?:/(\d{4}-\d{2}-\d{2}))?", re.I)
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
                if re.search(rf"CELEX[:%]*3?({_CELEX_BODY})", val, re.I):
                    return _clean(val)
                if _CELEX_RE.match(val):
                    return val.upper()

    # Het koppelteken hoort in de tekenklasse: anders breekt de match hier af
    # op `-20241018` en levert de tak een bestaande-maar-verkeerde CELEX op.
    m = re.search(r"CELEX[:/]([0-9A-Z()-]+)", text, re.I)
    if m and _CELEX_RE.match(m.group(1)):
        return m.group(1).upper()

    m = re.search(rf"/({_CELEX_BODY})", text, re.I)
    if m:
        return m.group(1).upper()

    return None


def is_celex(text: str) -> bool:
    """Of de invoer een CELEX bevat. Voor `detect_source`, dat anders een
    geconsolideerde CELEX met een laag aktenummer (01999L0001-20040501) als
    HUDOC-item-id leest."""
    return extract_celex(text) is not None


def _clean(uri_value: str) -> str:
    m = re.search(rf"({_CELEX_BODY})", uri_value, re.I)
    return m.group(1).upper() if m else uri_value.upper()


def eli_to_celex(text: str) -> str | None:
    """Leid een CELEX-nummer af uit een ELI-URL/identifier, of None.

    Staat er een consolidatiedatum in (`/eli/reg/2014/910/2024-10-18`), dan
    levert dat de geconsolideerde CELEX (`02014R0910-20241018`) en niet de
    oorspronkelijke handeling — die datum negeren gaf stilzwijgend het
    verkeerde document terug.
    """
    m = _ELI_RE.search(text)
    if not m:
        return None
    typ, year, num, date = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
    letter = _ELI_TYPE.get(typ)
    if not letter:
        return None
    if date:
        return f"0{year}{letter}{int(num):04d}-{date.replace('-', '')}"
    return f"3{year}{letter}{int(num):04d}"


def fetch_and_convert(text: str, lang: str = "NL") -> tuple[str, str]:
    """Los de invoer op naar een document; geeft (markdown, bronvermelding)."""
    lang = (lang or "NL").upper()

    # EU-rechtspraak op ECLI: via het Cellar-ECLI-endpoint.
    ecli_m = _EU_ECLI_RE.search(text)
    if ecli_m:
        ecli = ecli_m.group(0).upper()
        markdown = _fetch_cellar_ecli(ecli, lang)
        if markdown and len(markdown.strip()) > 80:
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
        markdown = _fetch_cellar(celex, lang)
        if markdown and len(markdown.strip()) > 80:
            return markdown, f"EUR-Lex (Cellar) • CELEX:{celex} • {lang}"
    except ConversionError:
        raise
    except Exception:
        pass  # netwerkprobleem bij Cellar: probeer de portal

    # Strategie 2: de EUR-Lex portal (met herhaalpogingen bij HTTP 202).
    html = _fetch_portal_html(celex, lang)
    return html_to_markdown(html), f"EUR-Lex portal • CELEX:{celex} • {lang}"


def _cellar_headers(lang: str) -> dict[str, str]:
    # `application/xhtml+xml` is wat de tekst oplevert voor de meeste
    # documenten; `text/html` staat erbij zodat Cellar ook content-negotieert
    # voor documenten die uit meerdere HTML-onderdelen bestaan (zie
    # _fetch_multipart) — die geven anders een 300 zonder bruikbare respons.
    # Zonder Accept-header (of met notice=object) krijg je alleen metadata,
    # niet de tekst. Accept-Language kiest de taal.
    return {"Accept": "application/xhtml+xml, text/html;q=0.9", "Accept-Language": lang.lower()}


def _fetch_cellar(celex: str, lang: str) -> str | None:
    """Markdown uit Cellar via content negotiation, of None."""
    url = f"http://publications.europa.eu/resource/celex/{celex}"
    r = net.documents().get(
        url, headers=_cellar_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
    )
    if r.status_code == 200:
        html = net.decoded_text(r)
        if _CONSOLIDATED_RE.match(celex):
            html = _with_base_preamble(html, celex, lang)
        return html_to_markdown(html)
    if r.status_code == 300:
        return _fetch_multipart(r.text, lang, f"CELEX:{celex}")
    if celex.startswith("0"):
        # Sector 0 is een geconsolideerde versie. Terugvallen op de portal heeft
        # hier geen zin (die blokkeert) en levert alleen een netwerkfout op waar
        # de echte oorzaak een niet-bestaande consolidatiedatum is.
        raise ConversionError(_consolidated_error(celex))
    return None


def _consolidated_error(celex: str) -> str:
    base = _derive_base_celex(celex)
    m = _CONSOLIDATED_RE.match(celex)
    if not m:
        return (
            f"CELEX:{celex} ziet eruit als een geconsolideerde versie, maar mist een "
            "geldige datum. Zo'n nummer heeft de vorm 02014R0910-20241018 (jjjjmmdd). "
            f"Voor de oorspronkelijke handeling: CELEX:{base}."
        )
    d = m.group(1)
    return (
        f"EUR-Lex heeft geen geconsolideerde versie van CELEX:{base} per "
        f"{d[6:8]}-{d[4:6]}-{d[0:4]}. Consolidatiedata liggen vast: er is alleen een "
        "versie per datum waarop de handeling daadwerkelijk is gewijzigd. Kies de "
        "juiste versie via 'Alle versies van dit document' op EUR-Lex, of gebruik "
        f"CELEX:{base} voor de oorspronkelijke handeling."
    )


# --------------------------------------------------------------------------
# Geconsolideerde versies: de preambule terugzetten
# --------------------------------------------------------------------------
#
# EUR-Lex laat in een geconsolideerde versie de hele preambule weg — de aanhef,
# de "Gezien …"-citaten én álle overwegingen. Alleen de oorspronkelijke
# handeling heeft die, en wel in hetzelfde XHTML-skelet: een
# `div.eli-subdivision#pbl_1` tussen de titel (`#tit_1`) en de artikelen
# (`#enc_1`). Het geconsolideerde document heeft diezelfde `#tit_1`/`#enc_1`,
# dus het blok kan letterlijk terug op zijn eigen plek — geen tekstheuristiek,
# geen taalafhankelijke zoektocht naar "Overwegende hetgeen volgt:".

_PREAMBLE_NOTE = (
    "Overwegingen en aanhef zijn overgenomen uit de oorspronkelijke handeling "
    "(CELEX:{base}); de geconsolideerde tekst op EUR-Lex bevat deze niet."
)
_PREAMBLE_MISSING_NOTE = (
    "De overwegingen uit de oorspronkelijke handeling (CELEX:{base}) konden niet "
    "worden opgehaald; hieronder staat alleen de geconsolideerde tekst."
)


def _with_base_preamble(html: str, celex: str, lang: str) -> str:
    """Voeg de preambule van de oorspronkelijke handeling in vóór de artikelen.

    Faalveilig maar niet stil: lukt een stap niet, dan gaat de conversie door
    met alléén de geconsolideerde tekst plus een notitie die dat zegt. Zwijgend
    weglaten is precies wat dit gat zo lang onzichtbaar hield.
    """
    soup = BeautifulSoup(html, "lxml")
    anchor = _enacting_terms(soup)
    if anchor is None:
        return html

    base = _base_celex(soup, celex)
    preamble = _fetch_preamble(base, lang)
    note = _PREAMBLE_NOTE if preamble is not None else _PREAMBLE_MISSING_NOTE
    anchor.insert_before(_note_tag(soup, note.format(base=base)))
    if preamble is not None:
        anchor.insert_before(preamble)
    return str(soup)


def _enacting_terms(soup):
    """Het element waar de artikelen beginnen, of None.

    Moderne consolidaties hebben het eli-skelet (`#enc_1`). Oudere (bv.
    02008R0593-20080724) missen dat, maar dragen wel dezelfde CONVEX-klassen op
    de eerste hoofdstuk- of artikelkop.
    """
    enc = soup.find(id="enc_1")
    if enc is not None:
        return enc
    return soup.find(class_=["title-division-1", "title-article-norm"])


def _base_celex(soup, celex: str) -> str:
    """De CELEX van de oorspronkelijke handeling achter een geconsolideerde versie.

    Die staat machineleesbaar in het document zelf: de ►B-pijl linkt naar de
    basishandeling en draagt haar CELEX in het `title`-attribuut. Ontbreekt die,
    dan is het nummer deterministisch af te leiden — sector 0 wordt sector 3.
    """
    for a in soup.select("p.arrow a[href*='/resource/celex/']"):
        if a.get_text(strip=True) not in ("►B", "▼B"):
            continue
        title = (a.get("title") or "").strip().upper()
        if _CELEX_RE.match(title):
            return title
    return _derive_base_celex(celex)


def _derive_base_celex(celex: str) -> str:
    return "3" + celex[1:].split("-")[0]


def _fetch_preamble(base_celex: str, lang: str):
    """De `#pbl_1`-preambule uit de oorspronkelijke handeling, of None.

    Handelingen van vóór ± 2004 hebben dit eli-skelet niet; daar valt niets
    betrouwbaars te selecteren en geeft deze functie None.
    """
    try:
        r = net.documents().get(
            f"http://publications.europa.eu/resource/celex/{base_celex}",
            headers=_cellar_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        return BeautifulSoup(net.decoded_text(r), "lxml").find(id="pbl_1")
    except Exception:
        return None


def _note_tag(soup, text: str):
    p = soup.new_tag("p")
    em = soup.new_tag("em")
    em.string = text
    p.append(em)
    return p


def _fetch_cellar_ecli(ecli: str, lang: str) -> str | None:
    """EU-rechtspraak uit Cellar, geadresseerd op ECLI, of None.

    De ECLI moet url-encoded (`ECLI%3AEU%3AC%3A…`), anders geeft Cellar 404.
    """
    url = f"http://publications.europa.eu/resource/ecli/{quote(ecli, safe='')}"
    r = net.documents().get(
        url, headers=_cellar_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
    )
    if r.status_code == 200:
        return html_to_markdown(net.decoded_text(r))
    if r.status_code == 300:
        return _fetch_multipart(r.text, lang, ecli)
    return None


# Cellar meldt "multiple choices" ook voor documenten die uit meerdere
# HTML-onderdelen bestaan (bv. een wetgevingsvoorstel met een losse bijlage,
# elk als eigen manifestatie). Elk onderdeel staat als "…/DOC_<n>"-link in de
# 300-respons, in documentvolgorde.
_DOC_PART_RE = re.compile(r'href="(https?://publications\.europa\.eu/resource/cellar/[^"]+?/DOC_\d+)"')


def _fetch_multipart(choices_html: str, lang: str, identifier: str) -> str | None:
    """Haal en concateneer de onderdelen uit een Cellar 300-respons.

    Elk onderdeel moet met `Accept: text/html` opgehaald worden — de
    manifestatie-URL zelf heeft `text/html` als resource-mimetype, en een
    `application/xhtml+xml`-verzoek daarop geeft 406.
    """
    urls = _DOC_PART_RE.findall(choices_html)
    if not urls:
        # Geen onderdelen te vinden: waarschijnlijk toch een taalprobleem.
        raise ConversionError(
            f"Document niet beschikbaar in taal {lang} ({identifier}). Probeer een andere taal."
        )
    parts = []
    for part_url in urls:
        pr = net.documents().get(
            part_url, headers={"Accept": "text/html", "Accept-Language": lang.lower()},
            timeout=_CELLAR_TIMEOUT,
        )
        if pr.status_code == 200:
            parts.append(html_to_markdown(net.decoded_text(pr)))
    return "\n\n---\n\n".join(parts) if parts else None


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
