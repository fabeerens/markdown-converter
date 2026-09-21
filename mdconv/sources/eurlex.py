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

from bs4 import BeautifulSoup, NavigableString

from .. import net
from ..errors import ConversionError
from ..herkomst import Herkomst
from ..source_structure import record_html, record_source
from ..render import collapse_ws, html_to_markdown, marker_prefix, prefix_into
from . import formex_hof, formex_xml

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
_FORMEX_MEDIA_TYPE = "application/zip;mtype=fmx4"
_FORMEX_LANGUAGES = {"NL": "nld", "EN": "eng", "DE": "deu", "FR": "fra"}


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


def fetch_and_convert(text: str, lang: str = "NL") -> tuple:
    """Los de invoer op naar een document; geeft (markdown, bronvermelding)."""
    lang = (lang or "NL").upper()

    # EU-rechtspraak op ECLI: de Cellar geeft de Formex ook op de ECLI zelf.
    ecli_m = _EU_ECLI_RE.search(text)
    if ecli_m:
        return _fetch_hof(ecli_m.group(0).upper(), lang, requested_url=text)

    celex = extract_celex(text) or eli_to_celex(text)
    if not celex:
        raise ConversionError(
            "Geen geldig CELEX-nummer of EUR-Lex URL herkend. "
            "Voorbeeld: 32016R0679 of "
            "https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32016R0679"
        )

    # Sector 6 is rechtspraak van de Unie. Die heeft een eigen Formex-vorm en een
    # eigen omzetter; de wetgevingsroute hieronder weigert haar (geen `.doc.xml`),
    # en dat kwam als een onbegrijpelijke Formex-melding bij de gebruiker terecht.
    if celex.startswith("6"):
        return _fetch_hof(celex, lang, requested_url=text)

    # De officiële Formex-manifestatie staat vóór de HTML-ladder. Alleen een
    # echte zip activeert de route; een HTML-/metadatarespons is een gewone
    # miss. Een eenmaal ontvangen zip gaat wel fail-closed door de omzetter.
    formex_result, formex_melding = _fetch_formex(celex, lang, requested_url=text)
    if formex_result is not None:
        return formex_result

    # Strategie 1: officiële inhoud uit Cellar (betrouwbaar).
    try:
        markdown = _fetch_cellar(celex, lang)
        if markdown and len(markdown.strip()) > 80:
            return (
                markdown,
                f"EUR-Lex (Cellar HTML) • CELEX:{celex} • {lang}",
                _html_herkomst(celex, lang, text, formex_melding),
            )
    except ConversionError:
        raise
    except Exception:
        pass  # netwerkprobleem bij Cellar: probeer de portal

    # Strategie 1b: een geconsolideerde versie (sector 0) staat alléén in Cellar.
    # Doorvallen naar de geblokkeerde portal maakt van elke oorzaak een
    # netwerkfout; de metadata weet wat er wél is en wat daarvan het dichtst bij
    # de gevraagde versie ligt.
    if celex.startswith("0"):
        markdown, bron = _consolidated_fallback(celex, lang)
        return markdown, bron, _html_herkomst(celex, lang, text, formex_melding)

    # Strategie 2: de EUR-Lex portal (met herhaalpogingen bij HTTP 202).
    html = _fetch_portal_html(celex, lang)
    return (
        html_to_markdown(html),
        f"EUR-Lex portal HTML • CELEX:{celex} • {lang}",
        _html_herkomst(celex, lang, text, formex_melding, portal=True),
    )


def _formex_headers(lang: str) -> dict[str, str]:
    return {
        "Accept": _FORMEX_MEDIA_TYPE,
        "Accept-Language": _FORMEX_LANGUAGES.get(lang.upper(), lang.lower()),
    }


def _response_bytes(response) -> bytes:
    inhoud = getattr(response, "content", None)
    if inhoud is not None:
        return bytes(inhoud)
    tekst = getattr(response, "text", "")
    return tekst.encode(getattr(response, "encoding", None) or "utf-8")


def _formex_zip(celex: str, lang: str):
    """`(bytes, bron_url, None)` bij een echte zip, anders `(None, None, (soort, detail))`.

    `soort` is "niet bereikbaar" (het netwerk) of "niet beschikbaar" (de Cellar
    antwoordt, maar niet met een zip); beide staan letterlijk in de melding.
    """
    url = f"http://publications.europa.eu/resource/celex/{celex}"
    try:
        response = net.documents().get(
            url, headers=_formex_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
        )
    except Exception as exc:  # netwerk is de reden om de bestaande ladder te behouden
        return None, None, ("niet bereikbaar", type(exc).__name__)
    data = _response_bytes(response)
    if response.status_code != 200 or not data.startswith(b"PK"):
        detail = f"HTTP {response.status_code}" if response.status_code != 200 else "antwoord is geen zip"
        return None, None, ("niet beschikbaar", detail)
    return data, getattr(response, "url", "") or url, None


