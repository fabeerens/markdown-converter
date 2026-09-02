"""Bronherkenning en -routering.

`from_link()` bepaalt uit een ECLI/CELEX/BWB/link welke bron erbij hoort en
routeert door. `from_file()` en `from_file_bytes()` doen hetzelfde voor
geüploade of gedownloade bestanden.

Alles geeft een `Document` terug: markdown, een bronvermelding voor de UI en de
soort (`caselaw` of `document`), die bepaalt welk AI-opschoonprofiel de UI
voorstelt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import (
    be_juportal,
    de_openlegaldata,
    eurlex,
    files,
    formex,
    fr_conseil_constitutionnel,
    hudoc,
    pasted_text,
    pdf_images,
    rechtspraak,
    wetten,
)

# Nationale rechtspraak buiten NL/EU/EHRM, per ECLI-landcode. Uitbreidbaar: voeg
# een module met dezelfde vorm toe (`ECLI_RE` + `fetch(query) -> (markdown, bron)`)
# en registreer hem hier — de rest van de routering werkt dan automatisch mee.
# DE: de_openlegaldata is de primaire bron en valt intern terug op
# de_rechtsprechung (rechtsprechung-im-internet.de) als een ECLI daar niet
# gevonden wordt.
_NATIONAL_SOURCES = {
    "DE": de_openlegaldata,
    "BE": be_juportal,
    "FR": fr_conseil_constitutionnel,
}


def _national_source(query: str):
    m = re.search(r"ECLI:([A-Z]{2}):", query, re.I)
    return _NATIONAL_SOURCES.get(m.group(1).upper()) if m else None

KIND_CASELAW = "caselaw"
KIND_DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class Attachment:
    """Eén bijlage (losse afbeelding uit een PDF), klaar om als los bestand weg te schrijven."""

    filename: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Document:
    """Eén geconverteerd document, klaar voor de editor.

    `attachments` (losse afbeeldingen uit een PDF, zie `pdf_images.py`) gaat
    NIET mee in `as_json()` — binaire data hoort niet in de conversie-JSON.
    De API-laag slaat ze apart op (`mdconv.attachments`) en stuurt alleen een
    token + aantal mee; pas bij het downloaden wordt er een zip van gemaakt.
    """

    markdown: str
    source: str
    kind: str = KIND_DOCUMENT
    attachments: tuple = ()

    def as_json(self) -> dict:
        return {"markdown": self.markdown, "source": self.source, "kind": self.kind}


# --------------------------------------------------------------------------
# Links en identifiers
# --------------------------------------------------------------------------

def detect_source(query: str) -> str | None:
    """'rechtspraak', 'hudoc', 'wetten', of None (→ EUR-Lex).

    De volgorde is bewust: een EHRM-ECLI of HUDOC-link wint van alles, want die
    bevat cijfergroepen die anders als iets anders gelezen worden. Een los
    HUDOC-item-id (001-210077) mag geen Nederlandse ECLI kapen, vandaar de
    extra uitsluitingen daar.
    """
    q = query.strip()
    low = q.lower()

    if hudoc.ECHR_ECLI_RE.search(q) or "hudoc.echr.coe.int" in low:
        return "hudoc"
    if wetten.matches(q):
        return "wetten"
    if (hudoc.ITEM_ID_RE.search(q)
            and "rechtspraak" not in low
            and not rechtspraak.ECLI_RE.search(q)):
        return "hudoc"
    # Alleen Nederlandse ECLI's horen bij Rechtspraak.nl. EU-ECLI's (ECLI:EU:…)
    # gaan naar EUR-Lex; overige ECLI's vallen ook door.
    if "rechtspraak.nl" in low or re.search(r"ECLI:NL:", q, re.I):
        return "rechtspraak"
    if _national_source(q):
        return "national"
    return None


def from_link(query: str, lang: str = "NL") -> Document:
    """Los een link/identifier op naar een document."""
    source = detect_source(query)
    if source == "rechtspraak":
        markdown, note = rechtspraak.fetch(query)
    elif source == "hudoc":
        markdown, note = hudoc.fetch(query, lang)
    elif source == "wetten":
        markdown, note = wetten.fetch(query)
    elif source == "national":
        markdown, note = _national_source(query).fetch(query)
    else:
        markdown, note = eurlex.fetch_and_convert(query, lang)
    return Document(markdown=markdown, source=note, kind=kind_for_source(note))


# --------------------------------------------------------------------------
# Bestanden
# --------------------------------------------------------------------------

# Formex-XML herkennen aan de eerste kilobytes: dan gaat het door de
# structuurparser in plaats van MarkItDown.
_FORMEX_MARKERS = (b"FORMEX", b"<ACT", b"ENACTING.TERMS",
                   b"CONS.DOC", b"<CONSID", b"<ARTICLE")
# Onder deze lengte gaan we ervan uit dat de Formex-parser het mis had (bv. een
# XML die alleen op een marker leek) en proberen we MarkItDown alsnog.
_FORMEX_MIN_LENGTH = 40


def looks_like_formex(data: bytes) -> bool:
    """Heuristiek: is dit een EUR-Lex Formex-XML-document?"""
    head = data[:8000].upper()
    return b"<" in head[:200] and any(m in head for m in _FORMEX_MARKERS)


def from_file(data: bytes, filename: str, *, extract_images: bool = False) -> Document:
    """Zet een geüpload bestand om naar een document.

    `extract_images=True` haalt bij een PDF ook losse ingesloten afbeeldingen
    eruit (grafieken, screenshots — hele-pagina-scans uitgesloten, zie
    `pdf_images.py`) en zet die als Obsidian wikilink-bijlagen onder een eigen
    "Bijlagen"-sectie aan het einde van het document. Bij elk ander
    bestandstype (of als poppler-utils niet geïnstalleerd is) wordt deze vlag
    genegeerd, precies zoals de UI 'm ook alleen bij PDF-invoer toont.
    """
    if filename.lower().endswith(".xml") and looks_like_formex(data):
        markdown = formex.convert_formex(data)
        if len(markdown.strip()) >= _FORMEX_MIN_LENGTH:
            return Document(markdown=markdown, source=f"Formex XML • {filename}")

    markdown, engine = files.convert(data, filename)

    attachments: tuple[Attachment, ...] = ()
    if extract_images and filename.lower().endswith(".pdf") and pdf_images.available():
        attachments = _attach_pdf_images(data)
        if attachments:
            embeds = "\n".join(f"![[{a.filename}]]" for a in attachments)
            markdown = f"{markdown.rstrip()}\n\n## Bijlagen\n\n{embeds}\n"
            engine = f"{engine} + {len(attachments)} afbeelding(en)"

    return Document(markdown=markdown, source=f"{engine} • {filename}", attachments=attachments)


def _attach_pdf_images(data: bytes) -> tuple[Attachment, ...]:
    """Losse afbeeldingen uit een PDF, herbenoemd naar `p{paginanummer}[-n].ext`."""
    images = pdf_images.extract_images(data)
    counts: dict[int, int] = {}
    for img in images:
        counts[img.page] = counts.get(img.page, 0) + 1
    attachments = []
    for img in images:
        suffix = "" if counts[img.page] == 1 else f"-{img.index_on_page}"
        attachments.append(Attachment(filename=f"p{img.page:02d}{suffix}.{img.ext}", data=img.data))
    return tuple(attachments)


def from_file_bytes(data: bytes, filename: str, label: str, *, extract_images: bool = False) -> Document:
    """Als `from_file`, maar met een eigen bronvermelding (bv. de URL)."""
    doc = from_file(data, filename, extract_images=extract_images)
    engine = doc.source.split(" • ", 1)[0]
    return Document(
        markdown=doc.markdown, source=f"{engine} • {label}", kind=doc.kind,
        attachments=doc.attachments,
    )


# --------------------------------------------------------------------------
# Handmatig geplakte tekst
# --------------------------------------------------------------------------

def from_pasted_text(html: str | None, text: str | None) -> Document:
    """Zet handmatig geplakte tekst (kaal of verrijkt) om naar een document."""
    markdown, note = pasted_text.convert(html, text)
    return Document(markdown=markdown, source=note, kind=kind_for_source(note))


# --------------------------------------------------------------------------
# Soort document
# --------------------------------------------------------------------------

def kind_for_source(source: str) -> str:
    """Bepaal uit de bronvermelding of dit rechtspraak is.

    Sector 6 in een CELEX is EU-rechtspraak; `ECLI:EU:` idem. Dit bepaalt of de
    UI het uitspraak-profiel voorstelt en de Obsidian-optie aanbiedt.
    """
    s = source or ""
    if s.startswith("Rechtspraak.nl") or s.startswith("HUDOC"):
        return KIND_CASELAW
    if "ECLI:EU:" in s or "CELEX:6" in s:
        return KIND_CASELAW
    # Nationale bronnen (bv. rechtsprechung-im-internet.de voor Duitsland) leveren
    # altijd rechtspraak; hun bronvermelding bevat de ECLI van het betreffende land.
    if re.search(r"ECLI:[A-Z]{2}:", s, re.I):
        return KIND_CASELAW
    return KIND_DOCUMENT
