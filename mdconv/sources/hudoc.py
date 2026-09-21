"""EHRM-rechtspraak via HUDOC: de zoek-API voor de metadata, de DOCX voor de tekst.

De tekst komt uit het Word-bestand dat het Hof zelf bewaart:
`…/app/conversion/docx/?library=ECHR&id=<itemid>&filename=<itemid>.docx`. Dat draagt
koppen als stijl, echte voetnoten en tabellen, en is daarmee het bronbewijs
(`hudoc_docx.py`). De HTML-body die deze module tot september 2026 gebruikte
(`…/docx/html/body`) is een conversie van datzelfde bestand en verliest wat het
bestand wel heeft; er is bewust geen terugval op.

De metadata komt uit de zoek-API. Die is kieskeurig: zonder de extra parameters
(`rankingmodelid`, `sort`, `facetquery`, `start`, `length`) geeft hij 404 in plaats van
een resultaat, `select` moet komma-gescheiden en in kleine letters, en hij weigert
(403) verzoeken die er niet uitzien als die van een browser of die te snel komen. Dat
laatste is een storing en geen "niet gevonden", en wordt ook zo gemeld.

Eén ECLI wijst naar meerdere documenten: het origineel (`HEJUD`), de Franse versie
(`HFJUD`), vertalingen en samenvattingen. Alleen het Engelse origineel wordt
ondersteund; de sectienamen in het profiel zijn Engels.
"""

from __future__ import annotations

import re
import time

from .. import net
from ..errors import ConversionError
from ..herkomst import Herkomst
from ..source_structure import record_source
from . import hudoc_docx

# Een EHRM-ECLI, bv. ECLI:CE:ECHR:2021:0525JUD005817013 (Raad van Europa).
ECHR_ECLI_RE = re.compile(r"ECLI:CE:ECHR:\d{4}:[A-Za-z0-9]+", re.I)
# HUDOC-item-id's zien uit als 001-210077. Het id zit vaak in een ge-encodeerd
# URL-fragment (…%22001-210077%22…), dus geen woordgrenzen eisen.
ITEM_ID_RE = re.compile(r"(00\d-\d{3,})")

# HUDOC weigert (403/429) verzoeken die te snel komen. Eén herhaling na een korte pauze is
# genoeg voor een tijdelijke beperking; meer zou de beperking verlengen.
_PAUZE_SECONDEN = 4
_pauze = time.sleep

_QUERY_TIMEOUT = 30
_DOCX_TIMEOUT = 90
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SELECT = ("itemid,ecli,appno,docname,doctype,kpdate,originatingbody,languageisocode,"
           "importance,respondent,article,conclusion")

# Het Engelse origineel van een arrest. `HFJUD` is het Franse origineel.
_ORIGINEEL = "HEJUD"
_FRANS = "HFJUD"


def fetch(query: str, lang: str = "EN") -> tuple[str, str, Herkomst]:
    """Haal een EHRM-uitspraak op; geeft (markdown, bronvermelding, herkomst)."""
    ecli_m = ECHR_ECLI_RE.search(query)
    if ecli_m:
        rij = _kies_origineel(_zoek(f'ecli:"{ecli_m.group(0).upper()}"'), ecli_m.group(0).upper())
    else:
        m = ITEM_ID_RE.search(query)
        if not m:
            raise ConversionError(
                "Geen geldig HUDOC item-id of EHRM-ECLI herkend "
                "(bv. 001-210077, ECLI:CE:ECHR:…, of plak de volledige HUDOC-link)."
            )
        item_id = m.group(1)
        rijen = [r for r in _zoek(f'itemid:"{item_id}"') if r.get("itemid") == item_id]
        if not rijen:
            raise ConversionError(f"Geen HUDOC-document gevonden met item-id {item_id}.")
        rij = rijen[0]
        _controleer_soort(rij)

    item_id = rij["itemid"]
    data = _haal_docx(item_id)
    # `docname` kan eindigen op een notitie van HUDOC zelf (`[Extracts]`); die hoort
    # niet bij de zaaknaam en past niet in het herkomstlabel van de kennisbank, dat
    # geen vierkante haken toestaat. De volledige `docname` blijft in het zijbestand.
    titel = re.sub(r"\s*\[[^\]]*\]\s*$", "", rij["docname"]).strip()
    markdown, meta = hudoc_docx.omzetten(data, titel)

    url = f"https://hudoc.echr.coe.int/eng?i={item_id}"
    record_source(data, media_type=_DOCX_MEDIA_TYPE, source_format="hudoc-docx",
                  source_url=url, identifier=rij["ecli"], language="en")

    waarschuwingen = ["De titel als kop komt uit de HUDOC-record (docname); de omslag staat er "
                      "ongewijzigd onder."]
    herkomst = Herkomst(
        format="hudoc-docx",
        ecli=rij["ecli"],
        title=titel,
        language="en",
        source_url=url,
        requested_url=query,
        koppen_bron=meta["koppen"],
        koppen_markdown=meta["koppen"],
        waarschuwingen=tuple(waarschuwingen),
        extra={
            "itemid": item_id,
            "appno": [a for a in (rij.get("appno") or "").split(";") if a],
            "docname": rij["docname"],
            "doctype": rij.get("doctype"),
            "uitspraakdatum": (rij.get("kpdate") or "")[:10] or None,
            "lichaam": rij.get("originatingbody"),
            "belang": rij.get("importance"),
            "verweerder": rij.get("respondent"),
            "artikelen": [a for a in (rij.get("article") or "").split(";") if a],
            "conclusie": rij.get("conclusion"),
            "randnummers": meta["randnummers"],
            "noten": meta["noten"],
            "velden": meta["velden"],
            "tabellen": meta["tabellen"],
        },
    )
    return markdown, f"HUDOC (EHRM) • {item_id} • {rij['ecli']}", herkomst