def _fetch_formex(celex: str, lang: str, *, requested_url: str):
    """Geef de Formex-uitkomst, of een melding waarmee HTML verdergaat."""
    data, bron_url, fout = _formex_zip(celex, lang)
    if data is None:
        return None, f"Formex {fout[0]} ({fout[1]}); HTML-route gebruikt."

    record_source(
        data,
        media_type=_FORMEX_MEDIA_TYPE,
        source_format="formex",
        source_url=bron_url,
        identifier=celex,
        language=lang,
    )
    markdown, eenheden, onbekend, extra = formex_xml.omzetten(data)
    if onbekend:
        # De walker weigert dit al; deze grendel voorkomt dat een latere
        # versoepeling de download ongemerkt weer openzet.
        raise ConversionError(f"Formex bevat elementen zonder behandeling: {onbekend}.")
    metadata = extra["metadata"]
    geconsolideerd = metadata.get("format") == "clg"
    waarschuwingen = ["EUR-Lex is via de officiële Formex-manifestatie opgehaald."]
    recitals_from = recitals_reason = None
    if geconsolideerd and not any(e.soort == "overweging" for e in eenheden):
        # CONS.ACT bevat de considerans niet. De HTML-route haalt die uit de
        # basishandeling, en dat doet deze route ook. Lukt het niet, dan mag dat
        # nooit stil zijn: zonder waarschuwing lijkt de tekst compleet terwijl
        # alle rec-ankers ontbreken.
        base = metadata.get("base_celex")
        basis, basis_url, fout = _formex_zip(base, lang) if base else (None, None, ("onbekend", "geen basishandeling in de bron"))
        preambule, reden_basis = (formex_xml.basis_preambule(basis, base) if basis is not None else (None, None))
        if preambule is not None:
            record_source(
                basis,
                media_type=_FORMEX_MEDIA_TYPE,
                source_format="formex",
                source_url=basis_url,
                identifier=base,
                language=lang,
                role="preamble",
            )
            # Een ontvangen basishandeling die de controles niet haalt weigert
            # de hele download, zoals bij de geconsolideerde zip zelf.
            markdown, eenheden, onbekend, extra = formex_xml.omzetten(data, basis)
            if onbekend:
                raise ConversionError(f"Formex bevat elementen zonder behandeling: {onbekend}.")
            metadata = extra["metadata"]
            recitals_from = base
            waarschuwingen.append(_PREAMBLE_NOTE.format(base=base))
        else:
            recitals_reason = (
                f"de Formex van de basishandeling {base} is {fout[0]} ({fout[1]})" if basis is None
                else reden_basis
            )
            waarschuwingen.append(
                "De geconsolideerde Formex bevat geen considerans en die van de "
                f"basishandeling kon niet worden ingevoegd: {recitals_reason}. "
                "De overwegingen en hun rec-ankers ontbreken."
            )
    # De vindplaats en de wijzigende handelingen staan in de bron zelf; wat de
    # omzetter daarbij niet kon lezen, komt als melding mee en is nooit stil.
    waarschuwingen.extend(metadata.get("waarschuwingen", ()))
    herkomst = Herkomst(
        format="clg" if geconsolideerd else "formex",
        celex=celex,
        language=metadata.get("language") or lang.lower(),
        oj_reference=metadata.get("oj_reference"),
        base_celex=metadata.get("base_celex"),
        amendments=tuple(metadata.get("amendments", ())),
        consolidation_date=metadata.get("consolidation_date"),
        version=metadata.get("version"),
        recitals_from=recitals_from,
        recitals_reason=recitals_reason,
        geldend_van=metadata.get("valid_from"),
        geldend_tot=metadata.get("valid_until"),
        source_url=bron_url,
        requested_url=requested_url,
        waarschuwingen=tuple(waarschuwingen),
        extra={
            "formex_eenheden": len(eenheden),
            "herhaalde_tabelcellen": extra["herhaalde_cellen"],
        },
    )
    return (
        markdown,
        f"EUR-Lex (Cellar Formex) • CELEX:{celex} • {lang}",
        herkomst,
    ), None


