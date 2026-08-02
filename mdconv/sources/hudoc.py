"""EHRM-rechtspraak via HUDOC.

De tekst komt uit de document-export:
`…/app/conversion/docx/html/body?library=ECHR&id=<itemid>`.

Een EHRM-**ECLI** moet eerst naar een itemid worden opgezocht via de zoek-API.
Die API is kieskeurig: zonder de extra parameters (`rankingmodelid`, `sort`,
`facetquery`, `start`, `length`) geeft hij 404 in plaats van een resultaat, en
`select` moet komma-gescheiden en in kleine letters.

Eén ECLI kan naar meerdere documenten wijzen (Engels/Frans origineel plus
vertalingen). Veel vertalingen bestaan alleen als PDF en geven dan een lege
HTML-body (204), dus de kandidaten worden op volgorde geprobeerd tot er één
daadwerkelijk tekst oplevert.
"""

from __future__ import annotations

import re

from .. import net
from ..errors import ConversionError
from ..render import html_to_markdown

# Een EHRM-ECLI, bv. ECLI:CE:ECHR:2021:0525JUD005817013 (Raad van Europa).
ECHR_ECLI_RE = re.compile(r"ECLI:CE:ECHR:\d{4}:[A-Za-z0-9]+", re.I)
# HUDOC-item-id's zien uit als 001-210077. Het id zit vaak in een ge-encodeerd
# URL-fragment (…%22001-210077%22…), dus geen woordgrenzen eisen.
ITEM_ID_RE = re.compile(r"(00\d-\d{3,})")

# De UI-taalkeuze naar HUDOC's drieletterige taalcodes.
_LANG_ISO3 = {
    "NL": "DUT", "EN": "ENG", "FR": "FRE", "DE": "GER",
    "ES": "SPA", "IT": "ITA", "PT": "POR", "PL": "POL",
}

_QUERY_TIMEOUT = 30
_BODY_TIMEOUT = 45
_MIN_USEFUL_LENGTH = 40


def fetch(query: str, lang: str = "EN") -> tuple[str, str]:
    """Haal een EHRM-uitspraak op; geeft (markdown, bronvermelding)."""
    ecli_m = ECHR_ECLI_RE.search(query)
    if ecli_m:
        return _fetch_by_ecli(ecli_m.group(0).upper(), lang)

    m = ITEM_ID_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig HUDOC item-id of EHRM-ECLI herkend "
            "(bv. 001-210077, ECLI:CE:ECHR:…, of plak de volledige HUDOC-link)."
        )
    item_id = m.group(1)
    html = _fetch_body(item_id)
    if not html:
        raise ConversionError(f"Kon HUDOC-document {item_id} niet ophalen (geen HTML-versie).")
    markdown = html_to_markdown(html)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"HUDOC-document {item_id} bevat geen leesbare tekst.")
    return markdown, f"HUDOC (EHRM) • {item_id}"


def _fetch_by_ecli(ecli: str, lang: str) -> tuple[str, str]:
    candidates = _candidates_from_ecli(ecli, lang)
    if not candidates:
        raise ConversionError(f"Geen HUDOC-document gevonden voor {ecli}.")
    for item_id in candidates:
        html = _fetch_body(item_id)
        if html:
            markdown = html_to_markdown(html)
            if len(markdown.strip()) >= _MIN_USEFUL_LENGTH:
                return markdown, f"HUDOC (EHRM) • {item_id} • {ecli}"
    raise ConversionError(
        f"Voor {ecli} is geen tekstversie (HTML) beschikbaar op HUDOC — mogelijk alleen als PDF."
    )


def _search(query: str, select: str, length: int = 30) -> list[dict]:
    """Voer een HUDOC-zoekopdracht uit; geeft de `columns`-dicts per resultaat.

    Alle parameters hieronder zijn verplicht — laat er één weg en de API
    antwoordt met 404 in plaats van een (leeg) resultaat.
    """
    params = {
        "query": query,
        "select": select,
        "sort": "",
        "start": "0",
        "length": str(length),
        "rankingmodelid": "11111_Ranking",
        "facetquery": "",
    }
    r = net.documents().get(
        "https://hudoc.echr.coe.int/app/query/results",
        params=params, timeout=_QUERY_TIMEOUT,
    )
    if r.status_code != 200:
        return []
    try:
        results = r.json().get("results", [])
    except ValueError:
        return []
    return [it.get("columns", {}) for it in results]


def _candidates_from_ecli(ecli: str, lang: str) -> list[str]:
    """HUDOC-item-id's voor een EHRM-ECLI, beste taal eerst.

    Volgorde: gevraagde taal → Engels → Frans → overige taalversies.
    """
    cols = _search(f'ecli:"{ecli}"', "itemid,ecli,languageisocode", length=30)
    matches = [c for c in cols
               if c.get("itemid") and (c.get("ecli") or "").upper() == ecli.upper()]

    ordered: list[str] = []

    def add(code: str) -> None:
        for c in matches:
            if (c.get("languageisocode") or "").upper() == code and c["itemid"] not in ordered:
                ordered.append(c["itemid"])

    for code in (_LANG_ISO3.get(lang.upper()), "ENG", "FRE"):
        if code:
            add(code)
    for c in matches:  # overige taalversies als laatste redmiddel
        if c["itemid"] not in ordered:
            ordered.append(c["itemid"])
    return ordered


def _fetch_body(item_id: str) -> str | None:
    """De HTML-body van een HUDOC-document, of None als die niet bestaat."""
    url = ("https://hudoc.echr.coe.int/app/conversion/docx/html/body"
           f"?library=ECHR&id={item_id}")
    r = net.documents().get(url, timeout=_BODY_TIMEOUT)
    if r.status_code != 200 or not r.text.strip():
        return None
    return net.decoded_text(r)