def _get(url: str, **kwargs):
    """Een GET met één herhaling wanneer HUDOC het verzoek weigert (403 of 429)."""
    r = net.documents().get(url, **kwargs)
    if r.status_code in (403, 429):
        _pauze(_PAUZE_SECONDEN)
        r = net.documents().get(url, **kwargs)
    return r


def _zoek(query: str, length: int = 30) -> list[dict]:
    """Voer een HUDOC-zoekopdracht uit; geeft de `columns`-dicts per resultaat.

    Alle parameters hieronder zijn verplicht: laat er één weg en de API antwoordt met
    404 in plaats van een (leeg) resultaat. Een 403 of 429 is HUDOC die het verzoek
    weigert en geen leeg resultaat; die krijgt een eigen melding, want "niet gevonden"
    zou de gebruiker een verkeerde oorzaak laten zoeken.
    """
    params = {
        "query": f"contentsitename=ECHR AND {query}",
        "select": _SELECT,
        "sort": "",
        "start": "0",
        "length": str(length),
        "rankingmodelid": "11111_Ranking",
        "facetquery": "",
    }
    r = _get("https://hudoc.echr.coe.int/app/query/results", params=params, timeout=_QUERY_TIMEOUT)
    if r.status_code in (403, 429):
        raise ConversionError(
            f"HUDOC weigerde het zoekverzoek (HTTP {r.status_code}). Dat is een storing of een "
            "beperking op het aantal verzoeken, geen 'niet gevonden'; probeer het over een "
            "minuut opnieuw.")
    if r.status_code != 200:
        raise ConversionError(f"HUDOC gaf HTTP {r.status_code} op het zoekverzoek.")
    try:
        resultaten = r.json().get("results", [])
    except ValueError as exc:
        raise ConversionError("HUDOC gaf geen JSON terug op het zoekverzoek.") from exc
    return [it.get("columns", {}) for it in resultaten]


def _controleer_soort(rij: dict) -> None:
    soort = rij.get("doctype")
    if soort == _ORIGINEEL:
        return
    if soort == _FRANS:
        raise ConversionError(
            f"{rij.get('itemid')} is het Franse origineel ({rij.get('docname')}). Franstalige "
            "uitspraken worden nog niet ondersteund: de sectienamen in het profiel zijn Engels.")
    raise ConversionError(
        f"{rij.get('itemid')} is van de soort {soort!r} ({rij.get('docname')}). Alleen het Engelse "
        "origineel van een arrest (HEJUD) wordt ondersteund; beslissingen, vertalingen en "
        "samenvattingen niet.")


def _kies_origineel(rijen: list[dict], ecli: str) -> dict:
    """Het Engelse origineel bij een ECLI, of een weigering met reden."""
    passend = [r for r in rijen if r.get("itemid") and (r.get("ecli") or "").upper() == ecli]
    if not passend:
        raise ConversionError(f"Geen HUDOC-document gevonden voor {ecli}.")
    engels = [r for r in passend if r.get("doctype") == _ORIGINEEL]
    if len(engels) == 1:
        return engels[0]
    if len(engels) > 1:
        raise ConversionError(
            f"{ecli} wijst naar {len(engels)} Engelse originelen "
            f"({', '.join(r['itemid'] for r in engels)}); geef het item-id.")
    # Geen Engels origineel: zeg waarom in plaats van een vertaling te nemen.
    _controleer_soort(passend[0])
    raise ConversionError(f"Voor {ecli} is geen Engels origineel gevonden.")


def _haal_docx(item_id: str) -> bytes:
    url = ("https://hudoc.echr.coe.int/app/conversion/docx/"
           f"?library=ECHR&id={item_id}&filename={item_id}.docx")
    r = _get(url, timeout=_DOCX_TIMEOUT)
    data = getattr(r, "content", None) or b""
    if r.status_code in (403, 429):
        raise ConversionError(
            f"HUDOC weigerde het verzoek om {item_id} (HTTP {r.status_code}); probeer het over "
            "een minuut opnieuw.")
    if r.status_code != 200 or not data.startswith(b"PK"):
        # Gemeten: sommige uitspraken geven bij het Word-bestand een serverfout van HUDOC
        # (HTTP 500, een ASP.NET-foutpagina), ook recente - 001-160044 is van 2016. Dat is
        # een eigenschap van dat document bij HUDOC en geen "te oud"; de oorzaak wordt hier
        # dus niet geraden.
        raise ConversionError(
            f"HUDOC levert voor {item_id} geen Word-bestand (HTTP {r.status_code}). Dat komt bij "
            "sommige uitspraken voor, ook recente, en er is geen terugval op HTML.")
    return data
