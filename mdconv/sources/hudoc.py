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

Sinds september 2026 staat HUDOC achter een botcontrole van Cloudflare die de Python-client
tegenhoudt: 403, `server: cloudflare`, een pagina "Just a moment...". Hetzelfde verzoek met
curl kreeg een 200, en twaalf vragen bleven over drie kwartier 403 (kennisbank, foutlog
`Fouten_test_25_09.md`, T2-F5). Dat is geen verzoeklimiet en wordt ook niet zo gemeld. De
controle wordt bewust niet omzeild; wie de bestanden zelf in de browser downloadt, zet ze om
met `uit_bestand()` (opdrachtregel: `kb_fetch --hudoc-map`), door dezelfde code als hier.

Eén ECLI wijst naar meerdere documenten: het origineel (`HEJUD`), de Franse versie
(`HFJUD`), vertalingen en samenvattingen. Alleen het Engelse origineel wordt
ondersteund; de sectienamen in het profiel zijn Engels.
"""

from __future__ import annotations

import datetime as dt
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

# De botcontrole herkennen aan wat T2-F5 mat: de `server`-kop, of de titel van de
# wachtpagina als een proxy die kop weghaalt.
_BOTCONTROLE_BODY = b"Just a moment"
_BOTCONTROLE = (
    "HUDOC laat deze client niet toe: HTTP 403 van een Cloudflare-botcontrole (\"Just a "
    "moment...\"). Dit is geen verzoeklimiet, dus later opnieuw proberen helpt niet. Download "
    "het Word-bestand en het zoekresultaat in de browser en zet ze om met "
    "`python -m mdconv.kb_fetch --hudoc-map <map> --uit <map>`.")

# Cloudflare voor HUDOC geeft een 403-challenge aan de nagebootste Chrome-UA van
# net.documents() (past niet bij requests' TLS-vingerafdruk); een neutrale UA komt door.
_HEADERS = {"User-Agent": "Mozilla/5.0"}


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

    return omzetten_record(_haal_docx(rij["itemid"]), rij, requested_url=query)


def omzetten_record(data: bytes, rij: dict, *, requested_url: str) -> tuple[str, str, Herkomst]:
    """Het Word-bestand bij een HUDOC-record als (markdown, bronvermelding, herkomst).

    Dit is alles wat `fetch()` doet nadat het record gekozen en het bestand binnen is, en
    het is de enige plek waar dat gebeurt: de lokale route (`uit_bestand()`, bytes die de
    gebruiker zelf in de browser heeft gedownload) gaat door precies deze regels, zodat de
    twee routes op dezelfde bytes niet uit elkaar kunnen lopen.
    """
    item_id = rij["itemid"]
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
        requested_url=requested_url,
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


def _docx_url(item_id: str) -> str:
    return ("https://hudoc.echr.coe.int/app/conversion/docx/"
            f"?library=ECHR&id={item_id}&filename={item_id}.docx")


def _botcontrole(r) -> bool:
    """Is dit de Cloudflare-controle en niet HUDOC zelf die weigert?

    Alleen bij een 403: een 429 is wél een verzoeklimiet. `headers` en `content` zijn er
    niet bij elk antwoord (de testvervangers hebben ze niet altijd), dus defensief lezen.
    """
    if r.status_code != 403:
        return False
    koppen = {str(k).lower(): str(v) for k, v in (getattr(r, "headers", None) or {}).items()}
    if "cloudflare" in koppen.get("server", "").lower():
        return True
    return _BOTCONTROLE_BODY in (getattr(r, "content", None) or b"")[:65536]


def _get(url: str, **kwargs):
    """Een GET met één herhaling wanneer HUDOC het verzoek weigert (403 of 429).

    Elk HUDOC-verzoek gaat met de neutrale User-Agent van `_HEADERS` (Floris, 6 oktober 2026):
    de nagebootste Chrome-UA van `net.documents()` past niet bij de TLS-vingerafdruk van
    `requests`, en dat is wat de Cloudflare-controle ziet. Een eigen `headers` in `kwargs` wint.
    """
    kwargs.setdefault("headers", _HEADERS)
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
    if _botcontrole(r):
        raise ConversionError(_BOTCONTROLE)
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
    r = _get(_docx_url(item_id), timeout=_DOCX_TIMEOUT)
    data = getattr(r, "content", None) or b""
    if _botcontrole(r):
        raise ConversionError(f"{item_id}: {_BOTCONTROLE}")
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


# --------------------------------------------------------------------------
# De lokale route: bytes die de gebruiker zelf in de browser heeft gedownload
# --------------------------------------------------------------------------

HANDMATIG = ("Het Word-bestand en het HUDOC-record zijn handmatig in de browser gedownload en "
             "niet door de converter opgehaald; fetched_at is de wijzigingstijd van het bestand.")


def uit_bestand(data: bytes, rij: dict, alle: list[dict], *, bestand: str, downloadtijd: float,
                records_bestand: str, records_sha256: str) -> tuple[str, str, Herkomst]:
    """Een lokaal gedownload Word-bestand met zijn record, als (markdown, bron, herkomst).

    **Waarom.** De botcontrole van T2-F5 houdt de converter buiten, niet de gebruiker. Die
    downloadt het bestand van exact de URL die `_haal_docx()` vraagt, en het record uit
    exact de zoek-API die `_zoek()` vraagt; vanaf daar is dit dezelfde omzetting
    (`omzetten_record()`), met dezelfde weigeringen:

    - `_controleer_soort()`: alleen het Engelse origineel (HEJUD), met dezelfde melding;
    - `_kies_origineel()` over alle records van de map, zodat twee Engelse originelen
      onder één ECLI ook hier een keuze van de gebruiker blijven en geen gok;
    - een bestand dat niet met `PK` begint is geen Word-bestand, maar vrijwel zeker de
      wachtpagina van Cloudflare of een foutpagina die de browser heeft bewaard.

    **Herkomst eerlijk houden.** `source_url` blijft de HUDOC-pagina, want de bytes zijn die
    van HUDOC; `requested_url` is de download-URL. Dat de converter ze niet zelf heeft
    opgehaald staat als waarschuwing en onder `extra.handmatig` in het zijbestand. Dat is
    een extra sleutel en geen nieuw veld in het bronbewijs, dus geen schemabump aan de
    kennisbankkant (regel 1 van haar `AGENTS.md`). `fetched_at` is de wijzigingstijd van het
    bestand: het moment dat de bytes binnenkwamen, en niet dat van de omzetting.
    """
    _controleer_soort(rij)
    item_id = rij["itemid"]
    ecli = (rij.get("ecli") or "").upper()
    if not ECHR_ECLI_RE.fullmatch(ecli):
        raise ConversionError(f"Het record van {item_id} draagt geen EHRM-ECLI ({rij.get('ecli')!r}).")
    if _kies_origineel(alle, ecli) is not rij:
        raise ConversionError(f"Voor {ecli} is een ander record dan {item_id} het Engelse origineel.")
    if not data.startswith(b"PK"):
        raise ConversionError(
            f"{bestand} begint niet met PK en is dus geen Word-bestand; waarschijnlijk heeft de "
            "browser de Cloudflare-controle of een foutpagina van HUDOC bewaard. Download het "
            "opnieuw.")

    markdown, note, herkomst = omzetten_record(data, rij, requested_url=_docx_url(item_id))
    moment = dt.datetime.fromtimestamp(downloadtijd, dt.timezone.utc)
    extra = dict(herkomst.extra)
    extra["handmatig"] = {
        "bestand": bestand,
        "download_url": _docx_url(item_id),
        "records": records_bestand,
        "records_sha256": records_sha256,
        "fetched_at_uit": "wijzigingstijd van het bestand",
    }
    return markdown, note, herkomst.met(
        fetched_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        geraadpleegd=dt.datetime.fromtimestamp(downloadtijd).date().isoformat(),
        waarschuwingen=herkomst.waarschuwingen + (HANDMATIG,),
        extra=extra,
    )
