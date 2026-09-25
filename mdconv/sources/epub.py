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
`markdownify` gewoon als `#`-`######` uit; een EPUB zonder échte kop-tags
(bv. alleen gestylede `<p>`'s) levert dus platte alinea's, geen giswerk-koppen
— dezelfde terughoudendheid als bij de EUR-Lex-koppromotie, maar dat is daar
een aparte, expliciet-beperkte heuristiek (structuurwoorden als "HOOFDSTUK"),
niet iets wat hier zomaar hergebruikt kan worden op willekeurige boektekst.
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


def convert_epub(data: bytes) -> str | None:
    """Zet EPUB-bytes om naar Markdown, of `None` als dit geen EPUB is die
    deze parser aankan (dan valt de aanroeper terug op MarkItDown)."""
    chapters = _read_chapters(data)
    if not chapters:
        return None

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

def _read_chapters(data: bytes) -> list[tuple[str, BeautifulSoup]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            opf_path, opf = _read_opf(z)
            manifest = _read_manifest(opf, opf_path)
            hrefs = _spine_hrefs(opf, manifest)
            return [
                (href, BeautifulSoup(z.read(href), "lxml"))
                for href in hrefs if href in z.namelist()
            ]
    except (zipfile.BadZipFile, KeyError, OSError):
        return []


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
