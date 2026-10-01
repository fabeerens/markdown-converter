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
from dataclasses import dataclass, replace

from ..errors import ConversionError
from ..herkomst import Herkomst
from ..kb_bundle import DOCUMENT_ID
from ..source_structure import capture_source_documents, bind_structure, record_source

from . import (
    be_juportal,
    common,
    consultatie,
    de_openlegaldata,
    docx,
    eurlex,
    files,
    formex,
    fr_conseil_constitutionnel,
    html_document,
    hudoc,
    kamerstuk,
    officiele_bekendmakingen,
    pasted_text,
    pdf_images,
    rechtspraak,
    wetten,
    wgk,
    woo,
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

    `bijlagen` zijn gerelateerde documenten (bijlagen bij een kamerstuk, relaties van een
    open overheid-document); ze staan als linklijst in de Markdown zelf (`common.with_bijlagen`).

    `attachments` (losse afbeeldingen uit een PDF, zie `pdf_images.py`) gaat
    NIET mee in `as_json()` — binaire data hoort niet in de conversie-JSON.
    De API-laag slaat ze apart op (`mdconv.attachments`) en stuurt alleen een
    token + aantal mee; pas bij het downloaden wordt er een zip van gemaakt.
    """

    markdown: str
    source: str
    kind: str = KIND_DOCUMENT
    attachments: tuple = ()
    # Wat er bij het omzetten opviel (bv. koppen die verdwenen). Gaat mee naar
    # de UI én naar het herkomstbestand, zodat een afnemer er alsnog op kan
    # afketsen als de gebruiker het wegklikt.
    warnings: tuple[str, ...] = ()
    # Waar dit vandaan komt; wordt naast de markdown als zijbestand geleverd.
    provenance: Herkomst | None = None
    # Open overheid: de bijlagen (linklijst), het id waaronder de bron dit document
    # kent (om "al geopend" te herkennen) en een voorstel voor de bestandsnaam.
    bijlagen: tuple = ()
    ident: str = ""
    name: str = ""

    def as_json(self) -> dict:
        payload = {
            "markdown": self.markdown,
            "source": self.source,
            "kind": self.kind,
            "warnings": list(self.warnings),
        }
        if self.provenance is not None:
            payload["provenance"] = self.provenance.as_json()
        if self.ident:
            payload["ident"] = self.ident
        if self.name:
            payload["name"] = self.name
        return payload


# --------------------------------------------------------------------------
# Links en identifiers
# --------------------------------------------------------------------------

def detect_source(query: str) -> str | None:
    """'kamerstuk', 'woo', 'consultatie', 'wgk', 'rechtspraak', 'hudoc', 'wetten', 'national'
    of None (→ EUR-Lex).

    De volgorde is bewust: een EHRM-ECLI of HUDOC-link wint van alles, want die
    bevat cijfergroepen die anders als iets anders gelezen worden. Een los
    HUDOC-item-id (001-210077) mag geen Nederlandse ECLI kapen, vandaar de
    extra uitsluitingen daar.
    """
    q = query.strip()
    low = q.lower()

    # Open overheid-vormen zijn ondubbelzinnig, dus eerst: de cijfergroepen in een
    # publicatie-id (`kst-34851-4`) mogen niet als HUDOC-item-id of CELEX gelezen worden.
    # Een Kamerstuk heeft twee herkenningen (de strenge van de kennisbankroute: een volledig
    # geankerd id of de host; de bredere van `kamerstuk`: tweedekamer.nl-links, nieuwe
    # PDF-publicaties, een dossiernotatie in volledige vorm) en één route (`_kamerstuk`,
    # besluit 1 van WP-77).
    if consultatie.matches(q):
        return "consultatie"
    if wgk.matches(q):
        return "wgk"
    if officiele_bekendmakingen.matches(q) or kamerstuk.matches(q) or kamerstuk.is_dossiernotatie(q):
        return "kamerstuk"
    if woo.matches(q):
        return "woo"
    if hudoc.ECHR_ECLI_RE.search(q) or "hudoc.echr.coe.int" in low:
        return "hudoc"
    if wetten.matches(q):
        return "wetten"
    # Een geconsolideerde CELEX van een handeling met een laag nummer bevat
    # dezelfde cijfergroep als een HUDOC-item-id (01999L0001-20040501 →
    # "001-20040501"), vandaar de CELEX-uitsluiting naast die voor ECLI:NL.
    if (hudoc.ITEM_ID_RE.search(q)
            and "rechtspraak" not in low
            and not rechtspraak.ECLI_RE.search(q)
            and not eurlex.is_celex(q)):
        return "hudoc"
    # Alleen Nederlandse ECLI's horen bij Rechtspraak.nl. EU-ECLI's (ECLI:EU:…)
    # gaan naar EUR-Lex; overige ECLI's vallen ook door.
    if "rechtspraak.nl" in low or re.search(r"ECLI:NL:", q, re.I):
        return "rechtspraak"
    if _national_source(q):
        return "national"
    return None


def _uitpakken(resultaat) -> tuple[str, str, Herkomst | None]:
    """Een bron mag `(markdown, bron)` of `(markdown, bron, herkomst)` teruggeven.

    Zo hoeft niet elke bronmodule tegelijk om: wie nog geen herkomst kan leveren
    blijft een paar teruggeven en krijgt `None`. Het contract is daarmee wel
    losser dan "een tuple van twee" — er is een test die dat vastlegt.
    """
    markdown, note, *rest = resultaat
    return markdown, note, (rest[0] if rest else None)


def from_link(query: str, lang: str = "NL") -> Document:
    """Los een link/identifier op naar een document."""
    source = detect_source(query)
    if source in ("kamerstuk", "woo", "consultatie", "wgk"):
        return from_overheid(query)
    with capture_source_documents() as documents:
        if source == "rechtspraak":
            resultaat = rechtspraak.fetch(query)
        elif source == "hudoc":
            resultaat = hudoc.fetch(query, lang)
        elif source == "wetten":
            resultaat = wetten.fetch(query)
        elif source == "national":
            resultaat = _national_source(query).fetch(query)
        else:
            resultaat = eurlex.fetch_and_convert(query, lang)
    return _als_document(resultaat, documents, query, lang)


def from_hudoc_file(data: bytes, record: dict, records: list[dict], **herkomst) -> Document:
    """Een lokaal gedownload HUDOC-Word-bestand met zijn record, als document.

    Hetzelfde als `from_link()` voor een EHRM-vraag, alleen zonder netwerk: dezelfde
    omzetting (`hudoc.omzetten_record()`), hetzelfde bronbewijs en dezelfde herkomst. Waarom
    deze route bestaat en wat ze in de herkomst anders zet, staat bij `hudoc.uit_bestand()`.
    """
    with capture_source_documents() as documents:
        resultaat = hudoc.uit_bestand(data, record, records, **herkomst)
    return _als_document(resultaat, documents, resultaat[2].requested_url, "EN")


def _als_document(resultaat, documents: list[dict], query: str, lang: str) -> Document:
    """Het gedeelde slot van de online en de lokale route: bronbewijs aan de herkomst."""
    markdown, note, herkomst = _uitpakken(resultaat)
    if documents:
        main = next((part for part in reversed(documents) if part["role"] != "preamble"), documents[0])
        if herkomst is None:
            identifier = main.get("identifier") or ""
            herkomst = Herkomst(format="eurlex-html", language=(lang or "NL").lower(),
                                celex=identifier if not identifier.startswith("ECLI:") else None,
                                ecli=identifier if identifier.startswith("ECLI:") else None,
                                source_url=main.get("source_url"), requested_url=query)
        herkomst = bind_structure(herkomst, markdown, documents)
    return Document(
        markdown=markdown,
        source=note,
        kind=kind_for_source(note),
        warnings=herkomst.waarschuwingen if herkomst else (),
        provenance=herkomst,
    )


_UUID_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:_\d+)?$", re.I
)


def _document_from(fetched) -> Document:
    """Een losse download van een Open overheid-bron: zonder herkomst, dus zonder bundel."""
    return Document(
        markdown=common.with_bijlagen(fetched.markdown, fetched.bijlagen), source=fetched.source, kind=KIND_DOCUMENT,
        attachments=tuple(Attachment(filename=n, data=d) for n, d in fetched.images),
        warnings=tuple(fetched.warnings),
        bijlagen=tuple(fetched.bijlagen), ident=fetched.ident, name=fetched.name,
    )


def _kamerstuk(query: str) -> Document:
    """De ene Kamerstukroute (besluit 1 van WP-77), voor `from_link` én het tabblad Open overheid.

    Komt de tekst uit de officiële XML, dan heeft het resultaat herkomst en bronbewijs, zoals
    elke kennisbankroute: dezelfde Markdown als `kb_fetch` levert, zonder bijlagenlijst erin
    (de bijlage-id's staan in de herkomst; de tekst moet byte-gelijk blijven aan wat de
    kennisbank bewaart). Komt de tekst uit een terugval, dan is het een losse download.
    """
    with capture_source_documents() as documents:
        fetched = kamerstuk.fetch(query)
    if fetched.herkomst is None:
        return _document_from(fetched)
    doc = _als_document((fetched.markdown, fetched.source, fetched.herkomst), documents, query, "NL")
    return replace(doc, ident=fetched.ident, name=fetched.name)


def from_overheid(query: str) -> Document:
    """Open overheid: kamerstuk/aanhangsel/Handelingen/bijlage, een open overheid-document,
    een consultatie (met documenten en reacties) of een wetgevingskalender-regeling.

    Routering: een open.overheid.nl-link of id → Woo; een kale UUID is óók een
    Tweede Kamer-Document-Id, dus die probeert eerst open.overheid.nl (één licht
    verzoek) en valt anders terug op de Tweede Kamer-open data; de rest is een
    kamerstuk (id, link, dossiernotatie of D-nummer)."""
    q = query.strip()
    if consultatie.matches(q):
        return _document_from(consultatie.fetch(q))
    if wgk.matches(q):
        return _document_from(wgk.fetch(q))
    if woo.matches(q):
        return _document_from(woo.fetch(q))
    if _UUID_ID.match(q) and (q.count("_") or woo.is_known_id(q)):
        return _document_from(woo.fetch(q))
    return _kamerstuk(q)


def from_kamerstuk(query: str) -> Document:
    """Kamerstuk, aanhangsel of handeling (id, link, dossiernotatie of D-nummer)."""
    return _kamerstuk(query)


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


def from_file(data: bytes, filename: str, *, extract_images: bool = False,
              document_id: str | None = None, source_url: str | None = None) -> Document:
    """Zet een geüpload bestand om naar een document.

    `document_id` is de slug waaronder de kennisbank dit document kent
    (`edpb-guidelines-05-2020`). Zonder slug blijft de download een los
    `.md`-bestand; met slug, en met een bron die de kennisbank kan bewaren (een
    PDF, een HTML-pagina), krijgt het document een herkomst en dus een
    kennisbankbundel. De slug wordt nooit uit de bestandsnaam afgeleid: een naam
    als `rapport-def-v3.pdf` zegt niets over de identiteit, en een gegokte
    identiteit is een document dat onder de verkeerde naam in de kennisbank landt.

    `extract_images=True` haalt bij een PDF ook losse ingesloten afbeeldingen
    eruit (grafieken, screenshots — hele-pagina-scans uitgesloten, zie
    `pdf_images.py`) en zet die als Obsidian wikilink-bijlagen ín de tekst, op
    de pagina waar ze ook echt uit kwamen — mogelijk zodra pdf-inspector een
    bruikbare tekstlaag heeft (`files.convert_pdf_pages()`, per-pagina
    Markdown). Kan pdf-inspector geen tekst geven (bv. terugval naar
    MarkItDown, dat één doorlopende tekst zonder paginagrenzen levert), dan
    is de precieze pagina niet bekend en komen alle afbeeldingen alsnog
    onderaan onder een losse "## Bijlagen"-sectie — beter een grove plek dan
    een gok. Bij elk ander bestandstype (of als poppler-utils niet
    geïnstalleerd is) wordt deze vlag genegeerd, precies zoals de UI 'm ook
    alleen bij PDF-invoer toont.

    Bevat de geëxtraheerde tekst onvertaalde glyphs (`�`, zie
    `files.warn_if_unmapped_glyphs`), dan krijgt de tekst een waarschuwing
    boven zich i.p.v. de gebruiker stilzwijgend een gat in de tekst te laten
    overnemen.
    """
    if document_id is not None and not DOCUMENT_ID.match(document_id):
        raise ConversionError(
            f"Ongeldig documentnummer {document_id!r}: een slug bestaat uit kleine letters, "
            "cijfers en koppeltekens (bijvoorbeeld edpb-guidelines-05-2020).")
    with capture_source_documents() as documenten:
        doc, extra = _omzetten(data, filename, extract_images=extract_images,
                               document_id=document_id, source_url=source_url)
    if not documenten:
        return doc
    herkomst = Herkomst(
        format=documenten[-1]["source_format"] if documenten[-1].get("source_format") else "html-document",
        document_id=document_id, bestandsnaam=filename, source_url=source_url,
        requested_url=source_url, waarschuwingen=doc.warnings,
        extra=extra,
    )
    herkomst = bind_structure(herkomst, doc.markdown, documenten)
    return Document(markdown=doc.markdown, source=doc.source, kind=doc.kind,
                    attachments=doc.attachments, warnings=doc.warnings, provenance=herkomst)


def _omzetten(data: bytes, filename: str, *, extract_images: bool, document_id: str | None,
              source_url: str | None) -> tuple[Document, dict]:
    """Het omzetten zelf; geeft het document en de herkomstvelden van deze bron terug."""
    naam = filename.lower()
    if naam.endswith(".xml") and looks_like_formex(data):
        markdown = formex.convert_formex(data)
        if len(markdown.strip()) >= _FORMEX_MIN_LENGTH:
            return Document(markdown=markdown, source=f"Formex XML • {filename}"), {}

    if naam.endswith((".html", ".htm")):
        try:
            markdown, waarschuwingen, extra = html_document.convert(
                data, source_url=source_url, identifier=document_id)
        except ConversionError as fout:
            return _terugval(data, filename, fout, document_id)
        return Document(markdown=markdown, source=f"HTML • {filename}",
                        warnings=waarschuwingen), extra

    if naam.endswith(".docx"):
        try:
            markdown, meta = docx.convert(data)
        except ConversionError as fout:
            return _terugval(data, filename, fout, document_id)
        record_source(data, media_type=_DOCX_MEDIA_TYPE, source_format="docx",
                      source_url=source_url or "", identifier=document_id)
        return Document(markdown=markdown, source=f"Word • {filename}"), {"docx": meta}

    if extract_images and naam.endswith(".pdf") and pdf_images.available():
        pages = files.convert_pdf_pages(data)
        if pages is not None:
            markdown, attachments = _attach_pdf_images_inline(pages, data)
            markdown = files.warn_if_unmapped_glyphs(markdown)
            engine = files.ENGINE_PDF_INSPECTOR
            if attachments:
                engine = f"{engine} + {len(attachments)} afbeelding(en)"
            extra = _leg_pdf_vast(data, markdown, source_url, document_id)
            return Document(
                markdown=markdown, source=f"{engine} • {filename}", attachments=attachments
            ), extra

    markdown, engine = files.convert(data, filename)

    attachments: tuple[Attachment, ...] = ()
    if extract_images and naam.endswith(".pdf") and pdf_images.available():
        # Hier belanden we alleen als convert_pdf_pages() hierboven None gaf
        # (geen tekstlaag) — geen paginagrenzen bekend, dus terug naar de
        # oude, grove plaatsing: alles onderaan.
        attachments = _attach_pdf_images(data)
        if attachments:
            embeds = "\n".join(f"![[{a.filename}]]" for a in attachments)
            markdown = f"{markdown.rstrip()}\n\n## Bijlagen\n\n{embeds}\n"
            engine = f"{engine} + {len(attachments)} afbeelding(en)"

    markdown = files.warn_if_unmapped_glyphs(markdown)
    extra = {}
    if naam.endswith(".pdf"):
        extra = _leg_pdf_vast(data, markdown, source_url, document_id)
    return Document(markdown=markdown, source=f"{engine} • {filename}",
                    attachments=attachments), extra


_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _terugval(data: bytes, filename: str, fout: ConversionError,
              document_id: str | None) -> tuple[Document, dict]:
    """De strikte route weigerde; wat er dan gebeurt hangt af van wat de gebruiker wil.

    Met een `document_id` wil de gebruiker een kennisbankbundel, en die is alleen zinvol
    als de structuur uit de bron zelf komt: dan is de weigering het antwoord, met de
    reden. Zonder `document_id` is dit de losse markdown-download die deze tool altijd
    al leverde, en die mag niet plotseling ophouden te werken voor een pagina met twee
    `<article>`s of een Word-bestand met automatische nummering. Dan valt de omzetting
    terug op MarkItDown, en de gebruiker leest in de waarschuwing waarom hij géén
    kennisbankbundel krijgt. Niets wordt vastgelegd als bron: er is dan niets te bewijzen.
    """
    if document_id:
        raise fout
    markdown, engine = files.convert(data, filename)
    return Document(markdown=markdown, source=f"{engine} • {filename}",
                    warnings=(f"{fout} De tekst is met {engine} omgezet zonder de structuur van de "
                              "bron; er is geen kennisbankbundel.",)), {}


def _leg_pdf_vast(data: bytes, markdown: str, source_url: str | None, document_id: str | None) -> dict:
    """Bewaar de PDF-bytes en meet de kwaliteit van de omzetting.

    Een PDF heeft geen boom: de kennisbank kan er geen structuur uit herberekenen, dus
    de bytes gaan mee als herkomst (URL, hash) en niet als bewijs. De meting van de
    omzetting gaat in het zijbestand, zodat de kennisbank `source_quality` niet opnieuw
    hoeft te schatten.
    """
    record_source(data, media_type="application/pdf", source_format="pdf",
                  source_url=source_url or "", identifier=document_id)
    return {"kwaliteit": files.pdf_kwaliteit(markdown)}


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


def _attach_pdf_images_inline(pages: list[str], data: bytes) -> tuple[str, tuple[Attachment, ...]]:
    """Plakt per-pagina Markdown (`files.convert_pdf_pages()`) weer aan elkaar
    en zet elke afbeelding direct ná de tekst van de pagina waar hij bij
    hoort — "op de plek waar het in de PDF staat", voor zover dat zonder
    coördinaten haalbaar is (paginagranulariteit, niet exact tussen twee
    alinea's in)."""
    images = pdf_images.extract_images(data)
    images_by_page: dict[int, list] = {}
    for img in images:
        images_by_page.setdefault(img.page, []).append(img)

    blocks: list[str] = []
    attachments: list[Attachment] = []
    for i, page_text in enumerate(pages):
        page_num = i + 1  # index → 1-gebaseerd, zelfde telling als pdfimages -list
        if page_text:
            blocks.append(page_text)

        page_images = images_by_page.get(page_num, [])
        if not page_images:
            continue
        embeds = []
        for img in page_images:
            suffix = "" if len(page_images) == 1 else f"-{img.index_on_page}"
            filename = f"p{page_num:02d}{suffix}.{img.ext}"
            attachments.append(Attachment(filename=filename, data=img.data))
            embeds.append(f"![[{filename}]]")
        blocks.append("\n".join(embeds))

    markdown = "\n\n".join(blocks).strip() + "\n"
    return markdown, tuple(attachments)


def from_file_bytes(data: bytes, filename: str, label: str, *, extract_images: bool = False,
                    document_id: str | None = None, source_url: str | None = None) -> Document:
    """Als `from_file`, maar met een eigen bronvermelding (bv. de URL)."""
    doc = from_file(data, filename, extract_images=extract_images,
                    document_id=document_id, source_url=source_url)
    engine = doc.source.split(" • ", 1)[0]
    return Document(
        markdown=doc.markdown, source=f"{engine} • {label}", kind=doc.kind,
        attachments=doc.attachments, warnings=doc.warnings, provenance=doc.provenance,
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
