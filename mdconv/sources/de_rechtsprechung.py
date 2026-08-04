"""Duitse federale rechtspraak via rechtsprechung-im-internet.de.

Dat portaal van het Bundesministerium der Justiz publiceert geselecteerde
uitspraken van het BGH, BVerfG, BVerwG, BFH, BAG, BSG en BPatG sinds 2010, in
schone XML met een eigen DTD. Er is geen directe "haal op met ECLI"-URL; het
portaal is een Java-portlet-applicatie die eerst doorzocht moet worden.

**De opzoek-dans (twee verzoeken, zelfde sessie).** De zoekfragment-URL
verwacht een setje verborgen formuliervelden (`sugportal`, `sughashcode`, …)
die de server per sessie genereert; zonder exact die velden retourneert de
site alleen het lege zoekformulier, geen resultaat. Dus:
  1. GET het zoekfragment → lees de verborgen velden uit het `<form>`.
  2. GET hetzelfde fragment, nu met die velden + `query=<ECLI>`, in **dezelfde**
     sessie (cookies) → de HTML bevat `doc.id=<ID>` van de gevonden uitspraak
     (of een "0 Treffer"-melding).

Dit moet met een **eigen, niet-gedeelde** `requests.Session` gebeuren: de
gedeelde sessie in `mdconv.net` wordt gelijktijdig door andere documenten
gebruikt (de tool haalt meerdere documenten parallel op), en twee
gelijktijdige zoekopdrachten op dezelfde JSESSIONID zouden elkaars
tussenstaat kunnen overschrijven.

**De uitspraaktekst.** Elke gevonden `doc.id` heeft een vaste
`.../docs/bsjrs/{doc.id}.zip` met daarin één XML-bestand. De secties
(`leitsatz`, `tenor`, `tatbestand`, `entscheidungsgruende`, `gruende`,
`abwmeinung`) volgen de "RspDL"-alineaconventie die `juris_markup.py`
parseert — zie die module voor de details. Dit is fetchable zonder
sessie/cookies.

Deze bron is inmiddels **terugval**: `de_openlegaldata.py` is de primaire
DE-bron (geen sessie/tokendans nodig, bredere dekking incl. deelstaten) en
valt hierop terug als een ECLI daar niet gevonden wordt.
"""

from __future__ import annotations

import io
import re
import zipfile

import requests
from bs4 import BeautifulSoup
from lxml import etree

from .. import net
from ..errors import ConversionError
from ..render import tidy
from .juris_markup import first, text_of, walk_dl_section

ECLI_RE = re.compile(r"ECLI:DE:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+", re.I)

_SEARCH_URL = "https://www.rechtsprechung-im-internet.de/jportal/portal/page/bsjrsprod.psml/js_pane/Suchportlet1/media-type/html"
_ZIP_URL = "https://www.rechtsprechung-im-internet.de/jportal/docs/bsjrs/{doc_id}.zip"

_SEARCH_TIMEOUT = 30
_ZIP_TIMEOUT = 45
_MIN_USEFUL_LENGTH = 40

# Documentvolgorde bepaalt de kopvolgorde in de uitvoer.
_SECTION_LABELS = {
    "leitsatz": "Leitsatz",
    "tenor": "Tenor",
    "tatbestand": "Tatbestand",
    "entscheidungsgruende": "Entscheidungsgründe",
    "gruende": "Gründe",
    "abwmeinung": "Abweichende Meinung",
}


