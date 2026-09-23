"""Nederlandse wetgeving uit officiële BWB-XML, met portal-HTML als terugval.

Invoer mag een volledige wetten.overheid.nl-URL zijn of een BWB-identifier
(eventueel met versiedatum):
  https://wetten.overheid.nl/BWBR0040940/2021-07-01
  BWBR0040940
  BWBR0040940/2021-07-01

De hoofdroute kiest in het KOOP-manifest de toestand die op de peildatum gold,
of bij een ingetrokken regeling de laatst geldende toestand. Identiteit en
`@inwerkingtreding` worden hard gecontroleerd. Alleen wanneer die route niet
beschikbaar is, volgt de bestaande portal-HTML-route hieronder:

    div#regeling
    ├── h1                                de citeertitel
    ├── div.article__header--main
    │   └── p.regeling-toestand-meldingen "Geraadpleegd op … Geldend van … t/m …"
    └── div.wetgeving                     de regeling zelf

De omzetting neemt `div.wetgeving` plus die `h1`. Daarmee valt het statusblok
vanzelf buiten de tekst — het is geen wettekst maar wel de enige plek waar staat
wélke versie dit is, dus het gaat naar het herkomstbestand.

Binnen de regeling staat achter elke kop een `ul[aria-label="Lijst met mogelijke
acties …"]` met zes portalknoppen. Die gaan eruit op hun aria-label. Eerder
gebeurde dat op klassenaam plus een tekstlengtegrens, en dat is precies
misgegaan: de kop en de melding "[Wijziging(en) zonder datum inwerkingtreding
aanwezig …]" staan in dezelfde div, en zodra de knoppen eruit waren paste die
div onder de grens. `decompose()` nam de `<h4>` mee. Vijftien artikelkoppen uit
het Wetboek van Strafrecht en zes uit de Awb verdwenen zo, met hun leden onder
het vórige artikel.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .. import net, verify
from ..herkomst import Herkomst, vandaag
from ..source_structure import source_structure, bind_structure, record_source
from ..errors import ConversionError
from ..render import collapse_ws, container_to_markdown
from .wetten_footnotes import prepare_footnotes
from . import bwb_xml

# BWB-identifiers: BWBR (regelingen), BWBV (verdragen), BWBW, BWBS, …
_BWB_RE = re.compile(r"BWB[A-Z]\d+", re.I)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# De portalknoppen achter elke kop ("Toon relaties in LiDO", "Druk af", …).
_MENU_SELECTOR = 'ul[aria-label^="Lijst met mogelijke acties"]'

_TIMEOUT = 60
_MIN_USEFUL_LENGTH = 40
_REPOSITORY = "https://repository.officiele-overheidspublicaties.nl/bwb"
_BWB_MEDIA_TYPE = "application/xml"


class _BwbXmlNietBeschikbaar(RuntimeError):
    """Alleen een ontbrekende route mag naar portal-HTML terugvallen."""


def matches(query: str) -> bool:
    """Hoort deze invoer bij wetten.overheid.nl?"""
    return "wetten.overheid.nl" in query.lower() or bool(_BWB_RE.search(query))


def fetch(query: str) -> tuple[str, str, Herkomst]:
    """Probeer officiële BWB-XML; gebruik portal-HTML alleen als terugval."""
    try:
        return _fetch_bwb_xml(query)
    except _BwbXmlNietBeschikbaar as exc:
        markdown, label, herkomst = _fetch_html(query)
        melding = f"BWB-XML niet beschikbaar ({exc}); HTML-route gebruikt."
        return markdown, label, herkomst.met(
            waarschuwingen=tuple((*herkomst.waarschuwingen, melding))
        )


def _query(query: str) -> tuple[str, str | None, str, str | None]:
    m = _BWB_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig BWB-nummer of wetten.overheid.nl-link herkend "
            "(bv. BWBR0040940 of https://wetten.overheid.nl/BWBR0040940/2021-07-01)."
        )
    bwb = m.group(0).upper()
    date = _DATE_RE.search(query)
    path = bwb + (f"/{date.group(0)}" if date else "")
    url = f"https://wetten.overheid.nl/{path}"

    # Een fragment (#Hoofdstuk1_Titeldeel1.1_Artikel1:1, …) betekent: alleen dat
    # onderdeel. De ids zijn volledige paden, niet kale artikelnummers.
    anchor = unquote(query.split("#", 1)[1]).strip() if "#" in query else None
    return bwb, (date.group(0) if date else None), url, anchor


def _fetch_html(query: str) -> tuple[str, str, Herkomst]:
    """De bestaande portalroute, behouden als expliciete terugval."""
    bwb, _, url, anchor = _query(query)

    r = net.documents().get(url, timeout=_TIMEOUT)
    if r.status_code != 200 or not r.text.strip():
        raise ConversionError(f"Kon regeling {bwb} niet ophalen (status {r.status_code}).")

    html = net.decoded_text(r)
    soup = BeautifulSoup(html, "lxml")

    # Een kale BWB lost op naar de versie die vandaag geldt; die datum staat
    # alleen in de opgeloste URL. Eerder werd het label uit de *gevraagde*
    # string gebouwd, en dan noemt de bronvermelding de versie niet.
    opgeloste_url = getattr(r, "url", "") or url
    versie = _versie_uit_url(opgeloste_url, bwb)
    getoond_pad = f"{bwb}/{versie}" if versie else bwb

    original_container = soup.select_one("div.wetgeving") or soup.select_one("#regeling")
    if original_container is not None and anchor:
        original_container = original_container.find(id=anchor)
    evidence = source_structure(html, original_container, selector="div.wetgeving", anchor=anchor) if original_container is not None else None
    if evidence is not None:
        evidence.update(source_url=opgeloste_url, identifier=bwb, language="nl", role="document")
    markdown, tellingen, waarschuwingen = _soup_to_markdown(soup, anchor, bron=url)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Geen leesbare wettekst gevonden voor {bwb}.")

    label = f"wetten.overheid.nl • {getoond_pad}" + (f" #{anchor}" if anchor else "")
    provenance = _herkomst(soup, bwb, versie, opgeloste_url, url, tellingen, waarschuwingen)
    if evidence is not None:
        provenance = bind_structure(provenance, markdown, [evidence])
    return markdown, label, provenance


def _antwoordbytes(response) -> bytes:
    inhoud = getattr(response, "content", None)
    if inhoud is not None:
        return bytes(inhoud)
    return (getattr(response, "text", "") or "").encode(
        getattr(response, "encoding", None) or "utf-8"
    )


def _volgnummer(label: str | None) -> int:
    match = re.search(r"_(\d+)$", label or "")
    return int(match.group(1)) if match else -1


def kies_versie(manifest: bytes, peildatum: str) -> dict:
    """Kies de toestand die gold, of de laatste vóór intrekking.

    De XML-manifestatie en haar hash horen bij elkaar. De hash is alleen een
    aantekening: BWBR0005252 bewijst dat KOOP daar verouderde bestandsgrootte
    en bytes naast een inhoudelijk juiste toestand kan publiceren.
    """
    try:
        root = ET.fromstring(manifest)
    except ET.ParseError as exc:
        raise _BwbXmlNietBeschikbaar(f"manifest is niet leesbaar: {exc}") from exc
    versies = []
    for expressie in root.iter("expression"):
        metadata = expressie.find("metadata")
        begin = metadata.findtext("datum_inwerkingtreding") if metadata is not None else None
        eind = metadata.findtext("einddatum") if metadata is not None else None
        xml_manifestatie = next(
            (m for m in expressie.findall("manifestation")
             if (m.get("label") or "").lower() == "xml"),
            None,
        )
        item = xml_manifestatie.find("item") if xml_manifestatie is not None else None
        if not begin or item is None or item.get("_deleted") == "true":
            continue
        versies.append({
            "label": expressie.get("label"),
            "begin": begin,
            "eind": None if (eind or "9999").startswith("9999") else eind,
            "bestand": item.get("label"),
            "sha512": xml_manifestatie.findtext("metadata/hashcode"),
            "ingetrokken_op": None,
        })
    if not versies:
        raise _BwbXmlNietBeschikbaar("manifest bevat geen XML-toestanden")

    def laatste(kandidaten):
        return max(kandidaten, key=lambda v: (v["begin"], _volgnummer(v["label"])))

    geldig = [
        v for v in versies
        if v["begin"] <= peildatum <= (v["eind"] or "9999-12-31")
    ]
    if geldig:
        return laatste(geldig)
    eerder = [v for v in versies if v["begin"] <= peildatum]
    if eerder:
        versie = dict(laatste(eerder))
        versie["ingetrokken_op"] = versie["eind"]
        return versie
    eerste = min(versies, key=lambda v: v["begin"])
    raise ConversionError(
        f"Op {peildatum} bestond deze regeling nog niet; de eerste BWB-toestand "
        f"geldt vanaf {eerste['begin']}."
    )


def _fetch_bwb_xml(query: str) -> tuple[str, str, Herkomst]:
    bwb, gevraagde_datum, _, anchor = _query(query)
    if anchor:
        # De XML-omzetter levert bewust het hele document: een fragment knippen
        # zonder het structurele pad zou de broneenheden en bewijsbytes scheiden.
        raise _BwbXmlNietBeschikbaar("fragmentlinks worden nog niet uit BWB-XML geknipt")
    peildatum = gevraagde_datum or vandaag()
    manifest_url = f"{_REPOSITORY}/{bwb}/manifest.xml"
    try:
        manifest_response = net.documents().get(manifest_url, timeout=_TIMEOUT)
    except Exception as exc:
        raise _BwbXmlNietBeschikbaar(type(exc).__name__) from exc
    if manifest_response.status_code != 200:
        raise _BwbXmlNietBeschikbaar(f"manifest gaf HTTP {manifest_response.status_code}")
    manifest = _antwoordbytes(manifest_response)
    versie = kies_versie(manifest, peildatum)
    xml_url = f"{_REPOSITORY}/{bwb}/{versie['label']}/xml/{versie['bestand']}"
    try:
        xml_response = net.documents().get(xml_url, timeout=_TIMEOUT)
    except Exception as exc:
        raise _BwbXmlNietBeschikbaar(type(exc).__name__) from exc
    if xml_response.status_code != 200:
        raise _BwbXmlNietBeschikbaar(f"toestand gaf HTTP {xml_response.status_code}")
    data = _antwoordbytes(xml_response)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConversionError(f"De opgehaalde BWB-XML is niet leesbaar: {exc}") from exc
    if root.tag != "toestand":
        raise ConversionError(
            f"Het opgehaalde bestand is geen BWB-toestand maar <{root.tag}>."
        )
    if root.get("bwb-id") != bwb:
        raise ConversionError(
            f"De BWB-toestand hoort bij {root.get('bwb-id')}, niet bij {bwb}."
        )
    if root.get("inwerkingtreding") != versie["begin"]:
        raise ConversionError(
            f"De BWB-toestand geldt vanaf {root.get('inwerkingtreding')}, terwijl "
            f"het manifest {versie['begin']} aanwijst."
        )

    markdown, eenheden, onbekend, extra = bwb_xml.omzetten(data)
    if onbekend:
        raise ConversionError(f"BWB-XML bevat elementen zonder behandeling: {onbekend}.")
    koppen_bron = len([e for e in eenheden if e.soort in {
        "boek", "deel", "titeldeel", "hoofdstuk", "afdeling", "paragraaf",
        "subparagraaf", "sub-paragraaf", "artikel", "bijlage",
    }])
    if root.find(".//artikel") is not None and koppen_bron == 0:
        raise ConversionError(
            "De BWB-XML bevat <artikel>-elementen, maar de omzetting geeft geen "
            "enkele artikel- of structuurkop; de omzetter weigert liever dan "
            "onvolledige structuur door te laten."
        )
    gemeten_sha512 = hashlib.sha512(data).hexdigest()
    manifest_sha512 = (versie.get("sha512") or "").strip().lower() or None
    wijkt_af = bool(manifest_sha512 and manifest_sha512 != gemeten_sha512)
    waarschuwingen = ["Wetgeving is via de officiële BWB-XML van KOOP opgehaald."]
    if wijkt_af:
        waarschuwingen.append(
            "De SHA-512 wijkt af van het KOOP-manifest; identiteit en versie van "
            "de toestand zijn wel rechtstreeks gecontroleerd."
        )
    bron_url = getattr(xml_response, "url", "") or xml_url
    record_source(
        data,
        media_type=_BWB_MEDIA_TYPE,
        source_format="bwb-xml",
        source_url=bron_url,
        identifier=bwb,
        language="nl",
    )
    titel = " ".join((root.findtext("wetgeving/citeertitel") or "").split()) or None
    herkomst = Herkomst(
        format="bwb-xml",
        bwb=bwb,
        title=titel,
        language="nl",
        version=versie["label"],
        versie=versie["begin"],
        geldend_van=versie["begin"],
        geldend_tot=versie["eind"],
        ingetrokken_op=versie["ingetrokken_op"],
        source_url=f"https://wetten.overheid.nl/{bwb}/{versie['begin']}",
        requested_url=query,
        expired=extra["expired"],
        koppen_bron=koppen_bron,
        koppen_markdown=verify.tel_koppen_markdown(markdown),
        waarschuwingen=tuple(waarschuwingen),
        extra={
            "manifest_url": manifest_url,
            "xml_url": xml_url,
            "xml_sha512_manifest": manifest_sha512,
            "xml_sha512_gemeten": gemeten_sha512,
            "xml_sha512_wijkt_af_van_manifest": wijkt_af,
            "herhaalde_tabelcellen": extra["herhaalde_cellen"],
        },
    )
    label = f"KOOP BWB-XML • {bwb}/{versie['begin']}"
    if versie["ingetrokken_op"]:
        label += f" • ingetrokken per {versie['ingetrokken_op']}"
    return markdown, label, herkomst


def _versie_uit_url(url: str, bwb: str) -> str | None:
    m = re.search(rf"/{re.escape(bwb)}/(\d{{4}}-\d{{2}}-\d{{2}})", url, re.I)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Herkomst: wat de pagina over deze versie zegt
# --------------------------------------------------------------------------

_GELDEND_RE = re.compile(r"Geldend van (\d{2}-\d{2}-\d{4}) t/m (heden|\d{2}-\d{2}-\d{4})")
_GERAADPLEEGD_RE = re.compile(r"Geraadpleegd op (\d{2}-\d{2}-\d{4})")


def _iso(datum: str) -> str | None:
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", datum)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _toestand(soup) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    """Lees `p.regeling-toestand-meldingen`: geldigheid, raadpleegdatum, rest.

    Dit blok staat in `#regeling` maar buiten `div.wetgeving`, dus het komt niet
    in de tekst terecht — en het is de enige plek op de pagina die zegt wélke
    versie je voor je hebt.
    """
    blok = soup.select_one(".regeling-toestand-meldingen")
    if blok is None:
        return None, None, None, ()
    tekst = collapse_ws(blok.get_text(" ", strip=True))

    geldend = _GELDEND_RE.search(tekst)
    geraadpleegd = _GERAADPLEEGD_RE.search(tekst)
    van = _iso(geldend.group(1)) if geldend else None
    tot = _iso(geldend.group(2)) if geldend and geldend.group(2) != "heden" else None

    # Wat er verder staat ("Wijziging(en) zonder datum inwerkingtreding
    # aanwezig.") is een melding over de versie, geen wettekst. Letterlijk mee.
    meldingen = tuple(
        zin.strip()
        for zin in re.split(r"(?<=\.)\s+", tekst)
        if zin.strip() and not zin.startswith(("Geldend van", "Geraadpleegd op"))
    )
    return van, tot, (_iso(geraadpleegd.group(1)) if geraadpleegd else None), meldingen


def _dcterms(soup) -> dict:
    """De dcterms-metatags uit de `<head>` (creator, type, modified, …)."""
    uit = {}
    for meta in soup.select('meta[name^="dcterms:"]'):
        naam = (meta.get("name") or "")[len("dcterms:"):]
        inhoud = (meta.get("content") or "").strip()
        if naam and inhoud:
            uit[naam] = inhoud
    return uit


def _herkomst(soup, bwb, versie, opgeloste_url, gevraagde_url, tellingen, waarschuwingen):
    van, tot, geraadpleegd, meldingen = _toestand(soup)
    dcterms = _dcterms(soup)
    h1 = soup.select_one("#regeling h1")
    koppen_bron, koppen_markdown = tellingen
    return Herkomst(
        format="wetten-nl",
        bwb=bwb,
        title=collapse_ws(h1.get_text(" ", strip=True)) if h1 else dcterms.get("title"),
        language=dcterms.get("language") or "nl",
        versie=versie,
        geldend_van=van,
        geldend_tot=tot,
        geraadpleegd=geraadpleegd or vandaag(),
        source_url=f"https://wetten.overheid.nl/{bwb}/{versie}" if versie else opgeloste_url,
        requested_url=gevraagde_url,
        toestand_meldingen=meldingen,
        koppen_bron=koppen_bron,
        koppen_markdown=koppen_markdown,
        waarschuwingen=waarschuwingen,
        extra={"dcterms": dcterms} if dcterms else {},
    )


# --------------------------------------------------------------------------
# De omzetting
# --------------------------------------------------------------------------

def _soup_to_markdown(
    soup, anchor: str | None = None, *, bron: str = "wetten.overheid.nl"
) -> tuple[str, tuple[int, int], tuple[str, ...]]:
    waarschuwingen: list[str] = []

    body = soup.select_one("div.wetgeving")
    gebruikt_terugval = body is None
    if gebruikt_terugval:
        # Geen bekende opbouw meer. Doorgaan met #regeling is beter dan niets
        # teruggeven, maar nooit stilzwijgend: dan bevat de uitvoer ook het
        # statusblok en mogelijk portalruis.
        body = soup.select_one("#regeling")
        if body is None:
            raise ConversionError(
                "Geen wettekst gevonden op deze pagina. Controleer het BWB-nummer "
                "of de link."
            )
        for el in body.select(".regeling-toestand-meldingen"):
            el.decompose()
        waarschuwingen.append(
            "Onverwachte paginaopbouw: geen div.wetgeving gevonden. De hele "
            "#regeling-container is omgezet; controleer of er geen portalruis in staat."
        )

    if anchor:
        # Binnen de regeling zoeken, niet in de hele pagina: een id uit de
        # inhoudsopgave of een modaal venster is geen wettekst.
        container = body.find(id=anchor)
        if container is None:
            raise ConversionError(
                f"Onderdeel '#{anchor}' niet gevonden in de regeling. "
                f"Controleer het anker in de link."
            )
    else:
        container = body

    for tag in container(["script", "style", "noscript"]):
        tag.decompose()

    # De actiemenu's achter elke kop zijn navigatie van de portal. Ze gaan eruit
    # op hun aria-label en niet op tekstlengte: een lengtegrens raakt ook de
    # kopblokken zelf — precies hoe vijftien artikelkoppen verdwenen.
    for el in container.select(_MENU_SELECTOR):
        el.decompose()
    for el in container.select(".visually-hidden"):
        el.decompose()

    # De per-artikel herhaalde melding "[Wijziging(en) zonder datum
    # inwerkingtreding aanwezig. Zie het wijzigingenoverzicht.]" blijft bewust
    # staan. Het is tekst van de bron, en wat ermee gebeurt is een keuze van de
    # afnemer — niet van de omzetter.

    prepare_footnotes(container)
    markdown = container_to_markdown(container)
    koppen_bron = verify.tel_koppen_html(container)
    waarschuwingen.extend(verify.controleer_koppen(container, markdown, bron=bron))

    # De titel staat als h1 buiten div.wetgeving. Bij een fragment hoort hij er
    # niet bij: dat is een onderdeel, geen regeling. Bij de terugval zit hij al
    # in de container, dus dan niet nog een keer.
    if anchor is None and not gebruikt_terugval:
        h1 = soup.select_one("#regeling > h1") or soup.select_one("#regeling h1")
        if h1 is not None:
            titel = collapse_ws(h1.get_text(" ", strip=True))
            if titel:
                markdown = f"# {titel}\n\n{markdown}"
                # De h1 komt uit de bron (hij staat in #regeling), dus hij telt
                # aan beide kanten mee. Anders meldt het zijbestand één kop meer
                # geleverd dan de bron had, en dat leest als winst uit het niets.
                koppen_bron += 1

    return markdown, (koppen_bron, verify.tel_koppen_markdown(markdown)), tuple(waarschuwingen)


def _html_to_markdown(html: str, anchor: str | None = None) -> str:
    """Alleen de markdown, voor wie de herkomst niet nodig heeft (tests)."""
    return _soup_to_markdown(BeautifulSoup(html, "lxml"), anchor)[0]