def _fetch_hof(ident: str, lang: str, *, requested_url: str):
    """Rechtspraak van de Unie: alleen uit de officiële Formex, of een weigering.

    Er is bewust geen terugval op de Curia-HTML. Twee routes per bron betekent twee
    omzetters die allebei bewezen moeten worden, en de HTML van het Hof heeft in de
    kennisbank juist de blokkades opgeleverd die de Formex bij de bron oplost (het
    dictum als lay-outtabel, de procestaalnoot zonder definitie). Een arrest van
    een paar dagen oud heeft nog geen Formex; dan is de weigering de juiste
    uitkomst en de reden staat erbij.
    """
    if ident.upper().startswith("ECLI:"):
        url = f"http://publications.europa.eu/resource/ecli/{quote(ident, safe='')}"
    else:
        url = f"http://publications.europa.eu/resource/celex/{ident}"
    try:
        response = net.documents().get(
            url, headers=_formex_headers(lang), timeout=_CELLAR_TIMEOUT, allow_redirects=True,
        )
    except Exception as exc:
        raise ConversionError(
            f"De Cellar is niet bereikbaar ({type(exc).__name__}); {ident} is niet opgehaald."
        ) from exc
    data = _response_bytes(response)
    if response.status_code != 200 or not data.startswith(b"PK"):
        detail = (f"HTTP {response.status_code}" if response.status_code != 200
                  else "het antwoord is geen zip")
        raise ConversionError(
            f"Voor {ident} is geen Formex-manifestatie in de Cellar ({detail}) in taal "
            f"{lang}. Een arrest van de laatste dagen of weken staat er nog niet; "
            "probeer het later opnieuw. Er is geen terugval op HTML."
        )
    bron_url = getattr(response, "url", "") or url
    markdown, meta = formex_hof.omzetten(data, ident)
    # Pas na de identiteitscontrole vastleggen: een bron die een ander arrest blijkt
    # te zijn hoort niet als bewijs bij deze aanvraag te staan.
    record_source(
        data,
        media_type=_FORMEX_MEDIA_TYPE,
        source_format="formex-hvj",
        source_url=bron_url,
        identifier=meta["ecli"] or meta["celex"],
        language=lang,
    )
    waarschuwingen = ["EUR-Lex is via de officiële Formex-manifestatie opgehaald."]
    if not meta["ecli"]:
        waarschuwingen.append(
            "De bron noemt geen ECLI; het CELEX-nummer is de identiteit van dit document.")
    if meta["opmaak_weggelaten"]:
        waarschuwingen.append(
            f"{meta['opmaak_weggelaten']} keer vet of cursief niet overgenomen; de tekst blijft.")
    herkomst = Herkomst(
        format="formex-hvj",
        celex=meta["celex"],
        ecli=meta["ecli"],
        title=" ".join(meta["titelregels"]) or None,
        language=meta["taal"] or lang.lower(),
        source_url=bron_url,
        requested_url=requested_url,
        koppen_bron=len(meta["secties"]),
        koppen_markdown=len(meta["secties"]),
        waarschuwingen=tuple(waarschuwingen),
        extra={k: meta[k] for k in ("zaaknummers", "auteur", "partijen", "overige_partijen",
                                    "secties", "paginakop", "soort", "noten", "bronbestand",
                                    "titelregels", "datum", "procestaal",
                                    "dictum_inleiding")},
    )
    label = ident if ident.upper().startswith("ECLI:") else f"CELEX:{ident}"
    return markdown, f"EUR-Lex (Cellar Formex) • {label} • {lang}", herkomst


def _html_herkomst(celex: str, lang: str, requested_url: str,
                    formex_melding: str | None, *, portal: bool = False) -> Herkomst:
    route = "EUR-Lex-portal" if portal else "Cellar"
    waarschuwingen = tuple(x for x in (formex_melding, f"{route}-HTML-route gebruikt.") if x)
    return Herkomst(
        format="eurlex-html",
        celex=celex,
        language=lang.lower(),
        source_url=(f"https://eur-lex.europa.eu/legal-content/{lang}/TXT/?uri=CELEX:{celex}"
                    if portal else f"http://publications.europa.eu/resource/celex/{celex}"),
        requested_url=requested_url,
        waarschuwingen=waarschuwingen,
    )


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
        record_html(html, source_url=getattr(r, "url", "") or url, identifier=celex, language=lang)
        if _CONSOLIDATED_RE.match(celex):
            html = _prepare_consolidated(html, celex, lang)
        return html_to_markdown(html)
    if r.status_code == 300:
        return _fetch_multipart(r.text, lang, f"CELEX:{celex}", celex)
    # Elke andere status is hier gewoon een miss (404, maar ook 406 als de taal
    # niet bestaat). Waaróm een sector-0-CELEX niet op te halen is — datum
    # bestaat niet, of de versie is er niet in deze taal — staat in de metadata
    # en niet in de statuscode. Dat uitzoeken, en de beste beschikbare versie
    # kiezen, doet _consolidated_fallback().
    return None


# --------------------------------------------------------------------------
# Geconsolideerde versies: welke bestaan er, en in welke talen?
# --------------------------------------------------------------------------
#
# Een 404 van Cellar op een sector-0-CELEX zegt niet wát er mis is. Twee heel
# verschillende oorzaken geven exact dezelfde status:
#
#   * de gevraagde consolidatiedatum bestaat niet — consolidatiedata liggen
#     vast, één per wijziging;
#   * de versie bestaat wél, maar is (nog) niet in de gevraagde taal. EUR-Lex
#     consolideert taal per taal en loopt daarin achter.
#
# Die tweede is niet exotisch. Geverifieerd: 02024R2979-20241204 bestaat alleen
# in het Iers en Zweeds, 02026R0798-20260408 alleen in het Duits en Ests, en van
# eIDAS bestaat 02014R0910-20140917 in 9 van de 24 talen. Zonder dit onderscheid
# meldde de tool in al die gevallen dat de datum niet bestond — feitelijk onjuist
# — en gaf ze niets terug, terwijl de Nederlandse tekst van de handeling zelf wél
# op te halen is.
#
# Het onderscheid staat in de metadata, keyless op te vragen bij het
# SPARQL-endpoint van het Publicatiebureau: dezelfde bron als "Alle versies van
# dit document" op de portal.

