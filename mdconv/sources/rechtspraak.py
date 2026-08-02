"""Nederlandse rechtspraak via de officiële Open Data API van de Rechtspraak.

`https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>` geeft schone,
gestructureerde XML terug (`<uitspraak>` met `section`/`title`/`parablock`/
`para`), dus er is geen HTML-schoonmaak nodig.
"""

from __future__ import annotations

import re

from lxml import etree

from .. import net
from ..errors import ConversionError

ECLI_RE = re.compile(r"ECLI:[A-Z]{2}:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+", re.I)

_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40


def fetch(query: str) -> tuple[str, str]:
    """Haal een uitspraak op; geeft (markdown, bronvermelding)."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError("Geen geldig ECLI-nummer herkend (bv. ECLI:NL:HR:2012:BQ9251).")
    ecli = m.group(0).upper()

    url = f"https://data.rechtspraak.nl/uitspraken/content?id={ecli}"
    r = net.documents().get(url, timeout=_TIMEOUT)
    if r.status_code != 200 or not r.content.strip():
        raise ConversionError(f"Kon uitspraak {ecli} niet ophalen (status {r.status_code}).")

    markdown = _xml_to_markdown(r.content, ecli)
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(
            f"Uitspraak {ecli} bevat geen (open) tekst. Mogelijk is alleen metadata beschikbaar."
        )
    return markdown, f"Rechtspraak.nl • {ecli}"


def _ln(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _norm(s: str) -> str:
    return " ".join(s.split())


def _xml_to_markdown(xml_bytes: bytes, ecli: str) -> str:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(xml_bytes, parser=parser)
    blocks: list[str] = []

    # Kop uit de Dublin Core-metadata, als die er is.
    title = None
    for el in root.iter():
        parent = el.getparent()
        if _ln(el.tag) == "title" and parent is not None and _ln(parent.tag) == "Description":
            title = _norm("".join(el.itertext()))
            break
    blocks.append(f"# {title}" if title else f"# {ecli}")

    body = next((el for el in root.iter() if _ln(el.tag) in ("uitspraak", "conclusie")), None)
    if body is not None:
        _walk(body, 0, blocks, pending=[""])

    return "\n\n".join(b for b in blocks if b.strip()).strip() + "\n"


def _walk(el, depth: int, blocks: list[str], pending: list[str]) -> None:
    """Doorloop de uitspraak-XML. `pending` draagt een <nr> naar de volgende <para>."""
    for child in el:
        tag = _ln(child.tag)
        if tag == "title":
            txt = _norm("".join(child.itertext()))
            if txt:
                blocks.append(f"{'#' * min(depth + 2, 6)} {txt}")
        elif tag == "nr":
            pending[0] = _norm("".join(child.itertext()))
        elif tag == "para":
            txt = _norm("".join(child.itertext()))
            if pending[0]:
                txt = f"{pending[0]} {txt}".strip()
                pending[0] = ""
            if txt:
                blocks.append(txt)
        elif tag in ("section", "parablock", "list", "item", "listitem",
                     "uitspraak.info", "footnote"):
            _walk(child, depth + (1 if tag == "section" else 0), blocks, pending)
        elif len(child):
            _walk(child, depth, blocks, pending)
        else:
            txt = _norm("".join(child.itertext()))
            if txt:
                blocks.append(txt)
