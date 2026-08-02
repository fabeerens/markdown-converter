"""Gedeelde HTML→Markdown-conversie en tekstopmaak.

Stond eerder verspreid: `_tidy` bestond drie keer identiek (eurlex, wetten,
formex) en `html_to_markdown` zat in `eurlex.py` terwijl HUDOC het ook
gebruikte. Alles staat nu één keer hier.
"""

from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning
from markdownify import markdownify as _markdownify

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def tidy(text: str) -> str:
    """Maximaal twee opeenvolgende witregels, geen trailing spaces, sluit met \\n."""
    out: list[str] = []
    blank = 0
    for line in text.split("\n"):
        if line.strip() == "":
            blank += 1
            if blank <= 2:
                out.append("")
        else:
            blank = 0
            out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def collapse_ws(s: str) -> str:
    return " ".join(s.split())


def html_to_markdown(html: str) -> str:
    """Schoon een EUR-Lex/HUDOC-pagina op en zet de inhoud om naar Markdown."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "head", "meta", "link"]):
        tag.decompose()

    # EUR-Lex zet genummerde alinea's (overwegingen, arrestpunten, geletterde
    # lijsten) neer als tweekoloms "marker | tekst"-tabellen. Die worden eerst
    # echte lijstitems/alinea's, anders levert de conversie lelijke tabellen op.
    _unwrap_marker_tables(soup)

    container = (
        soup.find(id="text")
        or soup.find(id="TexteOnly")
        or soup.find("div", class_="tabContent")
        or soup.body
        or soup
    )

    markdown = _markdownify(
        str(container),
        heading_style="ATX",
        strip=["a"],  # portal-links weg, leesbare tekst blijft
        bullets="-",
    )
    return tidy(promote_headings(markdown))


def container_to_markdown(container) -> str:
    """Zet een al geselecteerde BeautifulSoup-container om (wetten.overheid.nl)."""
    return tidy(_markdownify(str(container), heading_style="ATX", strip=["a"], bullets="-"))


# --------------------------------------------------------------------------
# Tweekoloms marker-tabellen ontvouwen
# --------------------------------------------------------------------------

# Een korte "marker"-cel: cijfer, letter of Romeins getal, eventueel tussen
# haakjes en/of gevolgd door een punt of sluithaak. Bv. 1  1.  1)  (1)  a)  ii)
_MARKER_RE = re.compile(r"^\(?(?:\d{1,4}|[ivxlcdm]{1,6}|[a-z])\)?[.)]?$", re.I)
_BULLET_CHARS = {"–", "—", "-", "‑", "•", "·", "*"}
_BLOCK_TAGS = {"p", "div", "li", "blockquote", "table", "ul", "ol"}


def _is_marker(text: str) -> bool:
    text = text.strip()
    if len(text) > 6:
        return False
    return text == "" or text in _BULLET_CHARS or bool(_MARKER_RE.match(text))


def _own_rows(table):
    """Rijen die direct bij deze tabel horen (niet bij een geneste tabel)."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _marker_prefix(marker: str) -> str:
    marker = marker.strip()
    if re.fullmatch(r"\d{1,4}", marker):
        return f"{marker}. "        # puur cijfer → genummerd item, nummer behouden
    if marker in _BULLET_CHARS:
        return "- "                 # streepje/bullet → opsommingsitem
    if marker:
        return f"{marker} "         # juridische marker letterlijk houden: "a)", "(1)"
    return ""


def _unwrap_marker_tables(soup) -> None:
    # Binnenste tabellen eerst, zodat geneste layout-tabellen goed omgezet worden.
    for table in reversed(soup.find_all("table")):
        rows = _own_rows(table)
        if not rows:
            continue
        cell_sets = [tr.find_all(["td", "th"], recursive=False) for tr in rows]
        if any(len(cells) != 2 for cells in cell_sets):
            continue
        markers = [cells[0].get_text(" ", strip=True) for cells in cell_sets]
        # Elke eerste cel moet op een marker lijken, en niet allemaal leeg zijn.
        if not all(_is_marker(m) for m in markers) or not any(m for m in markers):
            continue

        replacements = []
        for cells, marker in zip(cell_sets, markers):
            content = cells[1]
            prefix = _marker_prefix(marker)
            if prefix:
                # Zet de marker ín het eerste blok-element van de cel (bv. <p>),
                # zodat hij op dezelfde regel als de tekst blijft staan.
                target = next(
                    (c for c in content.find_all(recursive=False)
                     if c.name in _BLOCK_TAGS and c.get_text(strip=True)),
                    None,
                )
                if target is not None:
                    target.insert(0, NavigableString(prefix))
                else:
                    wrapper = soup.new_tag("p")
                    wrapper.append(NavigableString(prefix))
                    for child in list(content.contents):
                        wrapper.append(child.extract())
                    content.append(wrapper)
            for child in list(content.contents):
                replacements.append(child.extract())

        for node in replacements:
            table.insert_before(node)
        table.decompose()


# --------------------------------------------------------------------------
# Koppen promoveren
# --------------------------------------------------------------------------

# Structuurwoorden in de grote EU-talen. Alleen op volledig geankerde,
# alleenstaande regels, zodat lopende tekst nooit per ongeluk een kop wordt.
_H2_WORDS = (
    r"HOOFDSTUK|TITEL|AFDELING|ONDERAFDELING|BIJLAGE|"
    r"CHAPTER|TITLE|SECTION|SUBSECTION|ANNEX|"
    r"CHAPITRE|TITRE|ANNEXE|"
    r"KAPITEL|ABSCHNITT|UNTERABSCHNITT|ANHANG|"
    r"CAP[IÍ]TULO|T[IÍ]TULO|SECCI[OÓ]N|ANEXO|"
    r"CAPO|TITOLO|SEZIONE|ALLEGATO|"
    r"ROZDZIA[ŁL]|TYTU[ŁL]|ZA[ŁL][AĄ]CZNIK"
)
_ART_WORDS = r"Artikel|Article|Articolo|Art[ií]culo|Artigo|Artyku[łl]"

_H2_RE = re.compile(rf"^(?:{_H2_WORDS})(?:\s+[0-9IVXLCDM]+[A-Za-z]*)?$", re.I)
_ART_RE = re.compile(
    rf"^(?:{_ART_WORDS})\s+\d+[a-z]*(?:\s+(?:bis|ter|quater|quinquies|sexies))?$",
    re.I,
)


def promote_headings(markdown: str) -> str:
    """"Artikel 5" / "HOOFDSTUK I" op een eigen regel worden ### / ##.

    Genummerde overwegingen en randnummers blijven bewust alinea's.
    """
    out = []
    for line in markdown.split("\n"):
        s = line.strip()
        if s and not s.startswith(("#", "|", "-", ">")):
            if _H2_RE.match(s):
                out.append(f"## {s}")
                continue
            if _ART_RE.match(s):
                out.append(f"### {s}")
                continue
        out.append(line)
    return "\n".join(out)
