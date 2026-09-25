"""EPUB → Markdown, zonder AI.

Een EPUB is een zip met XHTML-hoofdstukken plus een package-document (OPF)
dat de leesvolgorde (spine) en de bestanden (manifest) beschrijft. MarkItDown
kan een EPUB al lezen, maar behandelt elk hoofdstuk als losse HTML: interne
links (tussen hoofdstukken, voetnoten, de inhoudsopgave) blijven dan gewone
relatieve `href`'s — kapotte links zodra alle hoofdstukken tot één
Markdown-bestand worden samengevoegd.

Deze module doet de extractie zelf en zet zulke interne links om in
Obsidian-wikilinks naar de kop waar ze naar verwijzen (`[[#Kop]]`, of
`[[#Kop|linktekst]]` als de linktekst afwijkt) — dat blijft, anders dan een
relatief bestandspad, ook werken ná het samenvoegen. Externe links (http(s),
mailto) blijven gewone Markdown-links. Echte koppen (`<h1>`-`<h6>`) komen via
`markdownify` gewoon als `#`-`######` uit.

**Koppromotie op typografie, niet op tekstinhoud.** Veel professioneel
gezette EPUB's (InDesign-export, bv. uitgeversboeken) gebruiken géén échte
kop-tags — hoofdstuktitels en paragraafkoppen zijn gewoon `<p class="...">`
met een eigen alinea-stijl. Tekstueel giswerk ("lijkt dit op een titel?") zou
hier onvoorspelbaar zijn op willekeurige boektekst. Wat wél betrouwbaar is:
de CSS zelf zegt hoe groot/vet/welk lettertype elke stijl heeft — exact wat
een lezer ook visueel als "groter/vetter dan de lopende tekst" zou herkennen.
`_qualifying_heading_classes()` vergelijkt elke alinea-stijl met de stijl die
in het hele boek de meeste tekens beslaat (de facto de hoofdtekst) op drie
signalen (lettergrootte, gewicht, lettertypefamilie) en promoveert alleen
stijlen die daar duidelijk van afwijken — en alleen als de instantie zelf kort
is (een titel/kop, geen alinea die toevallig dezelfde stijlklasse hergebruikt).
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as _markdownify

from .. import render

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Een scheme zoals "http:", "mailto:", "data:" — zo'n link is nooit intern.
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# Een gepromoveerde "kop" die feitelijk een hele alinea is, is geen kop meer
# — dit is de grens tussen een titel/kopregel en lopende tekst.
_MAX_HEADING_CHARS = 150


def convert_epub(data: bytes) -> str | None:
    """Zet EPUB-bytes om naar Markdown, of `None` als dit geen EPUB is die
    deze parser aankan (dan valt de aanroeper terug op MarkItDown)."""
    chapters, class_styles = _read_chapters(data)
    if not chapters:
        return None

    class_to_level = _qualifying_heading_classes(chapters, class_styles)
    if class_to_level:
        for _href, soup in chapters:
            _promote_headings_by_style(soup, class_to_level)

    heading_index = _index_headings(chapters)
    blocks = []
    for href, soup in chapters:
        _rewrite_links(soup, href, heading_index)
        for img in soup.find_all("img"):
            img.decompose()  # verwijst naar een pad binnen de zip; geen bijlage-mechanisme hier
        body = soup.body or soup
        markdown = _markdownify(str(body), heading_style="ATX", bullets="-").strip()
        if markdown:
            blocks.append(markdown)

    return render.tidy("\n\n".join(blocks)) if blocks else None


# --------------------------------------------------------------------------
# EPUB-structuur lezen: container.xml → OPF → manifest/spine → hoofdstukken
# --------------------------------------------------------------------------

def _read_chapters(data: bytes) -> tuple[list[tuple[str, BeautifulSoup]], dict[str, dict]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            opf_path, opf = _read_opf(z)
            manifest = _read_manifest(opf, opf_path)
            hrefs = _spine_hrefs(opf, manifest)
            chapters = [
                (href, BeautifulSoup(z.read(href), "lxml"))
                for href in hrefs if href in z.namelist()
            ]
            class_styles = _load_css_classes(z)
    except (zipfile.BadZipFile, KeyError, OSError):
        return [], {}
    return chapters, class_styles


def _read_opf(z: zipfile.ZipFile) -> tuple[str, BeautifulSoup]:
    container = BeautifulSoup(z.read("META-INF/container.xml"), "xml")
    opf_path = container.find("rootfile")["full-path"]
    return opf_path, BeautifulSoup(z.read(opf_path), "xml")


def _normalize(path: str) -> str:
    return posixpath.normpath(unquote(path))


def _read_manifest(opf: BeautifulSoup, opf_path: str) -> dict[str, str]:
    base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    return {
        item["id"]: _normalize(base + item["href"])
        for item in opf.find_all("item")
        if item.get("id") and item.get("href")
    }


def _spine_hrefs(opf: BeautifulSoup, manifest: dict[str, str]) -> list[str]:
    spine = opf.find("spine")
    if spine is None:
        return []
    out = []
    for itemref in spine.find_all("itemref"):
        # linear="no" is de EPUB3-manier om te zeggen "geen gewone
        # leesvolgorde-pagina" — met name de nav/inhoudsopgave zelf, die
        # anders als dubbele hoofdstuktekst zou meekomen.
        if itemref.get("linear") == "no":
            continue
        idref = itemref.get("idref")
        if idref in manifest:
            out.append(manifest[idref])
    return out


# --------------------------------------------------------------------------
# CSS lezen: alinea-stijl → (lettergrootte in em, gewicht, lettertype)
#
# De auto-gegenereerde CSS van digitale-uitgeverssoftware (InDesign e.d.) is
# vlak: geen @media, geen geneste selectors. Een simpele, niet-geneste
# regex over "selector { declaraties }" is daarom voldoende; @font-face/
# @page-blokken hebben zelf ook geen nesting, dus die worden gewoon als
# (nutteloze, nooit matchende) "klasse" meegelezen — geen probleem.
# --------------------------------------------------------------------------

_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_CLASS_RE = re.compile(r"\.([-\w]+)")
_CSS_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([\d.]+)\s*(em|rem|px|pt|%)?", re.I)
_CSS_FONT_WEIGHT_RE = re.compile(r"font-weight\s*:\s*([a-zA-Z0-9]+)", re.I)
_CSS_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;]+)", re.I)

# Ruwe omzetting naar "em-equivalent", alleen om stijlen relatief aan elkaar
# te kunnen afzetten — geen precieze layoutberekening nodig.
_UNIT_TO_EM = {"em": 1.0, "rem": 1.0, "%": 0.01, "px": 1 / 16, "pt": 1 / 12}
_FONT_WEIGHT_WORDS = {"normal": 400, "bold": 700, "bolder": 700, "lighter": 300}


def _load_css_classes(z: zipfile.ZipFile) -> dict[str, dict]:
    """`{klassenaam: {"size": em-equivalent|None, "weight": int, "family": str|None}}`
    over álle `.css`-bestanden in de zip heen (geen manifest-opzoektocht nodig
    — een los .css-bestand dat toevallig niets matcht, is harmloos)."""
    classes: dict[str, dict] = {}
    for name in z.namelist():
        if not name.lower().endswith(".css"):
            continue
        try:
            css = z.read(name).decode("utf-8", "replace")
        except (KeyError, OSError):
            continue
        for selector, decls in _CSS_RULE_RE.findall(css):
            if selector.strip().startswith("@"):
                continue
            names = {n for part in selector.split(",") for n in _CSS_CLASS_RE.findall(part)}
            if not names:
                continue
            style = _parse_declarations(decls)
            if not style:
                continue
            for cls in names:
                classes.setdefault(cls, {}).update(style)
    return classes


def _parse_declarations(decls: str) -> dict:
    style: dict = {}
    m = _CSS_FONT_SIZE_RE.search(decls)
    if m:
        value, unit = float(m.group(1)), (m.group(2) or "em").lower()
        style["size"] = value * _UNIT_TO_EM.get(unit, 1.0)
    m = _CSS_FONT_WEIGHT_RE.search(decls)
    if m:
        token = m.group(1).lower()
        style["weight"] = _FONT_WEIGHT_WORDS.get(token, int(token) if token.isdigit() else 400)
    m = _CSS_FONT_FAMILY_RE.search(decls)
    if m:
        style["family"] = m.group(1).split(",")[0].strip().strip("'\"").lower()
    return style


# --------------------------------------------------------------------------
# Koppromotie: welke alinea-stijlen zijn typografisch duidelijk "koppen"?
# --------------------------------------------------------------------------

def _qualifying_heading_classes(
    chapters: list[tuple[str, BeautifulSoup]], class_styles: dict[str, dict]
) -> dict[str, int]:
    """`{klassenaam: kopniveau (1-6)}` voor stijlen die typografisch duidelijk
    afwijken van de hoofdtekst — zie de moduledocstring voor de redenering."""
    if not class_styles:
        return {}

    baseline_size, baseline_family = _dominant_style(chapters, class_styles)
    if baseline_size is None:
        return {}

    qualifying_sizes: dict[str, float] = {}
    for cls, style in class_styles.items():
        size = style.get("size")
        if not size or size <= baseline_size:
            continue  # een kop is minstens een fractie groter dan de hoofdtekst
        ratio = size / baseline_size
        score = 0
        if ratio >= 1.5:
            score += 2
        elif ratio >= 1.15:
            score += 1
        if style.get("weight", 400) >= 600:
            score += 1
        family = style.get("family")
        if family and baseline_family and family != baseline_family:
            score += 1
        if score >= 2:
            qualifying_sizes[cls] = size

    if not qualifying_sizes:
        return {}
    # Grootste stijl → h1, volgende → h2, enz. — relatieve rangorde in plaats
    # van vaste ratio-afkappunten, want die verhoudingen verschillen per boek.
    levels = sorted(set(qualifying_sizes.values()), reverse=True)
    level_by_size = {size: min(i + 1, 6) for i, size in enumerate(levels)}
    return {cls: level_by_size[size] for cls, size in qualifying_sizes.items()}


def _dominant_style(
    chapters: list[tuple[str, BeautifulSoup]], class_styles: dict[str, dict]
) -> tuple[float | None, str | None]:
    """De lettergrootte/lettertype die de meeste tekens in het boek beslaat —
    in de praktijk de hoofdtekst, ongeacht hoe de uitgever die stijl noemt."""
    by_size: dict[float, int] = {}
    by_family: dict[str, int] = {}
    for _href, soup in chapters:
        for p in soup.find_all("p"):
            cls = _first_known_class(p, class_styles)
            if cls is None:
                continue
            n = len(p.get_text())
            if not n:
                continue
            style = class_styles[cls]
            if style.get("size"):
                by_size[style["size"]] = by_size.get(style["size"], 0) + n
            if style.get("family"):
                by_family[style["family"]] = by_family.get(style["family"], 0) + n
    baseline_size = max(by_size, key=by_size.get) if by_size else None
    baseline_family = max(by_family, key=by_family.get) if by_family else None
    return baseline_size, baseline_family


def _first_known_class(tag, class_styles: dict[str, dict]) -> str | None:
    for cls in tag.get("class") or ():
        if cls in class_styles:
            return cls
    return None


def _promote_headings_by_style(soup: BeautifulSoup, class_to_level: dict[str, int]) -> None:
    for p in soup.find_all("p"):
        level = None
        for cls in p.get("class") or ():
            if cls in class_to_level:
                level = class_to_level[cls]
                break
        if level is None:
            continue
        text = p.get_text(" ", strip=True)
        if not text or len(text) > _MAX_HEADING_CHARS:
            continue  # een hele alinea in een "kop"-stijl is geen kop
        p.name = f"h{level}"


# --------------------------------------------------------------------------
# Koppen indexeren: (hoofdstuk, anker) → koptekst, voor de wikilinks
# --------------------------------------------------------------------------

def _index_headings(chapters: list[tuple[str, BeautifulSoup]]) -> dict[tuple[str, str | None], str]:
    index: dict[tuple[str, str | None], str] = {}
    for href, soup in chapters:
        first_heading = None
        nearest_heading = None
        for el in soup.find_all(True):
            el_id = el.get("id")
            if el.name in _HEADING_TAGS:
                text = el.get_text(" ", strip=True)
                if text:
                    nearest_heading = text
                    first_heading = first_heading or text
                    if el_id:
                        index[(href, el_id)] = text
                continue
            # Een niet-kop-anker (bv. een voetnootmarkering of een
            # inhoudsopgave-doel) koppelen aan de dichtstbijzijnde
            # voorafgaande kop — een betekenisvolle wikilink in plaats van
            # een dode link naar een interne id die na samenvoegen niets
            # meer betekent.
            if el_id and (href, el_id) not in index and nearest_heading:
                index[(href, el_id)] = nearest_heading
        index[(href, None)] = first_heading
    return index


# --------------------------------------------------------------------------
# Interne links → Obsidian-wikilinks
# --------------------------------------------------------------------------

def _rewrite_links(soup: BeautifulSoup, own_href: str, heading_index: dict) -> None:
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or _SCHEME_RE.match(href):
            continue  # geen href, of een externe link (http/mailto/…) — ongewijzigd
        target, fragment = urldefrag(href)
        target_href = _normalize(posixpath.join(posixpath.dirname(own_href), target)) if target else own_href
        heading = heading_index.get((target_href, unquote(fragment) or None))
        if heading is None:
            heading = heading_index.get((target_href, None))  # terugval: titel van het doelhoofdstuk
        if heading is None:
            # Niet op te lossen (bv. een link naar een niet-hoofdstukbestand) —
            # de platte linktekst laten staan is beter dan een kapotte link.
            a.replace_with(NavigableString(a.get_text()))
            continue
        link_text = a.get_text(" ", strip=True)
        wikilink = f"[[#{heading}]]" if not link_text or link_text == heading else f"[[#{heading}|{link_text}]]"
        a.replace_with(NavigableString(wikilink))
