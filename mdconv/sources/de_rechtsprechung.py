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
`abwmeinung`) bestaan uit `<dl class="RspDL"><dt>…</dt><dd>…</dd></dl>`-
paren: `<dt>` bevat het randnummer (als `<a name="rd_N">N</a>`, net als
Rechtspraak.nl's `<nr>`), `<dd>` de bijbehorende alinea of een `<table>`
(bv. het handtekeningenblok). Dit is fetchable zonder sessie/cookies.
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
from ..render import collapse_ws, tidy

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

def _resolve_doc_id(ecli: str) -> str | None:
    # Eigen sessie (zie moduledocstring) — niet de gedeelde net.documents().
    session = requests.Session()
    session.headers["User-Agent"] = net.USER_AGENT
    try:
        r0 = session.get(_SEARCH_URL, timeout=_SEARCH_TIMEOUT)
        if r0.status_code != 200:
            return None
        form = BeautifulSoup(r0.text, "lxml").find("form")
        if form is None:
            return None
        hidden = {
            i.get("name"): i.get("value", "")
            for i in form.find_all("input")
            if i.get("type") == "hidden" and i.get("name")
        }

        params = dict(hidden)
        params.update({"query": ecli, "standardsuche": "suchen"})
        r1 = session.get(_SEARCH_URL, params=params, timeout=_SEARCH_TIMEOUT)
        if r1.status_code != 200:
            return None
    except requests.exceptions.RequestException:
        return None
    finally:
        session.close()

    m = re.search(r"doc\.id=([\w-]+)", r1.text)
    return m.group(1) if m else None


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

    gertyp = _text(_first(root, "gertyp"))
    spruchkoerper = _text(_first(root, "spruchkoerper"))
    aktenzeichen = _text(_first(root, "aktenzeichen"))
    heading = " ".join(p for p in (gertyp, spruchkoerper) if p) or "Uitspraak"
    blocks.append(f"# {heading}" + (f" — {aktenzeichen}" if aktenzeichen else ""))

    titel = _text(_first(root, "titelzeile"))
    if titel:
        blocks.append(f"## {titel}")

    for tag, label in _SECTION_LABELS.items():
        section = _first(root, tag)
        if section is None or len(section) == 0:
            continue
        rendered = _walk_section(section)
        if rendered:
            blocks.append(f"## {label}")
            blocks.extend(rendered)

    return tidy("\n\n".join(blocks))


def _first(el, name: str):
    if el is None:
        return None
    for child in el:
        if child.tag == name:
            return child
    return None


def _text(el) -> str:
    return collapse_ws("".join(el.itertext())) if el is not None else ""


def _walk_section(section) -> list[str]:
    """Elke `<dl>` is één genummerde alinea (`<dt>`) met inhoud (`<dd>`)."""
    blocks: list[str] = []
    for dl in section.iter("dl"):
        dt, dd = _first(dl, "dt"), _first(dl, "dd")
        if dd is None:
            continue
        content = _render_dd(dd)
        if not content:
            continue
        num = _text(dt)
        blocks.append(f"{num}. {content}" if num else content)
    return blocks


def _render_dd(dd) -> str:
    parts = []
    for child in dd:
        if child.tag == "p":
            text = _inline(child)
            if text:
                parts.append(text)
        elif child.tag == "table":
            table_md = _table_to_markdown(child)
            if table_md:
                parts.append(table_md)
    return "\n\n".join(parts)


def _inline(el) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if child.tag == "em":
            inner = _inline(child)
            parts.append(f"*{inner}*" if inner.strip() else inner)
        elif child.tag == "br":
            parts.append(" ")
        else:
            parts.append(_inline(child))
        if child.tail:
            parts.append(child.tail)
    return collapse_ws("".join(parts))


def _table_to_markdown(table) -> str:
    rows = []
    for tr in table.iter("tr"):
        cells = [_text(td).replace("|", "\\|") for td in tr if td.tag == "td"]
        if any(c.strip() for c in cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
