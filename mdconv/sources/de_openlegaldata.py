"""Duitse rechtspraak via OpenLegalData (de.openlegaldata.io) — primaire bron.

Een gratis, **sleutelloze** JSON-API die rechtstreeks op ECLI doorzoekbaar is
(`?ecli=<ECLI>`) — geen sessie/tokendans zoals bij rechtsprechung-im-
internet.de nodig, en met een veel bredere dekking: ruim 400.000 zaken, ook
regionale/deelstaatgerechten (bv. OVG Nordrhein-Westfalen), niet alleen de
zeven federale gerechten.

**Drie kwaliteitsniveaus, want OpenLegalData aggregeert meerdere bronnen met
elk hun eigen HTML-conventie:**

1. Federale uitspraken volgen de "RspDL"-conventie (zie `juris_markup.py`) —
   dezelfde structuur als rechtsprechung-im-internet.de, alleen als HTML met
   `<h2>`-koppen per sectie in plaats van XML. Hoogste betrouwbaarheid.
2. Sommige deelstaten (geverifieerd: Nordrhein-Westfalen) gebruiken een eigen
   conventie: `<span class="absatzRechts">N</span>` gevolgd door een sibling
   `<p class="absatzLinks">tekst</p>` — het randnummer staat dus **naast**
   de alinea, niet erin. `_merge_absatz_pairs()` herkent dit specifieke
   patroon en voegt ze samen tot "N. tekst", net als bij de RspDL-conventie.
3. Onbekende structuur (een deelstaat-conventie die nog niet is gezien): een
   generieke `markdownify`-conversie (`container_to_markdown`) als eerlijk
   best-effort-vangnet — leesbaar, maar zonder de garantie dat randnummers
   correct bij hun alinea blijven staan.

Is een ECLI hier niet te vinden (bv. een zeer recente uitspraak die nog niet
geïndexeerd is), dan valt `fetch()` terug op `de_rechtsprechung.fetch()`.
"""

from __future__ import annotations

import lxml.html

from .. import net
from ..errors import ConversionError
from ..render import container_to_markdown, tidy
from . import de_rechtsprechung
from .de_rechtsprechung import ECLI_RE
from .juris_markup import has_rspdl_structure, ln, walk_dl_section

_API_BASE = "https://de.openlegaldata.io/api/cases/"
_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40


