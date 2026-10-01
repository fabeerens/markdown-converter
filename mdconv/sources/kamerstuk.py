"""Kamerstukken en andere parlementaire publicaties (Tweede/Eerste Kamer): de invoer.

Deze module herkent wat een gebruiker plakt en vertaalt het naar een publicatie-id van
officielebekendmakingen.nl; de omzetting van de officiële XML zelf (schema
`op-xsd-2012-2`, keyless) doet `officiele_bekendmakingen.py`, mét herkomst en
bronbewijs, zodat een Kamerstuk uit het tabblad Open overheid dezelfde kennisbankbundel
krijgt als een Kamerstuk via `kb_fetch` (besluit 1 van WP-77, 1 oktober 2026). Tot die
samenvoeging had deze module een eigen `xml_to_markdown`; die is vervallen, want twee
omzettingen van dezelfde bron geven twee waarheden.

Ondersteunde invoer (`parse_reference`):
  kst-36600-VII-1 · ah-tk-20242025-100 · h-tk-20242025-20-3
  https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.html
  36600-VII, nr. 1 · 36 600 VII nr 1 · Kamerstukken II 2024/25, 36600-VII, nr. 1
  2024D40329 (D-nummer van de Tweede Kamer) of een tweedekamer.nl-link ermee

Een D-nummer/Document-GUID wordt via de OData-API van de Tweede Kamer
(gegevensmagazijn.tweedekamer.nl) vertaald naar een kamerstuk- of
aanhangsel-id.

Wat de strenge route niet kan leveren, valt terug — maar alleen voor de losse download,
en nooit stil. Bestaat er geen officiële XML (nieuwe publicaties, bijlagen, een brief
buiten een dossier), dan komt de tekst uit de PDF van het SRU-record of uit het
originele Word/PDF-bestand van de Tweede Kamer, met een cursieve notitie bovenaan.
Weigert de strenge route een XML die er wél is (een element dat zij niet kent, een
Kamervraag of Handeling: alleen Kamerstukken zijn gemeten), dan geldt dezelfde terugval,
met de reden van de weigering in de notitie. Zo'n `Fetched` heeft geen `herkomst`, en
`kb_fetch` maakt daar een weigering van (besluit 2): de kennisbank krijgt nooit een
terugval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse, parse_qs

from .. import net
from ..errors import ConversionError
from . import officiele_bekendmakingen, sru
from .common import Fetched, MAX_DOWNLOAD_BYTES, bijlage, header, nl_date

BASE = "https://zoek.officielebekendmakingen.nl"
ODATA = "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0"

_TIMEOUT = 90

# --------------------------------------------------------------------------
# Invoer herkennen
# --------------------------------------------------------------------------

# De publicatiefamilies die dezelfde XML-structuur delen en hier bedoeld zijn.
# Daarnaast de nieuwe, niet-gestructureerde vormen: `blg-1184123` (bijlage),
# `ah-1271549` (aanhangsel) en `kst-1268678` (kamerstuk zonder dossiernummer in het
# id) — alleen als PDF gepubliceerd. Die laatste moet vóór de dossiernotatie
# herkend worden, anders leest "kst-1268678" als dossier 12686, nr. 78.
_ID_RE = re.compile(
    r"\b((?:kst|ah-tk|ah-ek|h-tk|h-ek)-[0-9A-Za-z]+(?:-[0-9A-Za-z]+)+|(?:blg|ah)-\d{4,}|kst-\d{6,})\b", re.I
)
_DNUM_RE = re.compile(r"\b(\d{4}D\d{3,6})\b", re.I)
_GUID_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.I
)
# "36600-VII, nr. 1", "36 600 VII nr 1", "21501-02 3174", "36836 D".
_DOSSIER_RE = re.compile(
    r"""(?P<dossier>\d{1,2}[ .]?\d{3}(?:-\d{2}(?=[\s,\-–]))?)
        (?:[\s,\-–]+(?P<hfst>[IVXLC]+[A-Z]?\b|[A-Z]{1,2}\b(?=[\s,\-–]+(?:nr\b|\d))))?
        [\s,\-–]*(?:(?:nr|nummer)\.?\s*)?
        (?<![A-Za-z])(?P<nr>\d{1,5}|[A-Z])\b""",
    re.I | re.X,
)
_DOSSIER_ONLY_RE = re.compile(r"^\s*\d{1,2}[ .]?\d{3}(?:[\s,\-–]+[IVXLC]+[A-Z]?)?\s*$", re.I)


def _normalise_id(raw: str) -> str:
    """kst-36600-vii-1 → kst-36600-VII-1: voorvoegsel klein, de rest hoofdletters
    (behalve een erratum-/versiesuffix zoals `-n1`)."""
    prefix, _, rest = raw.partition("-")
    if rest.isdigit():                       # blg-1184123, ah-1271549
        return f"{prefix.lower()}-{rest}"
    if prefix.lower() == "ah" or prefix.lower() == "h":
        # ah-tk / h-tk hebben een tweedelig voorvoegsel
        second, _, rest = rest.partition("-")
        prefix = f"{prefix}-{second}"
    rest = re.sub(r"-([nb]\d+)$", lambda m: "-" + m.group(1).lower(), rest.upper())
    return f"{prefix.lower()}-{rest}"


def matches(query: str) -> bool:
    """Ondubbelzinnig een kamerstuk-achtige invoer? (gebruikt door `detect_source`).

    Bewust alleen de vormen die niet met iets anders te verwarren zijn: een
    publicatie-id of een link naar officielebekendmakingen.nl / tweedekamer.nl.
    Losse dossiernotaties ("36600-VII, nr. 1") horen bij het eigen tabblad.
    """
    low = query.lower()
    return bool(
        _ID_RE.search(query)
        or "officielebekendmakingen.nl" in low
        or "tweedekamer.nl" in low
    )


_VOLLEDIGE_NOTATIE = re.compile(r"^\s*kamerstuk(?:ken)?\b", re.I)


def is_dossiernotatie(query: str) -> bool:
    """Een dossiernotatie in haar volledige vorm ("Kamerstukken II 2017/18, 34851, nr. 4").

    `detect_source` mag een kale "34851, nr. 4" niet claimen: die cijfers staan ook in
    zoekregels en in andere bronnen, en het tabblad Open overheid kent die vorm al. Met het
    woord "Kamerstuk(ken)" ervoor is de invoer ondubbelzinnig, en dan hoort hij ook bij
    `from_link`, zodat `kb_fetch` een lijst in dossiernotatie aankan (WP-77, stap 7).
    """
    if not _VOLLEDIGE_NOTATIE.match(query):
        return False
    try:
        return parse_reference(query).ident is not None
    except ConversionError:
        return False


@dataclass(frozen=True)
class Reference:
    """Waar de invoer naartoe wijst: een publicatie-id, of een TK-document."""

    ident: str | None = None      # bv. kst-36600-VII-1
    dnummer: str | None = None    # bv. 2024D40329
    guid: str | None = None       # Document-Id uit de open data


def parse_reference(query: str) -> Reference:
    q = unquote(query.strip())
    if not q:
        raise ConversionError("Voer een kamerstuk, link of identifier in.")

    m = _ID_RE.search(q)
    if m:
        return Reference(ident=_normalise_id(m.group(1)))

    # tweedekamer.nl-links: ?did=2024D12345 (document) / ?id=<guid> (download).
    if "tweedekamer.nl" in q.lower():
        params = parse_qs(urlparse(q).query)
        for key in ("did", "id"):
            for value in params.get(key, []):
                if _DNUM_RE.fullmatch(value):
                    return Reference(dnummer=value.upper())
                if _GUID_RE.fullmatch(value):
                    return Reference(guid=value.lower())
    m = _DNUM_RE.search(q)
    if m:
        return Reference(dnummer=m.group(1).upper())
    m = _GUID_RE.search(q)
    if m:
        return Reference(guid=m.group(1).lower())

    # "Kamerstukken II 2024/25, 36600-VII, nr. 1": het voorwerk eraf.
    cleaned = re.sub(r"^\s*kamerstuk(?:ken)?\s+(?:II|I|TK|EK)?\b[,.\s]*", "", q, flags=re.I)
    cleaned = re.sub(r"\b\d{4}\s*[/-]\s*(?:\d{2}|\d{4})\b[,.\s]*", "", cleaned)
    if _DOSSIER_ONLY_RE.match(cleaned):
        raise ConversionError(
            "Dit is een dossiernummer; vul ook het stuknummer in (bv. 36600-VII, nr. 1)."
        )
    m = _DOSSIER_RE.search(cleaned)
    if m:
        dossier = re.sub(r"[ .]", "", m.group("dossier"))
        parts = ["kst", dossier]
        if m.group("hfst"):
            parts.append(m.group("hfst").upper())
        parts.append(m.group("nr").upper())
        return Reference(ident="-".join(parts))

    raise ConversionError(
        "Geen kamerstuk herkend. Geef een identifier (kst-36600-VII-1), een link naar "
        "officielebekendmakingen.nl of tweedekamer.nl, een dossiernotatie "
        "(36600-VII, nr. 1) of een D-nummer (2024D40329)."
    )


# --------------------------------------------------------------------------
# Tweede Kamer open data (OData): D-nummer / GUID → publicatie-id
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _TkDocument:
    guid: str
    dnummer: str
    soort: str
    ident: str | None       # officiële publicatie, voor zover af te leiden
    content_type: str


def _lookup_tk_document(ref: Reference) -> _TkDocument:
    select = ("Id,DocumentNummer,Soort,Vergaderjaar,Volgnummer,Aanhangselnummer,"
              "Kamer,ContentType")
    expand = "Kamerstukdossier($select=Nummer,Toevoeging)"
    if ref.guid:
        url = f"{ODATA}/Document({ref.guid})"
        params = {"$select": select, "$expand": expand}
    else:
        url = f"{ODATA}/Document"
        params = {
            "$filter": f"DocumentNummer eq '{ref.dnummer}'",
            "$select": select, "$expand": expand, "$top": "1",
        }
    r = net.documents().get(url, params=params, timeout=_TIMEOUT)
    if r.status_code == 404:
        raise ConversionError("Dit document staat niet in de open data van de Tweede Kamer.")
    if r.status_code != 200:
        raise ConversionError(
            f"De open data van de Tweede Kamer gaf een fout (status {r.status_code})."
        )
    data = r.json()
    row = data if ref.guid else (data.get("value") or [None])[0]
    if not row:
        raise ConversionError(
            f"Document {ref.dnummer} niet gevonden in de open data van de Tweede Kamer. "
            "Let op: een Z-nummer is een zaak, geen document."
        )
    return _TkDocument(
        guid=row["Id"],
        dnummer=row.get("DocumentNummer") or ref.dnummer or "",
        soort=row.get("Soort") or "",
        ident=_ident_from_tk_row(row),
        content_type=row.get("ContentType") or "",
    )


def _ident_from_tk_row(row: dict) -> str | None:
    """Kamerstuk (dossier + volgnummer) of Aanhangsel (Kamervragen) → id."""
    dossiers = row.get("Kamerstukdossier") or []
    volg = row.get("Volgnummer")
    if dossiers and isinstance(volg, int) and volg > 0:
        d = dossiers[0]
        parts = ["kst", str(d["Nummer"])]
        if d.get("Toevoeging"):
            parts.append(str(d["Toevoeging"]).upper())
        parts.append(str(volg))
        return "-".join(parts)
    ah = row.get("Aanhangselnummer")
    if ah and re.fullmatch(r"\d{4}\d+", ah):
        # 242501244 → vergaderjaar 2024-2025, nummer 1244
        yy1, yy2, nr = ah[:2], ah[2:4], int(ah[4:])
        prefix = "ah-ek" if row.get("Kamer") == 1 else "ah-tk"
        return f"{prefix}-20{yy1}20{yy2}-{nr}"
    return None


def _file_from_tk(doc: _TkDocument) -> tuple[bytes, str]:
    r = net.documents().get(f"{ODATA}/Document({doc.guid})/resource", timeout=_TIMEOUT)
    if r.status_code != 200 or not r.content:
        raise ConversionError(
            f"Kon het bestand van document {doc.dnummer} niet ophalen "
            f"(status {r.status_code})."
        )
    ext = {
        "application/pdf": "pdf",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/plain": "txt",
    }.get(doc.content_type.split(";")[0].strip(), "bin")
    return r.content, f"{doc.dnummer}.{ext}"


# --------------------------------------------------------------------------
# Ophalen
# --------------------------------------------------------------------------

def _pdf_only(ident: str) -> bool:
    """Nieuwe publicaties (`blg-…`, `ah-<nummer>`, `kst-<nummer>`) bestaan alleen als PDF."""
    return bool(re.fullmatch(r"(?:blg|ah|kst)-\d+", ident))


def resolve(query: str) -> tuple[str | None, _TkDocument | None]:
    """De invoer naar een publicatie-id; een D-nummer of GUID via de open data van de Kamer.

    Wat de strenge herkenning van de kennisbankroute al kent, gaat daar langs (klein
    geschreven, zoals die route hem opvraagt); de rest door `parse_reference`.
    """
    q = query.strip()
    if officiele_bekendmakingen.matches(q):
        return officiele_bekendmakingen.publicatie_id(q), None
    ref = parse_reference(q)
    if ref.ident is not None:
        return ref.ident, None
    tk = _lookup_tk_document(ref)
    return tk.ident, tk


def fetch(query: str) -> Fetched:
    """Haal een kamerstuk, aanhangsel, handeling of bijlage op.

    Eén omzetting van de XML: `officiele_bekendmakingen.fetch`, met herkomst. De terugval
    (PDF uit het SRU-record, of het originele bestand van de Kamer) komt alleen aan bod als
    er geen XML is of als de strenge route de XML weigert; de reden staat dan in de notitie
    en in `warnings`, en het resultaat heeft geen `herkomst`.
    """
    ident, tk = resolve(query)
    reden: str | None = None
    if ident and not _pdf_only(ident) and officiele_bekendmakingen.matches(ident):
        try:
            markdown, source, herkomst = officiele_bekendmakingen.fetch(ident)
        except officiele_bekendmakingen.GeenXml:
            reden = None
        except ConversionError as fout:
            reden = str(fout)
        else:
            return Fetched(markdown, source, ident=ident, name=ident, herkomst=herkomst)

    try:
        return _terugval(ident, tk, reden)
    except ConversionError as fout:
        # Een weigering van de strenge route mag niet schuilgaan achter een haperende
        # terugval: de gebruiker leest eerst waarom de XML niet door de poort kwam.
        if reden:
            raise ConversionError(f"{reden} De terugval op de PDF lukte ook niet: {fout}") from fout
        raise


def _terugval(ident: str | None, tk: _TkDocument | None, reden: str | None) -> Fetched:
    """De losse download zonder XML: de PDF uit het SRU-record, of het bestand van de Kamer."""
    bijlagen = _bijlagen(ident) if ident else []
    if ident:
        fetched = _from_sru_pdf(ident, reden)
        if fetched is not None:
            fetched.bijlagen = bijlagen or fetched.bijlagen
            fetched.ident = fetched.name = ident
            return fetched

    if tk is not None:
        fetched = _fallback_from_tk(tk, ident, reden)
        fetched.bijlagen = bijlagen
        fetched.ident, fetched.name = ident or tk.dnummer, ident or tk.dnummer
        return fetched
    if reden:
        raise ConversionError(f"{reden} Er is ook geen PDF om op terug te vallen.")
    raise ConversionError(
        f"{_display_id(ident)} is niet gevonden op officielebekendmakingen.nl. "
        "Controleer het nummer; een zeer recent stuk kan er nog niet staan — "
        "plak dan het D-nummer (bv. 2024D40329) om het originele bestand op te halen."
    )


_GEEN_BUNDEL = "er is geen kennisbankbundel."


def _notitie(reden: str | None, standaard: str) -> tuple[str, tuple[str, ...]]:
    """De cursieve notitie bovenaan en dezelfde tekst als waarschuwing voor de UI en kb_fetch."""
    tekst = f"{reden} {standaard}" if reden else standaard
    return f"*{tekst}*", (tekst,)


def _bijlagen(ident: str) -> list[dict]:
    """Bijlagen van een hoofddocument (SRU), of — voor een bijlage — het hoofddocument.

    Best-effort: het paneel is verrijking, een storing in de zoekdienst mag de
    conversie zelf nooit laten falen."""
    try:
        items = [
            bijlage(r.ident, r.title, "Bijlage", r.page_url or f"{BASE}/{r.ident}.html")
            for r in sru.attachments_of(ident)
        ]
        if not items and ident.startswith("blg-"):
            rec = sru.by_identifier(ident)
            if rec and rec.hoofddocument:
                items = [bijlage(rec.hoofddocument, _display_id(rec.hoofddocument),
                                 "Hoofddocument", f"{BASE}/{rec.hoofddocument}.html")]
        return items
    except Exception:
        return []


def _download(url: str) -> bytes:
    r = net.documents().get(url, timeout=_TIMEOUT, stream=True)
    if r.status_code != 200:
        raise ConversionError(f"Kon het bestand niet ophalen (status {r.status_code}).")
    declared = int(r.headers.get("Content-Length") or 0)
    if declared > MAX_DOWNLOAD_BYTES:
        raise ConversionError("Het bestand is te groot om hier om te zetten (meer dan 100 MB).")
    data = bytearray()
    for chunk in r.iter_content(1 << 20):
        data += chunk
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ConversionError("Het bestand is te groot om hier om te zetten (meer dan 100 MB).")
    return bytes(data)


def _from_sru_pdf(ident: str, reden: str | None = None) -> Fetched | None:
    """Terugval voor publicaties zonder (bruikbare) XML: de PDF-manifestatie uit het SRU-record."""
    from . import files

    rec = sru.by_identifier(ident)
    if rec is None or not rec.pdf_url:
        return None
    markdown, engine = files.convert(_download(rec.pdf_url), f"{ident}.pdf")
    markdown = files.warn_if_unmapped_glyphs(markdown)
    head = header(rec.title or _display_id(ident), [
        ("Publicatie", f"{_display_id(ident)} (`{ident}`)"),
        ("Soort", rec.subsoort or rec.soort),
        ("Vergaderjaar", rec.vergaderjaar),
        ("Kamer", rec.creator),
        ("Datum", nl_date(rec.date)),
        ("Indiener", rec.indiener),
        ("Bron", f"<{rec.page_url or f'{BASE}/{ident}.html'}>"),
    ])
    note, warnings = _notitie(reden, (
        ("De tekst is omgezet uit de PDF" if reden else
         "Voor deze publicatie bestaat geen gestructureerde XML; de tekst is omgezet uit de PDF")
        + f". Koppen en voetnoten zijn daardoor minder betrouwbaar, en {_GEEN_BUNDEL}"))
    return Fetched(
        f"{head}\n\n{note}\n\n{markdown.lstrip()}",
        f"Officiële Bekendmakingen • {_display_id(ident)} ({ident}) — PDF ({engine})",
        warnings=warnings,
    )


def _fallback_from_tk(tk: _TkDocument, ident: str | None, reden: str | None = None) -> Fetched:
    # Lokale import: `files` laadt zijn zware engines lui, maar de module zelf
    # hoort niet bij het opstarten van deze converter.
    from . import files

    data, filename = _file_from_tk(tk)
    markdown, engine = files.convert(data, filename)
    if filename.lower().endswith(".pdf"):
        markdown = files.warn_if_unmapped_glyphs(markdown)
    reason = (
        f"Voor {_display_id(ident)} bestaat (nog) geen officiële XML"
        if ident else "Dit document is geen gepubliceerd kamerstuk"
    )
    note, warnings = _notitie(reden, (
        f"{reason}; deze tekst is omgezet uit het originele "
        f"{filename.rsplit('.', 1)[-1].upper()}-bestand uit de open data van de Tweede "
        f"Kamer ({tk.dnummer}). Koppen en voetnoten zijn daardoor minder betrouwbaar, en {_GEEN_BUNDEL}"
    ))
    source = f"Tweede Kamer open data • {tk.dnummer} ({engine})"
    return Fetched(f"{note}\n\n{markdown.lstrip()}", source, warnings=warnings)


def _display_id(ident: str | None) -> str:
    """kst-36600-VII-1 → 'Kamerstuk 36600-VII, nr. 1'."""
    if not ident:
        return "document"
    parts = ident.split("-")
    if parts[0] == "kst" and len(parts) >= 3:
        return f"Kamerstuk {'-'.join(parts[1:-1])}, nr. {parts[-1]}"
    if ident.startswith("ah-"):
        if len(parts) >= 4:
            return f"Aanhangsel {parts[2][:4]}/{parts[2][4:]}, nr. {parts[-1]}"
        return f"Aanhangsel {parts[-1]}"
    if ident.startswith("h-"):
        return f"Handelingen {ident}"
    if ident.startswith("blg-"):
        return f"Bijlage {ident[4:]}"
    return ident