_SPARQL_URL = "http://publications.europa.eu/webapi/rdf/sparql"
_SPARQL_TIMEOUT = 30

# Hoeveel oudere geconsolideerde versies we maximaal proberen voordat we op de
# oorspronkelijke handeling terugvallen. De metadata zegt al in welke taal een
# versie bestaat, dus in de praktijk is de eerste kandidaat de goede; de grens
# is er zodat een handeling met dertig versies geen dertig verzoeken uitlokt.
_FALLBACK_PROBES = 3

# De EU-talen in de codes die Cellar gebruikt (drieletterig in de metadata,
# tweeletterig in `Accept-Language` — vandaar de brug), met hun Nederlandse naam
# voor de meldingen.
_EU_LANGUAGES = {
    "BG": ("BUL", "Bulgaars"), "CS": ("CES", "Tsjechisch"), "DA": ("DAN", "Deens"),
    "DE": ("DEU", "Duits"), "EL": ("ELL", "Grieks"), "EN": ("ENG", "Engels"),
    "ES": ("SPA", "Spaans"), "ET": ("EST", "Ests"), "FI": ("FIN", "Fins"),
    "FR": ("FRA", "Frans"), "GA": ("GLE", "Iers"), "HR": ("HRV", "Kroatisch"),
    "HU": ("HUN", "Hongaars"), "IT": ("ITA", "Italiaans"), "LT": ("LIT", "Litouws"),
    "LV": ("LAV", "Lets"), "MT": ("MLT", "Maltees"), "NL": ("NLD", "Nederlands"),
    "PL": ("POL", "Pools"), "PT": ("POR", "Portugees"), "RO": ("RON", "Roemeens"),
    "SK": ("SLK", "Slowaaks"), "SL": ("SLV", "Sloveens"), "SV": ("SWE", "Zweeds"),
}
_LANGUAGE_NAMES = {code: name for code, name in _EU_LANGUAGES.values()}


def _nl_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}-{yyyymmdd[4:6]}-{yyyymmdd[0:4]}"


