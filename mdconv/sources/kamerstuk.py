"""Kamerstukken en andere parlementaire publicaties (Tweede/Eerste Kamer).

Bron is de **officiële XML** van officielebekendmakingen.nl (schema
`op-xsd-2012-2`): `https://zoek.officielebekendmakingen.nl/{id}.xml`. Die is
keyless, bevat de volledige gestructureerde tekst (koppen, voetnoten,
verwijzingen, lijsten, tabellen, afbeeldingen) en is dezelfde bron die de
open data van de Tweede Kamer (opendata.tweedekamer.nl) voor de gepubliceerde
stukken aanwijst — de SyncFeed van Tkconv's `tkgetxml` levert alleen metadata en
het originele Word/PDF-bestand, geen gestructureerde tekst.

Ondersteunde invoer (`parse_reference`):
  kst-36600-VII-1 · ah-tk-20242025-100 · h-tk-20242025-20-3
  https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.html
  36600-VII, nr. 1 · 36 600 VII nr 1 · Kamerstukken II 2024/25, 36600-VII, nr. 1
  2024D40329 (D-nummer van de Tweede Kamer) of een tweedekamer.nl-link ermee

Een D-nummer/Document-GUID wordt via de OData-API van de Tweede Kamer
(gegevensmagazijn.tweedekamer.nl) vertaald naar een kamerstuk- of
aanhangsel-id. Bestaat er (nog) geen officiële XML, dan valt de conversie
terug op het originele Word/PDF-bestand uit diezelfde open data — mét een
duidelijke notitie bovenaan, nooit stil.

De XML-naar-Markdown-vertaling (`xml_to_markdown`) is puur: geen netwerk, dus
testbaar met een fragment. Wat de structuur ons geeft, wordt behouden:
- `divisie`/`kop`/`tussenkop` → `##`… (de diepte volgt de nesting; ongenummerde
  tussenkoppen krijgen hun niveau uit hun opmaak: vet > vetcur > cur)
- `noot` → Markdown-voetnoten (`[^1]`) onderaan
- `extref` → gewone links (kamerstuk-, dossier- en externe verwijzingen)
- `lijst`, `table` (CALS, met col-/rowspan), `box`, `definitielijst`, Kamervragen
  en Handelingen.
Onbekende elementen vallen niet weg: ze worden als alinea of container gelezen.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse, parse_qs

from lxml import etree

from .. import net
from ..errors import ConversionError
from ..render import tidy
from . import sru
from .common import Fetched, MAX_DOWNLOAD_BYTES, bijlage, header, nl_date

BASE = "https://zoek.officielebekendmakingen.nl"
ODATA = "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0"

_TIMEOUT = 90
_MIN_BODY = 20
_MAX_IMAGES = 30
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_TOTAL = 50 * 1024 * 1024

# --------------------------------------------------------------------------
# Invoer herkennen
# --------------------------------------------------------------------------

# De publicatiefamilies die dezelfde XML-structuur delen en hier bedoeld zijn.
# Daarnaast de nieuwe, niet-gestructureerde vormen: `blg-1184123` (bijlage) en
# `ah-1271549` (aanhangsel dat alleen als PDF is gepubliceerd).
_ID_RE = re.compile(
    r"\b((?:kst|ah-tk|ah-ek|h-tk|h-ek)-[0-9A-Za-z]+(?:-[0-9A-Za-z]+)+|(?:blg|ah)-\d{4,})\b", re.I
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

def _get_xml(ident: str) -> bytes | None:
    r = net.documents().get(f"{BASE}/{ident}.xml", timeout=_TIMEOUT)
    if r.status_code == 404:
        return None
    if r.status_code != 200 or not r.content:
        raise ConversionError(f"Kon {ident} niet ophalen (status {r.status_code}).")
    # Een niet-bestaand id geeft soms een HTML-foutpagina met status 200.
    if b"<officiele-publicatie" not in r.content[:2000]:
        return None
    return r.content


def _get_metadata(ident: str) -> dict[str, list[str]]:
    """Best-effort: metadata.xml levert o.a. soort stuk, indiener en data."""
    try:
        r = net.documents().get(f"{BASE}/{ident}/metadata.xml", timeout=30)
        if r.status_code != 200:
            return {}
        root = etree.fromstring(r.content, _parser())
    except Exception:  # metadata is verrijking; nooit de conversie laten falen
        return {}
    out: dict[str, list[str]] = {}
    for m in root.iter("metadata"):
        name, content = m.get("name"), (m.get("content") or "").strip()
        if name and content:
            out.setdefault(name, []).append(content)
    return out


def fetch(query: str) -> Fetched:
    """Haal een kamerstuk, aanhangsel, handeling of bijlage op."""
    ref = parse_reference(query)
    tk: _TkDocument | None = None
    ident = ref.ident
    if ident is None:
        tk = _lookup_tk_document(ref)
        ident = tk.ident

    xml, meta, bijlagen = None, {}, []
    if ident:
        # Nieuwe publicaties (`blg-…`, `ah-<nummer>`) bestaan alleen als PDF: geen XML proberen.
        pdf_only = bool(re.fullmatch(r"(?:blg|ah)-\d+", ident))
        with ThreadPoolExecutor(max_workers=3) as pool:
            xml_future = None if pdf_only else pool.submit(_get_xml, ident)
            meta_future = None if pdf_only else pool.submit(_get_metadata, ident)
            bijl_future = pool.submit(_bijlagen, ident)
            xml = xml_future.result() if xml_future else None
            meta = meta_future.result() if meta_future else {}
            bijlagen = bijl_future.result()

    if xml is not None:
        markdown, image_names = xml_to_markdown(xml, ident, meta)
        markdown, images = _attach_images(markdown, image_names)
        source = f"Officiële Bekendmakingen • {_display_id(ident)} ({ident})"
        if not bijlagen:   # SRU gaf niets (of faalde): de ids uit de XML-metadata zijn beter dan niets
            bijlagen = [bijlage(b, b, "Bijlage", f"{BASE}/{b}.html")
                        for b in meta.get("OVERHEIDop.bijlage", [])]
        return Fetched(markdown, source, images, bijlagen, ident=ident, name=ident)

    if ident:
        fetched = _from_sru_pdf(ident)
        if fetched is not None:
            fetched.bijlagen = bijlagen or fetched.bijlagen
            fetched.ident = fetched.name = ident
            return fetched

    if tk is not None:
        fetched = _fallback_from_tk(tk, ident)
        fetched.bijlagen = bijlagen
        fetched.ident, fetched.name = ident or tk.dnummer, ident or tk.dnummer
        return fetched
    raise ConversionError(
        f"{_display_id(ident)} is niet gevonden op officielebekendmakingen.nl. "
        "Controleer het nummer; een zeer recent stuk kan er nog niet staan — "
        "plak dan het D-nummer (bv. 2024D40329) om het originele bestand op te halen."
    )


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


def _from_sru_pdf(ident: str) -> Fetched | None:
    """Terugval voor publicaties zonder XML: de PDF-manifestatie uit het SRU-record."""
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
    note = ("*Voor deze publicatie bestaat geen gestructureerde XML; de tekst is omgezet uit "
            "de PDF. Koppen en voetnoten zijn daardoor minder betrouwbaar.*")
    return Fetched(
        f"{head}\n\n{note}\n\n{markdown.lstrip()}",
        f"Officiële Bekendmakingen • {_display_id(ident)} ({ident}) — PDF ({engine})",
    )


def _fallback_from_tk(tk: _TkDocument, ident: str | None) -> Fetched:
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
    note = (
        f"*{reason}; deze tekst is omgezet uit het originele "
        f"{filename.rsplit('.', 1)[-1].upper()}-bestand uit de open data van de Tweede "
        f"Kamer ({tk.dnummer}). Koppen en voetnoten zijn daardoor minder betrouwbaar.*"
    )
    source = f"Tweede Kamer open data • {tk.dnummer} ({engine})"
    return Fetched(f"{note}\n\n{markdown.lstrip()}", source)


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


# --------------------------------------------------------------------------
# Afbeeldingen
# --------------------------------------------------------------------------

def _attach_images(markdown: str, names: list[str]) -> tuple[str, list[tuple[str, bytes]]]:
    """Download de afbeeldingen uit het stuk en geef ze mee als bijlagen.

    Wat niet gedownload kan worden (te veel, te groot, fout) blijft als gewone
    Markdown-afbeelding naar de bron staan — zichtbaar, nooit een dode embed.
    """
    if not names:
        return markdown, []
    wanted = names[:_MAX_IMAGES]

    def one(name: str):
        try:
            r = net.documents().get(f"{BASE}/{name}", timeout=60)
        except Exception:
            return None
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > _MAX_IMAGE_BYTES:
            return None
        return r.content

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, wanted))

    images: list[tuple[str, bytes]] = []
    total = 0
    ok: set[str] = set()
    for name, data in zip(wanted, results):
        if data is None or total + len(data) > _MAX_IMAGE_TOTAL:
            continue
        total += len(data)
        images.append((name, data))
        ok.add(name)
    for name in names:
        if name not in ok:
            markdown = markdown.replace(f"![[{name}]]", f"![{name}]({BASE}/{name})")
    return markdown, images


# --------------------------------------------------------------------------
# XML → Markdown
# --------------------------------------------------------------------------

def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True,
                           remove_blank_text=False)


# Elementen die nooit inhoud zijn (metadata of al elders verwerkt).
_SKIP = {
    "metadata", "kamerstukkop", "kamervraagkop", "dossier", "stuknr", "datumtekst",
    "kamervraagnummer", "kamervraagomschrijving", "tekstregel", "spreker", "item-titel",
    "colspec", "li.nr", "lidnr",
}
_BLOCKS = {
    "al", "al-groep", "lijst", "li", "table", "plaatje", "illustratie", "box",
    "divisie", "kop", "tussenkop", "artikel", "lid", "tekst", "vrije-tekst",
    "algemeen", "bijlage", "definitielijst", "ondertekening", "wijziging",
    "amendement", "vraag", "antwoord", "agendapunt", "spreekbeurt", "onderwerp",
    "wettekst", "aanhef", "tekst-sluiting", "voorstel-wet", "titel", "wat",
}
# Elementen die binnen een alinea thuishoren; alles wat daarbuiten valt en kinderen
# heeft, wordt als container gelezen (anders plakt `considerans` zijn alinea's aan elkaar).
_INLINE = {
    "nadruk", "noot", "noot.nr", "noot.al", "extref", "intref", "sup", "inf", "br",
    "naam", "voornaam", "achternaam", "voorvoegsels", "datum", "organisatie",
    "kamervraagonderwerp", "term", "politiek", "functie", "plaats", "dagtekening",
}
# Rangorde van ongenummerde tussenkoppen: hoe hoger in de lijst, hoe hoger het niveau.
_TUSSENKOP_RANK = ["vet", "halfvet", "vetcur", "cur", "rom", "ondlijn"]

_WS = re.compile(r"[ \t\r\n]+")
_NUMBERED = re.compile(r"^\d+(?:\.\d+)*\.?$")
_ESC = re.compile(
    r"(?P<bs>\\)|(?P<tok>[`*$])|(?P<fn>\[\^)|(?P<lt><(?=[A-Za-z/!]))"
    r"|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])"
)
_PARA_START = re.compile(r"^(#{1,6}\s|>|[-+]\s|\d+[.)]\s)")


def _escape(text: str) -> str:
    def repl(m: re.Match) -> str:
        return "\\" + ("[^" if m.group("fn") else m.group(0))

    return _ESC.sub(repl, text)


def _para(text: str) -> str:
    text = _WS.sub(" ", text).strip()
    if _PARA_START.match(text):
        text = "\\" + text
    return text


@dataclass
class _Ctx:
    ident: str
    notes: list[tuple[str, str]] = field(default_factory=list)   # (label, definitie)
    labels: set[str] = field(default_factory=set)
    images: list[str] = field(default_factory=list)


def _tag(el) -> str | None:
    return el.tag if isinstance(el.tag, str) else None


def _text_of(el) -> str:
    """Kale tekst van een element (voor koppen/metadata)."""
    return _WS.sub(" ", "".join(el.itertext())).strip()


def _link_target(doc: str, soort: str) -> str | None:
    doc = (doc or "").strip()
    if not doc:
        return None
    if re.match(r"https?://", doc, re.I) or soort.lower() == "url":
        return doc if "://" in doc else f"https://{doc}"
    if doc.startswith("dossier/"):
        return f"{BASE}/{doc}"
    return f"{BASE}/{doc}.html"


def _inline(el, ctx: _Ctx, plain: bool = False, *, own_text: bool = True) -> str:
    """Lopende tekst van een element, met opmaak, voetnoten en links."""
    parts: list[str] = []
    if own_text and el.text:
        parts.append(_escape(el.text))
    for child in el:
        tag = _tag(child)
        if tag is None:                      # commentaar/processing-instruction
            pass
        elif tag == "nadruk":
            parts.append(_emphasis(child, ctx, plain))
        elif tag == "noot":
            parts.append(_note(child, ctx))
        elif tag == "extref":
            parts.append(_extref(child, ctx, plain))
        elif tag == "sup":
            inner = _inline(child, ctx, plain).strip()
            parts.append(f"<sup>{inner}</sup>" if inner else "")
        elif tag == "inf":
            inner = _inline(child, ctx, plain).strip()
            parts.append(f"<sub>{inner}</sub>" if inner else "")
        elif tag == "br":
            parts.append("<br>")
        elif tag in ("li.nr", "lidnr") or tag in _SKIP:
            pass
        else:                                 # onbekend inline-element: inhoud behouden
            parts.append(_inline(child, ctx, plain))
        if child.tail:
            parts.append(_escape(child.tail))
    return "".join(parts)


def _emphasis(el, ctx: _Ctx, plain: bool) -> str:
    inner = _inline(el, ctx, plain)
    if plain or not inner.strip():
        return inner
    kind = el.get("type", "")
    mark = {"vet": "**", "halfvet": "**", "cur": "*", "vetcur": "***"}.get(kind)
    if not mark:                              # onderstreept e.d.: geen Markdown-equivalent
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{mark}{inner.strip()}{mark}{trail}"


def _extref(el, ctx: _Ctx, plain: bool) -> str:
    text = _inline(el, ctx, plain=True).strip()
    url = _link_target(el.get("doc", ""), el.get("soort", ""))
    if not url:
        return text
    if not text:
        text = el.get("doc", "")
    if plain:
        return text
    if text == url:
        return f"<{url}>"
    # Haakjes in de URL breken de Markdown-link.
    url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
    return f"[{text}]({url})"


def _note(el, ctx: _Ctx) -> str:
    nr = _text_of(el.find("noot.nr")) if el.find("noot.nr") is not None else ""
    label = nr if re.fullmatch(r"[A-Za-z0-9]{1,6}", nr) else str(len(ctx.notes) + 1)
    base, k = label, 2
    while label in ctx.labels:                # nummering die opnieuw begint (bijlagen)
        label, k = f"{base}-{k}", k + 1
    ctx.labels.add(label)
    paragraphs = [
        _para(_inline(al, ctx)) for al in el.findall("noot.al")
    ] or [_para(_inline(el, ctx))]
    paragraphs = [p for p in paragraphs if p]
    definition = ("\n\n    ").join(paragraphs) if paragraphs else "(lege voetnoot)"
    ctx.notes.append((label, definition))
    return f"[^{label}]"


# -- koppen -----------------------------------------------------------------

def _heading(text: str, level: int) -> str:
    text = _WS.sub(" ", text).strip()
    return f"{'#' * max(1, min(level, 6))} {text}" if text else ""


def _kop_text(kop, ctx: _Ctx) -> str:
    """`kop` = optioneel label + nr + titel, in documentvolgorde."""
    bits = []
    for child in kop:
        if _tag(child) in ("label", "nr", "titel", "subtitel", "tekst"):
            bits.append(_inline(child, ctx, plain=True).strip())
    return " ".join(b for b in bits if b) or _inline(kop, ctx, plain=True).strip()


def _tussenkop_levels(parent) -> dict[str, int]:
    """Opmaak → relatief niveau binnen deze container (0 = hoogste in gebruik)."""
    used = {c.get("kopopmaak", "vet") for c in parent if _tag(c) == "tussenkop"}
    ordered = sorted(
        used, key=lambda s: _TUSSENKOP_RANK.index(s) if s in _TUSSENKOP_RANK else 99
    )
    return {style: i for i, style in enumerate(ordered)}


# -- blokken ----------------------------------------------------------------

def _has_block_child(el) -> bool:
    return any(_tag(c) in _BLOCKS for c in el)


def _is_container(el) -> bool:
    return any(_tag(c) and (_tag(c) in _BLOCKS or _tag(c) not in _INLINE) for c in el)


def _children(parent, ctx: _Ctx, level: int, *, skip=()) -> list[str]:
    """Render de kinderen van een container tot een lijst Markdown-blokken."""
    out: list[str] = []
    levels = _tussenkop_levels(parent)
    if parent.text and parent.text.strip():
        out.append(_para(_escape(parent.text)))
    for child in parent:
        tag = _tag(child)
        if tag and tag not in skip:
            out.extend(_block(child, ctx, level, levels))
        if child.tail and child.tail.strip():
            out.append(_para(_escape(child.tail)))
    return [b for b in out if b]


def _block(el, ctx: _Ctx, level: int, tussen: dict[str, int]) -> list[str]:
    tag = _tag(el)
    if tag in _SKIP or tag in ("title", "nr", "noot.nr", "noot.al"):
        return []
    if tag == "tussenkop":
        rank = tussen.get(el.get("kopopmaak", "vet"), 0)
        return [_heading(_inline(el, ctx, plain=True), level + rank)]
    if tag == "kop":
        return [_heading(_kop_text(el, ctx), level)]
    if tag == "titel":
        return [_heading(_inline(el, ctx, plain=True), level)]
    if tag == "divisie":
        return _divisie(el, ctx, level)
    if tag == "lijst":
        return [_list(el, ctx)]
    if tag == "table":
        return _table(el, ctx)
    if tag in ("plaatje", "illustratie"):
        return _images(el, ctx)
    if tag == "box":
        inner = "\n\n".join(_children(el, ctx, level + 1))
        return ["\n".join(f"> {ln}" if ln else ">" for ln in inner.split("\n"))] if inner else []
    if tag == "definitielijst":
        return [_definitions(el, ctx)]
    if tag == "ondertekening":
        lines = [_inline(c, ctx, plain=True).strip() for c in el if _tag(c)]
        return [" <br>".join(l for l in lines if l)]
    if tag == "lid":
        return _lid(el, ctx, level)
    if tag == "artikel":
        return _children(el, ctx, level)
    if tag in ("vraag", "antwoord"):
        nr = el.find("nr")
        head = _heading(_text_of(nr), level) if nr is not None else ""
        return [head, *_children(el, ctx, level + 1, skip=("nr",))]
    if tag == "noot":
        return [_para(_note(el, ctx))]
    if tag in ("al", "wat") and not _has_block_child(el):
        return [_para(_inline(el, ctx))]
    if tag in _BLOCKS or _is_container(el):
        return _children(el, ctx, level)
    # Onbekend element zonder blokken: als alinea lezen i.p.v. het weg te laten.
    text = _inline(el, ctx)
    return [_para(text)] if text.strip() else []


def _divisie(el, ctx: _Ctx, level: int) -> list[str]:
    kop = el.find("kop")
    if kop is None:
        return _children(el, ctx, level)
    # Genummerde koppen ("2.", "2.1") dragen hun diepte zelf; de XML nest ze niet
    # altijd consistent ("1." bevat "1.1", maar "2.1" staat naast "2."), dus de
    # nummering wint zodra die er is.
    nr = kop.find("nr")
    if nr is not None and _NUMBERED.match(_text_of(nr)):
        level = 2 + _text_of(nr).rstrip(".").count(".") + 1
    return [
        _heading(_kop_text(kop, ctx), level),
        *_children(el, ctx, level + 1, skip=("kop",)),
    ]


def _lid(el, ctx: _Ctx, level: int) -> list[str]:
    nr = el.find("lidnr")
    blocks = _children(el, ctx, level, skip=("lidnr",))
    if nr is not None and blocks:
        marker = _text_of(nr)
        if marker and not blocks[0].startswith(("#", ">", "|", "-")):
            blocks[0] = f"{marker} {blocks[0]}"
    return blocks


def _definitions(el, ctx: _Ctx) -> str:
    items = []
    for item in el.findall("definitie-item"):
        term = item.find("term")
        definition = item.find("definitie")
        t = _inline(term, ctx, plain=True).strip() if term is not None else ""
        d = " ".join(_children(definition, ctx, 6)) if definition is not None else ""
        items.append(f"- **{t}** {d}".rstrip())
    return "\n".join(items)


# -- lijsten ----------------------------------------------------------------

_BULLETS = {"", "-", "–", "—", "‑", "•", "·", "*"}


def _marker(nr: str, ltype: str, index: int) -> str:
    nr = nr.strip()
    if re.fullmatch(r"\d{1,4}[.)]?", nr):
        return f"{nr.rstrip('.)')}. "
    if nr in _BULLETS:
        if not nr and ltype == "1":
            return f"{index}. "
        return "- "
    return f"- {nr} "     # juridische marker (a., i., 1°) letterlijk behouden


def _list(el, ctx: _Ctx) -> str:
    ltype = el.get("type", "")
    start = int(el.get("start", "1")) if str(el.get("start", "1")).isdigit() else 1
    items: list[str] = []
    for i, li in enumerate((c for c in el if _tag(c) == "li"), start):
        nr_el = li.find("li.nr")
        prefix = _marker(_text_of(nr_el) if nr_el is not None else "", ltype, i)
        blocks = _children(li, ctx, 6, skip=("li.nr",))
        if not blocks:
            continue
        pad = " " * len(prefix)
        lines = (prefix + blocks[0]).split("\n")
        body = [lines[0]] + [pad + l if l else l for l in lines[1:]]
        for extra in blocks[1:]:
            body.append("")
            body.extend(pad + l if l else l for l in extra.split("\n"))
        items.append("\n".join(body))
    return "\n".join(items)


# -- tabellen ---------------------------------------------------------------

def _cell_text(entry, ctx: _Ctx) -> str:
    blocks = _children(entry, ctx, 6)
    text = "<br>".join(b.replace("\n", "<br>") for b in blocks)
    return text.replace("|", "\\|")


def _grid(rows, colnums: dict[str, int], ncols: int, ctx: _Ctx, *, repeat_span: bool):
    """CALS-rijen → rechthoekig raster; col-/rowspans worden uitgevouwen."""
    grid: list[list[str]] = []
    pending: dict[int, int] = {}          # kolom → nog te vullen vervolgrijen
    for row in rows:
        cells = [None] * ncols
        for c, left in list(pending.items()):
            if left > 0 and c < ncols:
                cells[c] = ""
                pending[c] = left - 1
        running = 0
        for entry in (e for e in row if _tag(e) in ("entry", "th", "td")):
            def pos(name):
                return colnums.get(name) - 1 if name in colnums else None

            start = pos(entry.get("namest") or entry.get("colname"))
            if start is None:
                while running < ncols and cells[running] is not None:
                    running += 1
                start = running
            end = pos(entry.get("nameend")) if entry.get("nameend") else start
            end = max(start, end if end is not None else start)
            text = _cell_text(entry, ctx)
            for c in range(start, min(end, ncols - 1) + 1):
                cells[c] = text if (c == start or repeat_span) else ""
            more = int(entry.get("morerows", "0") or 0)
            if more:
                for c in range(start, min(end, ncols - 1) + 1):
                    pending[c] = more
            running = end + 1
        grid.append(["" if c is None else c for c in cells])
    return grid


def _table(el, ctx: _Ctx) -> list[str]:
    out: list[str] = []
    title = el.find("title")
    if title is not None and _text_of(title):
        out.append(f"**{_inline(title, ctx, plain=True).strip()}**")
    head_rows, body_rows = [], []
    colnums: dict[str, int] = {}
    ncols = 0
    for tgroup in el.iter("tgroup"):
        ncols = max(ncols, int(tgroup.get("cols", "0") or 0))
        for spec in tgroup.findall("colspec"):
            if spec.get("colname") and spec.get("colnum", "").isdigit():
                colnums[spec.get("colname")] = int(spec.get("colnum"))
        for sect in tgroup:
            if _tag(sect) == "thead":
                head_rows += [r for r in sect if _tag(r) == "row"]
            elif _tag(sect) == "tbody":
                body_rows += [r for r in sect if _tag(r) == "row"]
    if not (head_rows or body_rows):
        return out
    if not ncols:
        ncols = max(len([e for e in r if _tag(e) == "entry"]) for r in head_rows + body_rows)
    head = _grid(head_rows, colnums, ncols, ctx, repeat_span=True)
    body = [r for r in _grid(body_rows, colnums, ncols, ctx, repeat_span=False) if any(r)]
    if head:
        header = []
        for c in range(ncols):
            seen: list[str] = []
            for r in head:
                if r[c] and r[c] not in seen:
                    seen.append(r[c])
            header.append(" ".join(seen))
    else:
        header, body = body[0], body[1:]
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * ncols]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    out.append("\n".join(lines))
    return out


# -- afbeeldingen -----------------------------------------------------------

def _images(el, ctx: _Ctx) -> list[str]:
    out = []
    for ill in ([el] if _tag(el) == "illustratie" else el.iter("illustratie")):
        name = ill.get("naam")
        if name:
            ctx.images.append(name)
            out.append(f"![[{name}]]")
    return out


# -- hele document ----------------------------------------------------------

def _meta_line(label: str, value: str | None) -> str:
    return f"- **{label}:** {value}" if value else ""


def _first(meta: dict, *keys: str) -> str | None:
    for k in keys:
        if meta.get(k):
            return meta[k][0]
    return None


def _nl_date(iso: str | None) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return iso
    months = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
              "augustus", "september", "oktober", "november", "december"]
    return f"{int(m.group(3))} {months[int(m.group(2)) - 1]} {m.group(1)}"


def _received_lines(text: str | None) -> list[str]:
    """"Ontvangen 17 september 2024" / "Vastgesteld 2 juni 2026" → eigen label."""
    m = re.match(r"^(\w+)\s+(\d.*)$", text or "")
    return [_meta_line(m.group(1), m.group(2))] if m else [_meta_line("Datum stuk", text)]


def _kop_lines(root) -> dict[str, str]:
    """De tekstregels uit de kop (vergaderjaar, kamer, documenttype)."""
    out: dict[str, str] = {}
    for line in root.iter("tekstregel"):
        key = line.get("inhoud")
        if key and key not in out:
            out[key] = _text_of(line)
    return out


def xml_to_markdown(xml: bytes, ident: str, meta: dict | None = None) -> tuple[str, list[str]]:
    """Officiële-publicatie-XML → (markdown, namen van de afbeeldingen)."""
    meta = meta or {}
    try:
        root = etree.fromstring(xml, _parser())
    except etree.XMLSyntaxError as e:
        raise ConversionError(f"De XML van {ident} is niet leesbaar: {e}") from e

    ctx = _Ctx(ident=ident)
    lines = _kop_lines(root)
    body_root = next(
        (c for c in root if _tag(c) in ("kamerstuk", "kamervragen", "handelingen")), None
    )
    kind = _tag(body_root) if body_root is not None else None

    title = None
    subtitle = None
    received = None
    blocks: list[str] = []

    if kind == "kamerstuk":
        dossier = body_root.find("dossier")
        if dossier is not None and dossier.find("titel") is not None:
            title = _inline(dossier.find("titel"), ctx, plain=True).strip()
        stuk = body_root.find("stuk")
        if stuk is not None:
            if stuk.find("titel") is not None:
                subtitle = _inline(stuk.find("titel"), ctx, plain=True).strip()
            if stuk.find("datumtekst") is not None:
                received = _text_of(stuk.find("datumtekst"))
            blocks += _children(stuk, ctx, 3, skip=("titel",))
        blocks += _children(
            body_root, ctx, 3,
            skip=("stuk", "dossier", "kamerstukkop"),
        )
    elif kind == "kamervragen":
        omschr = {o.get("type"): o for o in body_root.findall("kamervraagomschrijving")}
        onderwerp = body_root.find(".//kamervraagonderwerp")
        title = _text_of(onderwerp) if onderwerp is not None else None
        for key in ("vraag", "antwoord"):
            o = omschr.get(key)
            if o is not None:
                blocks.append(_para(_inline(o, ctx)))
        blocks += _children(
            body_root, ctx, 2, skip=("kamervraagkop", "kamervraagnummer"),
        )
        subtitle = None
    elif kind == "handelingen":
        blocks += _children(body_root, ctx, 2)
    else:
        blocks += _children(root, ctx, 2)

    if kind == "kamervragen":
        title = _first(meta, "DC.title") or (f"Kamervragen over {title}" if title else None)
    title = title or _first(meta, "DC.title") or _display_id(ident)
    soort = _first(meta, "OVERHEIDop.documenttitel") or next(
        (v for v in meta.get("DC.type", []) if v not in ("Kamerstuk", "officiële publicatie")),
        None,
    )
    head = [
        f"# {_WS.sub(' ', title).strip()}",
        "",
        "\n".join(filter(None, [
            _meta_line("Publicatie", f"{_display_id(ident)} (`{ident}`)"),
            _meta_line("Soort", soort),
            _meta_line("Vergaderjaar", (lines.get("vergaderjaar") or "").replace("Vergaderjaar ", "")
                       or _first(meta, "OVERHEIDop.vergaderjaar")),
            _meta_line("Kamer", lines.get("kameraanduiding")),
            *_received_lines(received),
            _meta_line("Datum", _nl_date(_first(meta, "DCTERMS.issued"))),
            _meta_line("Indiener", _first(meta, "OVERHEIDop.indiener")),
            _meta_line("Bron", f"<{BASE}/{ident}.html>"),
        ])),
    ]
    if subtitle:
        head += ["", _heading(subtitle, 2)]

    bijlagen = meta.get("OVERHEIDop.bijlage", [])
    tail: list[str] = []
    if bijlagen:
        tail += ["## Bijlagen bij dit stuk", "",
                 "\n".join(f"- [{b}]({BASE}/{b}.html)" for b in bijlagen)]
    if ctx.notes:
        tail += ["", "\n\n".join(f"[^{label}]: {text}" for label, text in ctx.notes)]

    parts = ["\n".join(head), *blocks, "\n".join(tail).strip()]
    markdown = tidy("\n\n".join(p for p in parts if p))
    # De drempel geldt de inhoud, niet de kop: metadata alleen is geen document.
    if sum(len(b.strip()) for b in blocks) < _MIN_BODY:
        raise ConversionError(f"Geen leesbare tekst gevonden in {ident}.")
    return markdown, list(dict.fromkeys(ctx.images))