def fetch(query: str) -> tuple[str, str]:
    """Haal een Duitse uitspraak op; geeft (markdown, bronvermelding)."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig Duits ECLI-nummer herkend (bv. ECLI:DE:BGH:2019:240919BVIZB39.18.0)."
        )
    ecli = m.group(0).upper()

    doc_id = _resolve_doc_id(ecli)
    if not doc_id:
        raise ConversionError(
            f"Geen uitspraak gevonden voor {ecli} op rechtsprechung-im-internet.de "
            "(dat portaal ontsluit alleen geselecteerde uitspraken van de Duitse federale "
            "gerechten sinds 2010 — BGH, BVerfG, BVerwG, BFH, BAG, BSG, BPatG)."
        )

    xml_bytes = _fetch_xml(doc_id)
    if xml_bytes is None:
        raise ConversionError(f"Kon de uitspraaktekst voor {ecli} niet downloaden.")

    markdown = _xml_to_markdown(xml_bytes)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Uitspraak {ecli} bevat geen leesbare tekst.")
    return markdown, f"rechtsprechung-im-internet.de • {ecli}"


# --------------------------------------------------------------------------
# Zoeken: ECLI -> doc.id
# --------------------------------------------------------------------------

# Letterlijke "0 resultaten"-melding van de site: dat is een échte lege
# uitkomst, geen technische hapering — dan is een nieuwe poging zinloos.
_NO_RESULTS_MARKER = "0 Treffer"

# De sessie/tokendans (zie moduledocstring) is één keer meegemaakt te falen
# door een tijdelijke hapering bij de site zelf, zonder dat de zoekopdracht
# echt niets opleverde. Eén nieuwe poging onderscheidt dat van een genuine
# "geen resultaten", zonder de gebruiker bij elke kleine hapering al een
# harde foutmelding te geven.
_SEARCH_ATTEMPTS = 2


def _resolve_doc_id(ecli: str) -> str | None:
    last_error: Exception | None = None
    for attempt in range(_SEARCH_ATTEMPTS):
        try:
            result = _search_once(ecli)
        except requests.exceptions.RequestException as e:
            last_error = e
            continue
        if result is not _RETRY:
            return result
    if last_error is not None:
        raise ConversionError(
            f"Kon rechtsprechung-im-internet.de niet bereiken om {ecli} op te zoeken "
            f"(verbindingsfout): {last_error}"
        )
    return None


# Sentinel: de zoekpoging leverde geen bruikbaar antwoord op, maar zonder de
# expliciete "0 Treffer"-melding — dus mogelijk een technische hapering
# (bv. een ontbrekend/gewijzigd formulierveld) in plaats van een echt lege
# uitkomst. Dat verdient een nieuwe poging, anders dan een bevestigde 0 Treffer.
_RETRY = object()


def _search_once(ecli: str):
    # Eigen sessie (zie moduledocstring) — niet de gedeelde net.documents().
    session = requests.Session()
    session.headers["User-Agent"] = net.USER_AGENT
    try:
        r0 = session.get(_SEARCH_URL, timeout=_SEARCH_TIMEOUT)
        if r0.status_code != 200:
            return _RETRY
        form = BeautifulSoup(r0.text, "lxml").find("form")
        if form is None:
            return _RETRY
        hidden = {
            i.get("name"): i.get("value", "")
            for i in form.find_all("input")
            if i.get("type") == "hidden" and i.get("name")
        }

        params = dict(hidden)
        params.update({"query": ecli, "standardsuche": "suchen"})
        r1 = session.get(_SEARCH_URL, params=params, timeout=_SEARCH_TIMEOUT)
        if r1.status_code != 200:
            return _RETRY
    finally:
        session.close()

    m = re.search(r"doc\.id=([\w-]+)", r1.text)
    if m:
        return m.group(1)
    return None if _NO_RESULTS_MARKER in r1.text else _RETRY


# --------------------------------------------------------------------------
# Uitspraaktekst: doc.id -> XML -> Markdown
# --------------------------------------------------------------------------

def _fetch_xml(doc_id: str) -> bytes | None:
    """Stateloze download, dus wél de gedeelde, gepoolde sessie."""
    r = net.documents().get(_ZIP_URL.format(doc_id=doc_id), timeout=_ZIP_TIMEOUT)
    if r.status_code != 200:
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(r.content))
        name = next((n for n in archive.namelist() if n.endswith(".xml")), None)
        return archive.read(name) if name else None
    except zipfile.BadZipFile:
        return None


def _xml_to_markdown(xml_bytes: bytes) -> str:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(xml_bytes, parser=parser)
    if root is None:
        return ""

    blocks: list[str] = []

    gertyp = text_of(first(root, "gertyp"))
    spruchkoerper = text_of(first(root, "spruchkoerper"))
    aktenzeichen = text_of(first(root, "aktenzeichen"))
    heading = " ".join(p for p in (gertyp, spruchkoerper) if p) or "Uitspraak"
    blocks.append(f"# {heading}" + (f" — {aktenzeichen}" if aktenzeichen else ""))

    titel = text_of(first(root, "titelzeile"))
    if titel:
        blocks.append(f"## {titel}")

    for tag, label in _SECTION_LABELS.items():
        section = first(root, tag)
        if section is None or len(section) == 0:
            continue
        rendered = walk_dl_section(section)
        if rendered:
            blocks.append(f"## {label}")
            blocks.extend(rendered)

    return tidy("\n\n".join(blocks))