def fetch(query: str) -> tuple[str, str]:
    """Haal een Duitse uitspraak op; geeft (markdown, bronvermelding)."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError(
            "Geen geldig Duits ECLI-nummer herkend (bv. ECLI:DE:BGH:2019:240919BVIZB39.18.0)."
        )
    ecli = m.group(0).upper()

    case = _find_case(ecli)
    if case is not None:
        markdown = _case_to_markdown(case)
        if len(markdown.strip()) >= _MIN_USEFUL_LENGTH:
            return markdown, f"OpenLegalData • {ecli}"

    # Terugval: rechtsprechung-im-internet.de heeft een andere index en dekking.
    return de_rechtsprechung.fetch(query)


def _find_case(ecli: str) -> dict | None:
    try:
        r = net.documents().get(_API_BASE, params={"ecli": ecli}, timeout=_TIMEOUT)
    except Exception:  # noqa: BLE001 — een misser hier is geen fatale fout, terugval volgt
        return None
    if r.status_code != 200:
        return None
    try:
        results = r.json().get("results") or []
    except ValueError:
        return None
    if not results:
        return None

    case_id = results[0].get("id")
    if case_id is None:
        return None
    try:
        r2 = net.documents().get(f"{_API_BASE}{case_id}/", timeout=_TIMEOUT)
    except Exception:  # noqa: BLE001
        return None
    if r2.status_code != 200:
        return None
    try:
        return r2.json()
    except ValueError:
        return None


def _case_to_markdown(case: dict) -> str:
    court = (case.get("court") or {}).get("name") or ""
    if not court or court == "Unknown court":
        # Data-kwaliteitsgat bij OpenLegalData voor sommige (vooral oudere)
        # zaken: het gerecht staat dan wél in de ECLI zelf (3e onderdeel).
        ecli = case.get("ecli") or ""
        parts = ecli.split(":")
        court = parts[2] if len(parts) > 2 else ""
    file_number = case.get("file_number") or ""
    heading = " ".join(p for p in (court, file_number) if p) or "Uitspraak"
    blocks = [f"# {heading}"]

    content = (case.get("content") or "").strip()
    if content:
        blocks.append(_content_to_markdown(content))

    return tidy("\n\n".join(blocks))


def _content_to_markdown(content: str) -> str:
    if has_rspdl_structure(content):
        return _render_rspdl(content)

    merged = _merge_absatz_pairs(content)
    if merged is not None:
        return merged

    # Onbekende conventie: generieke, eerlijke best-effort-conversie.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(f"<div>{content}</div>", "lxml")
    return container_to_markdown(soup.div)


def _render_rspdl(content: str) -> str:
    """Federale structuur: `<h2>`-sectiekoppen gevolgd door een `<div>` met
    de RspDL-alinea's — zelfde conventie als `de_rechtsprechung.py`, nu als
    HTML-fragment in plaats van XML met een DTD."""
    root = lxml.html.fromstring(content)
    blocks: list[str] = []
    label = None
    for child in root:
        tag = ln(child.tag)
        if tag == "h2":
            label = (child.text_content() or "").strip()
        elif tag == "div":
            rendered = walk_dl_section(child)
            if rendered:
                if label:
                    blocks.append(f"## {label}")
                blocks.extend(rendered)
            label = None
    return "\n\n".join(blocks)


def _merge_absatz_pairs(content: str) -> str | None:
    """Deelstaatconventie (geverifieerd: NRW): `<span class="absatzRechts">N</span>`
    gevolgd door een sibling `<p class="absatzLinks">tekst</p>` — het nummer
    staat naast de alinea, niet erin. Voegt ze samen tot "N. tekst", net als
    bij de RspDL-conventie. Geeft None als dit patroon niet voorkomt, zodat de
    aanroeper op de generieke fallback terugvalt."""
    root = lxml.html.fromstring(content)
    numbers = root.xpath('.//span[@class="absatzRechts"]')
    if not numbers:
        return None

    blocks: list[str] = []
    for child in root:
        tag = ln(child.tag)
        if tag == "h2":
            label = (child.text_content() or "").strip()
            if label:
                blocks.append(f"## {label}")
        elif tag == "span" and child.get("class") == "absatzRechts":
            continue  # wordt verwerkt via de eropvolgende <p>
        elif tag == "p" and child.get("class") == "absatzLinks":
            prev = child.getprevious()
            num = ""
            if prev is not None and ln(prev.tag) == "span" and prev.get("class") == "absatzRechts":
                num = (prev.text_content() or "").strip()
            text = _inline_html(child)
            if text:
                blocks.append(f"{num}. {text}" if num else text)
        elif tag == "p":
            text = _inline_html(child)
            if text:
                blocks.append(text)
    return "\n\n".join(blocks)


def _inline_html(el) -> str:
    """Platte tekst van een HTML-element, met `<u>`/onderstreping behouden
    (bv. de "Gründe:"-kop die als `<span style="text-decoration:underline">`
    binnen de alinea staat) en overtollige witruimte genormaliseerd."""
    from ..render import collapse_ws

    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        style = (child.get("style") or "")
        inner = _inline_html(child)
        if "underline" in style:
            parts.append(f"<u>{inner}</u>" if inner.strip() else inner)
        elif ln(child.tag) == "br":
            parts.append(" ")
        else:
            parts.append(inner)
        if child.tail:
            parts.append(child.tail)
    return collapse_ws("".join(parts))