def _join_nl(items: list[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " en " + items[-1]


def _language_list(codes: set[str]) -> str:
    """De talen bij naam, of hun aantal als de lijst te lang wordt om te lezen."""
    names = sorted(_LANGUAGE_NAMES.get(c, c) for c in codes)
    if len(names) > 5:
        return f"in {len(names)} van de {len(_EU_LANGUAGES)} talen"
    return "in het " + _join_nl(names)


def _consolidated_index(act: str) -> dict[str, set[str]] | None:
    """Per geconsolideerde versie van `act` de talen waarin die bestaat.

    `act` is een sector-0-CELEX zónder datum (`02014R0910`). None betekent "niet
    te achterhalen" (endpoint onbereikbaar) — iets anders dan een leeg antwoord
    ("deze handeling is nooit geconsolideerd"), en de ladder hieronder behandelt
    het ook anders.
    """
    if not re.fullmatch(r"0[0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?", act, re.I):
        return None
    query = (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
        "SELECT DISTINCT ?celex ?lang WHERE {\n"
        "  ?w cdm:resource_legal_id_celex ?celex .\n"
        "  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language ?lang .\n"
        f'  FILTER(STRSTARTS(STR(?celex), "{act.upper()}"))\n'
        "}"
    )
    try:
        r = net.documents().get(
            _SPARQL_URL,
            params={"query": query, "format": "application/sparql-results+json"},
            timeout=_SPARQL_TIMEOUT,
        )
        rows = r.json()["results"]["bindings"]
    except Exception:
        return None
    index: dict[str, set[str]] = {}
    for row in rows:
        celex = (row.get("celex", {}).get("value") or "").upper()
        lang = (row.get("lang", {}).get("value") or "").rsplit("/", 1)[-1].upper()
        if _CONSOLIDATED_RE.match(celex):
            index.setdefault(celex, set()).add(lang)
    return index


def _fallback_reason(index, celex: str, wanted: str, lang: str, code: str | None) -> str:
    """Één zin: waarom de gevraagde geconsolideerde versie er niet is.

    Gedeeld door de notitie boven een terugvaltekst en de foutmelding als de
    hele ladder faalt, zodat die twee nooit iets anders kunnen beweren.
    """
    date = _nl_date(wanted)
    langname = _EU_LANGUAGES.get(lang, (None, lang))[1]
    if index is None:
        return (
            f"De geconsolideerde versie per {date} was niet op te halen bij EUR-Lex, "
            "en de lijst met beschikbare versies ook niet."
        )
    langs = index.get(celex.upper())
    if langs is not None and code and code not in langs:
        return (
            f"De geconsolideerde versie per {date} bestaat op EUR-Lex, maar (nog) niet "
            f"in het {langname} — alleen {_language_list(langs)}. EUR-Lex consolideert "
            "taal per taal."
        )
    if langs is not None:
        return (
            f"De geconsolideerde versie per {date} bestaat op EUR-Lex, ook in het "
            f"{langname}, maar was nu niet op te halen."
        )
    if not index:
        return (
            f"EUR-Lex heeft geen geconsolideerde versie per {date}: deze handeling is "
            "nooit geconsolideerd."
        )
    # Noem alleen de datums die in déze taal te krijgen zijn: dat is wat de
    # gebruiker met de melding kan doen.
    mine = sorted(
        (c.rsplit("-", 1)[-1] for c in index if not code or code in index[c]), reverse=True
    )
    if mine:
        shown = _join_nl([_nl_date(d) for d in mine[:5]])
        more = " (en ouder)" if len(mine) > 5 else ""
        return (
            f"EUR-Lex heeft geen geconsolideerde versie per {date}. Consolidatiedata "
            f"liggen vast, één per wijziging; in het {langname} bestaan de versies per "
            f"{shown}{more}."
        )
    every = sorted((c.rsplit("-", 1)[-1] for c in index), reverse=True)
    shown = _join_nl([_nl_date(d) for d in every[:5]])
    more = " (en ouder)" if len(every) > 5 else ""
    return (
        f"EUR-Lex heeft geen geconsolideerde versie per {date}, en geen van de versies "
        f"die er zijn ({shown}{more}) bestaat in het {langname}."
    )


def _base_act_tail(index, before: list[str], earlier: list[str], lang: str) -> str:
    """Waarom er geen eerdere geconsolideerde versie in de plaats komt.

    Drie verschillende gevallen die makkelijk door elkaar lopen — en waarvan er
    twee eerder onterecht als "die bestaat niet" werden gemeld, terwijl de
    notitie in dezelfde alinea de bestaande versies opsomde.
    """
    langname = _EU_LANGUAGES.get(lang, (None, lang))[1]
    if index is None:
        return "welke eerdere geconsolideerde versies er zijn, was niet na te gaan."
    if not before:
        return "EUR-Lex heeft geen eerdere geconsolideerde versie van deze handeling."
    if not earlier:
        return f"de eerdere geconsolideerde versies bestaan niet in het {langname}."
    return "een eerdere geconsolideerde versie was niet op te halen."


def _try_cellar(celex: str, lang: str) -> str | None:
    """`_fetch_cellar` zonder scherpe kanten: elke storing wordt None."""
    try:
        markdown = _fetch_cellar(celex, lang)
    except Exception:
        return None
    if markdown and len(markdown.strip()) > 80:
        return markdown
    return None


def _with_note(markdown: str, note: str, label: str) -> str:
    """Zet de notitie als blockquote met label boven de tekst.

    Niet als volledig cursieve regel: dat is in Markdown nadruk, geen
    herkomstvermelding, en een intakepoort die op `^\\*[^*\\n]{20,}\\*\\s*$`
    filtert rekent zo'n regel terecht af als opmaakruis. Een blockquote met
    label zegt wát de mededeling is en blijft zichtbaar — "nooit stil" blijft
    dus overeind.
    """
    return f"> **{label}:** {collapse_ws(note)}\n\n{markdown}"


def _consolidated_fallback(celex: str, lang: str) -> tuple[str, str]:
    """De beste beschikbare tekst als de gevraagde geconsolideerde versie niet lukt.

    Terugvalladder:

    1. de nieuwste geconsolideerde versie **vóór** de gevraagde datum die wél in
       deze taal bestaat — dat is precies de versie die op de gevraagde datum
       gold, dus geen concessie maar het juiste antwoord;
    2. de oorspronkelijke handeling in deze taal;
    3. een foutmelding die uit de metadata zegt wát er aan de hand is.

    Latere versies blijven buiten de ladder: die verwerken wijzigingen die op de
    gevraagde datum nog niet golden. Elke terugval zet een notitie als blockquote boven
    de tekst én noemt de afwijking in de bronvermelding — een ander document dan
    gevraagd stil doorgeven is de val die deze code eerder maakte, en dan lijkt de
    oorspronkelijke handeling de geconsolideerde versie te zijn.
    """
    m = _CONSOLIDATED_RE.match(celex)
    if not m:
        raise ConversionError(_consolidated_error(celex))
    wanted = m.group(1)
    base = _derive_base_celex(celex)
    index = _consolidated_index(celex.split("-")[0])
    code = _EU_LANGUAGES.get(lang, (None, lang))[0]
    reason = _fallback_reason(index, celex, wanted, lang, code)

    before = [c for c in (index or {}) if c.rsplit("-", 1)[-1] < wanted]
    earlier = sorted(
        (c for c in before if not (code and code not in index[c])), reverse=True
    )
    for cand in earlier[:_FALLBACK_PROBES]:
        markdown = _try_cellar(cand, lang)
        if markdown is None:
            continue
        got = _nl_date(cand.rsplit("-", 1)[-1])
        note = (
            f"{reason} Hieronder staat de geconsolideerde versie per {got} "
            f"(CELEX:{cand}) — de nieuwste versie op of vóór de gevraagde datum."
        )
        source = (
            f"EUR-Lex (Cellar) • CELEX:{cand} • {lang} • "
            f"i.p.v. de gevraagde versie per {_nl_date(wanted)}"
        )
        return _with_note(markdown, note, "Herkomst"), source

    markdown = _try_cellar(base, lang)
    if markdown is not None:
        note = (
            f"{reason} Hieronder staat de oorspronkelijke handeling (CELEX:{base}), "
            f"niet een geconsolideerde versie: "
            f"{_base_act_tail(index, before, earlier, lang)}"
        )
        source = (
            f"EUR-Lex (Cellar) • CELEX:{base} • {lang} • oorspronkelijke handeling "
            f"i.p.v. de geconsolideerde versie per {_nl_date(wanted)}"
        )
        return _with_note(markdown, note, "Herkomst"), source

    raise ConversionError(
        f"{reason} Ook de oorspronkelijke handeling (CELEX:{base}) was niet op te "
        f"halen in taal {lang}."
    )


def _consolidated_error(celex: str) -> str:
    """Een sector-0-CELEX zonder geldige datum: daar valt niets te repareren."""
    base = _derive_base_celex(celex)
    return (
        f"CELEX:{celex} ziet eruit als een geconsolideerde versie, maar mist een "
        "geldige datum. Zo'n nummer heeft de vorm 02014R0910-20241018 (jjjjmmdd). "
        f"Voor de oorspronkelijke handeling: CELEX:{base}."
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

# --------------------------------------------------------------------------
# CLG-markup is niet OJ-markup
# --------------------------------------------------------------------------
#
# De geconsolideerde XHTML (CLG) zet een lidnummer NIET in een tweekoloms
# tabel — waar `render._unwrap_marker_tables` op wacht — maar in div/span-markup:
#
#   <div class="norm"><span class="no-parag">1.&nbsp;&nbsp;</span>
#     <div class="norm inline-element"><p>Om de goede werking …</p></div></div>
#
#   <div class="grid-container grid-list">
#     <div class="list grid-list-column-1"><span>a)&nbsp;</span></div>
#     <div class="grid-list-column-2"><p class="norm">de doelstellingen …</p></div></div>
#
# In beide gevallen staat de tekst in de *volgende sibling* van de marker. Zonder
# samenvoeging wordt elk nummer een eigen alinea (280 stuks in 02019R0881-20250204)
# en leest Markdown het als een leeg lijstitem.
#
# Voetnootankers dragen bovendien letterlijke newlines binnen de `<a>`; `strip=["a"]`
# haalt de tag weg maar niet de regeleinden, en `tidy()` voegt regels nooit samen —
# vandaar `(`, het cijfer en `)` elk op een eigen regel.
#
# Beide vormen komen 0x voor in de basishandeling (daar zijn het echte tabellen),
# dus dit hoort hier en niet in de gedeelde `render.py`, die ook HUDOC bedient.

_CLG_MARKERS = "span.no-parag, div.grid-list-column-1"
# Het nootcijfer staat op een eigen regel bínnen het anker: `(<a>\n<span>1</span>\n</a>)`.
# Daar moet de witruimte helemaal weg — een spatie zou er "( 1 )" van maken, terwijl
# de haakjes in de bron buiten het anker staan en er dus "(1)" hoort te komen.
_CLG_NOTE_ANCHORS = 'a[href^="#"]:has(> span.superscript)'

# Witruimte in HTML is niet betekenisdragend, maar markdownify neemt een newline
# uit de bron letterlijk over. De CLG-bron is pretty-printed, dus een voetnootanker
# (`(<a>\n<span>1</span>\n</a>)`) en een inline ►M1-markering midden in een alinea
# vallen zo uiteen over drie regels. Alles behalve `pre`/`code` mag dus veilig
# genormaliseerd worden; blokgrenzen zijn tags, geen witruimte.
_SOURCE_NEWLINE_RE = re.compile(r"\s*\n\s*")
_VERBATIM_TAGS = ("pre", "code", "textarea", "script", "style")


def _normalise_clg_markup(html: str) -> str:
    """Zet elke CLG-marker bij de tekst die erbij hoort."""
    soup = BeautifulSoup(html, "lxml")

    # Achterstevoren, dus binnenste marker eerst: een geneste marker moet in zijn
    # blok staan vóórdat een buitenste dat blok als geheel van een prefix voorziet.
    # De lus gaat over de markers, niet over de containers — `div.norm` nest in
    # `div.norm.inline-element`, dus een containerlus zou zichzelf in lopen.
    # Geen `_is_marker`-filter: bij een tabel is de vorm het enige houvast, hier
    # zegt de klassenaam het al. Dat filter zou hier juist schade doen — "c bis)"
    # haalt de lengtegrens maar faalt op het patroon en zou stil verdwijnen.
    for marker in reversed(soup.select(_CLG_MARKERS)):
        block = marker.find_next_sibling()
        if block is None or not block.get_text(strip=True):
            continue
        # Een wijzigingsmarkering (▼M1) kan vóór de eigenlijke tekst staan. Die
        # hoort op haar eigen regel te blijven — en mag het lidnummer niet
        # opslokken, want dan raakt het nummer alsnog los van zijn tekst.
        container = marker.parent
        while True:
            first = block.find(recursive=False)
            if first is None or "modref" not in (first.get("class") or []):
                break
            container.insert_before(first.extract())
        prefix_into(soup, block, marker_prefix(collapse_ws(marker.get_text())))
        marker.decompose()

    for anchor in soup.select(_CLG_NOTE_ANCHORS):
        anchor.string = collapse_ws(anchor.get_text())

    return str(soup)


def _collapse_source_newlines(html: str) -> str:
    """Maak van een newline uit de bron weer gewone witruimte.

    Geldt voor élke EUR-Lex-markup, niet alleen CLG: ook de uit de basishandeling
    ingevoegde `p.oj-note` breekt anders tussen het nootnummer en zijn tekst.
    Daarom draait dit ná het invoegen van de preambule, terwijl het samenvoegen
    van markers — dat wél op CLG-klassen selecteert — eraan voorafgaat.
    """
    soup = BeautifulSoup(html, "lxml")
    for text in soup.find_all(string=True):
        if text.find_parent(_VERBATIM_TAGS) is not None:
            continue
        collapsed = _SOURCE_NEWLINE_RE.sub(" ", text)
        if collapsed != text:
            text.replace_with(collapsed)
    return str(soup)


def _prepare_consolidated(
    html: str, celex: str, lang: str, *, with_preamble: bool = True
) -> str:
    """Normaliseer de CLG-markup en zet daarna pas de preambule terug.

    De volgorde is de ingreep, niet een detail. De preambule komt uit de
    *basishandeling* en draagt OJ-markup (`span.oj-super`); het CLG-document
    gebruikt `span.superscript`. Die vocabulaires zijn vandaag disjunct, dus
    andersom zou nu toevallig ook goed gaan — maar dan berust dat op een meting
    in plaats van op structuur. Zo kan de CLG-normalisatie de ingevoegde
    preambule domweg niet raken.
    """
    html = _normalise_clg_markup(html)
    if with_preamble:
        html = _with_base_preamble(html, celex, lang)
    return _collapse_source_newlines(html)


_PREAMBLE_NOTE = (
    "Overwegingen en aanhef zijn overgenomen uit de oorspronkelijke handeling "
    "(CELEX:{base}); de geconsolideerde tekst op EUR-Lex bevat deze niet."
)
_PREAMBLE_MISSING_NOTE = (
    "De overwegingen uit de oorspronkelijke handeling (CELEX:{base}) konden niet "
    "worden opgehaald; hieronder staat alleen de geconsolideerde tekst."
)
_PREAMBLE_ANCHOR_NOTE = (
    "De overwegingen uit de oorspronkelijke handeling (CELEX:{base}) konden niet "
    "op hun plek worden gezet: dit document mist de gebruikelijke structuur. "
    "Hieronder staat alleen de geconsolideerde tekst."
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
        # Geen invoegpunt: de overwegingen kunnen nergens heen. Ook dát moet
        # gezegd worden — zwijgend de geconsolideerde tekst teruggeven laat hem
        # compleet lijken terwijl de aanhef en álle overwegingen ontbreken. De
        # basishandeling wordt hier niet opgehaald; er valt toch niets te plaatsen.
        note = _note_tag(
            soup, _PREAMBLE_ANCHOR_NOTE.format(base=_base_celex(soup, celex)),
            "Overwegingen",
        )
        title = soup.find(id="tit_1")
        if title is not None:
            title.insert_before(note)
        else:
            (soup.body or soup).insert(0, note)
        return str(soup)

    base = _base_celex(soup, celex)
    preamble = _fetch_preamble(base, lang)
    note = _PREAMBLE_NOTE if preamble is not None else _PREAMBLE_MISSING_NOTE
    anchor.insert_before(_note_tag(soup, note.format(base=base), "Overwegingen"))
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
        original_html = net.decoded_text(r)
        soup = BeautifulSoup(original_html, "lxml")
        pbl = soup.find(id="pbl_1")
        if pbl is not None:
            record_html(original_html, source_url=getattr(r, "url", "") or f"http://publications.europa.eu/resource/celex/{base_celex}", identifier=base_celex, language=lang, role="preamble")
            _attach_preamble_notes(soup, pbl)
        return pbl
    except Exception:
        return None


def _attach_preamble_notes(soup, pbl) -> None:
    """Verhuis de voetnootdefinities van de overwegingen mee ín de preambule.

    De `p.oj-note`-definities staan niet ín `#pbl_1` maar ernaast, als siblings
    binnen `div.eli-container`. Wie alleen `#pbl_1` kopieert neemt de overwegingen
    mét hun verwijzingen (1)(2)(3) mee, maar laat de definities achter — dode
    verwijzingen, en niets dat dat meldt.

    Wélke noten meemoeten volgt uit de verwijzingen zelf: een noot hoort bij de
    preambule dan en slechts dan als een anker bínnen `#pbl_1` ernaartoe wijst.
    Zo blijven de noten van de artikelen vanzelf achter — die heeft de
    geconsolideerde tekst zelf al — zonder dat er ergens een aantal in de code
    staat dat bij de volgende handeling niet meer klopt.
    """
    wanted = set()
    for a in pbl.select('a[href^="#"]'):
        target = soup.find(id=a["href"][1:])
        if target is None:
            continue
        note = target.find_parent("p", class_="oj-note")
        if note is not None:
            wanted.add(id(note))
    if not wanted:
        return

    # `soup.select` geeft documentvolgorde; de `hr.oj-note` ervoor blijft bewust
    # staan, want die zou als `---` midden in de preambule terechtkomen.
    notes = [p for p in soup.select("p.oj-note") if id(p) in wanted]

    # Ná de laatste overweging, dus vóór de vaststellingsformule — precies waar
    # het origineel ze ook heeft. Die formule hoort tegen de artikelen aan.
    recitals = pbl.select("div.eli-subdivision[id^='rct_']")
    anchor = recitals[-1] if recitals else None
    for note in notes:
        if anchor is None:
            pbl.append(note.extract())
        else:
            anchor.insert_after(note.extract())
            anchor = note


def _note_tag(soup, text: str, label: str):
    """Een notitie als `<blockquote><p><strong>Label:</strong> …</p></blockquote>`.

    markdownify maakt daar één regel `> **Label:** …` van. Zie `_with_note` voor
    waarom het geen cursieve regel meer is.
    """
    quote = soup.new_tag("blockquote")
    p = soup.new_tag("p")
    strong = soup.new_tag("strong")
    strong.string = f"{label}:"
    p.append(strong)
    p.append(NavigableString(f" {text}"))
    quote.append(p)
    return quote


# Cellar meldt "multiple choices" ook voor documenten die uit meerdere
# HTML-onderdelen bestaan (bv. een wetgevingsvoorstel met een losse bijlage,
# elk als eigen manifestatie). Elk onderdeel staat als "…/DOC_<n>"-link in de
# 300-respons, in documentvolgorde.
_DOC_PART_RE = re.compile(r'href="(https?://publications\.europa\.eu/resource/cellar/[^"]+?/DOC_\d+)"')


def _fetch_multipart(
    choices_html: str, lang: str, identifier: str, celex: str | None = None
) -> str | None:
    """Haal en concateneer de onderdelen uit een Cellar 300-respons.

    Elk onderdeel moet met `Accept: text/html` opgehaald worden — de
    manifestatie-URL zelf heeft `text/html` als resource-mimetype, en een
    `application/xhtml+xml`-verzoek daarop geeft 406.

    Een geconsolideerde versie kan óók langs deze weg binnenkomen, en kreeg dan
    preambule noch notitie: deze route sloeg de hele voorbewerking over. Met
    `celex` erbij gaat elk onderdeel door dezelfde poort als de 200-tak.
    """
    urls = _DOC_PART_RE.findall(choices_html)
    if not urls:
        # Geen onderdelen te vinden: waarschijnlijk toch een taalprobleem.
        raise ConversionError(
            f"Document niet beschikbaar in taal {lang} ({identifier}). Probeer een andere taal."
        )
    consolidated = bool(celex and _CONSOLIDATED_RE.match(celex))
    parts = []
    for index, part_url in enumerate(urls):
        pr = net.documents().get(
            part_url, headers={"Accept": "text/html", "Accept-Language": lang.lower()},
            timeout=_CELLAR_TIMEOUT,
        )
        if pr.status_code != 200:
            continue
        html = net.decoded_text(pr)
        record_html(html, source_url=getattr(pr, "url", "") or part_url, identifier=celex or identifier, language=lang, role="document-part")
        if consolidated:
            # De preambule hoort één keer in het geheel, in het eerste onderdeel
            # (de handeling zelf); de rest zijn bijlagen. `index == 0` in plaats
            # van een vlag-op-succes houdt dat deterministisch.
            html = _prepare_consolidated(
                html, celex, lang, with_preamble=(index == 0),
            )
        parts.append(html_to_markdown(html))
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
    html = net.decoded_text(r)
    record_html(html, source_url=getattr(r, "url", "") or url, identifier=celex, language=lang)
    return html
