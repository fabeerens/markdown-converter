"""Fetch documents from EUR-Lex by CELEX number or URL and convert to Markdown.

Two acquisition strategies are attempted, in order:

1. The official document content from the Publications Office **Cellar**
   repository via content negotiation (``application/xhtml+xml``). This is
   the reliable programmatic endpoint and honours ``Accept-Language``.
2. The rendered HTML page from the EUR-Lex portal (with retries, since that
   endpoint returns HTTP 202 while it prepares the page).
"""

from __future__ import annotations

import re
import time
import warnings
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString
from bs4 import XMLParsedAsHTMLWarning
from markdownify import markdownify as md

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# A CELEX looks like: sector digit + year + document-type letter(s) + number.
# e.g. 32016R0679, 32011L0083, 62019CJ0311, 52021PC0206
_CELEX_RE = re.compile(r"^[0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?$", re.I)


def extract_celex(text: str) -> str | None:
    """Pull a CELEX identifier out of a raw string or an EUR-Lex URL."""
    text = text.strip()
    if not text:
        return None

    # Bare CELEX number.
    if _CELEX_RE.match(text):
        return text.upper()

    # From a URL: look for CELEX in the `uri` query param or the path.
    parsed = urlparse(text)
    if parsed.query:
        qs = parse_qs(parsed.query)
        for key in ("uri", "CELEX", "celex"):
            if key in qs:
                val = unquote(qs[key][0])
                m = re.search(r"CELEX[:%]*3?([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4})", val, re.I)
                if m:
                    return _clean(val)
                if _CELEX_RE.match(val):
                    return val.upper()

    m = re.search(r"CELEX[:/]([0-9A-Z()]+)", text, re.I)
    if m and _CELEX_RE.match(m.group(1)):
        return m.group(1).upper()

    m = re.search(r"/([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4})", text, re.I)
    if m:
        return m.group(1).upper()

    return None


def _clean(uri_value: str) -> str:
    m = re.search(r"([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:\([0-9]+\))?)", uri_value, re.I)
    return m.group(1).upper() if m else uri_value.upper()


# ELI (European Legislation Identifier) URLs, e.g.
#   https://eur-lex.europa.eu/eli/reg/2016/679/oj?locale=NL
# The ELI document-type maps to the CELEX descriptor letter; the rest of the
# CELEX (sector 3, year, 4-digit number) is deterministic for legislative acts.
_ELI_RE = re.compile(r"/eli/([a-z_]+)/(\d{4})/(\d+)", re.I)
_ELI_TYPE = {
    "reg": "R", "reg_impl": "R", "reg_del": "R",
    "dir": "L", "dir_impl": "L", "dir_del": "L",
    "dec": "D", "dec_impl": "D", "dec_del": "D",
    "reco": "H", "recommendation": "H",
}


def eli_to_celex(text: str) -> str | None:
    """Derive a CELEX number from an ELI URL/identifier, or None."""
    m = _ELI_RE.search(text)
    if not m:
        return None
    typ, year, num = m.group(1).lower(), m.group(2), m.group(3)
    letter = _ELI_TYPE.get(typ)
    if not letter:
        return None
    return f"3{year}{letter}{int(num):04d}"


# An EU ECLI (Court of Justice / General Court), e.g. ECLI:EU:C:2025:645.
_EU_ECLI_RE = re.compile(r"ECLI:EU:[A-Z]{1,2}:\d{4}:\d+", re.I)


def fetch_and_convert(text: str, lang: str = "NL") -> tuple[str, str]:
    """Resolve the input to a document and return (markdown, source_note)."""
    lang = (lang or "NL").upper()

    # EU case law addressed by ECLI: resolve it through the Cellar ECLI endpoint.
    ecli_m = _EU_ECLI_RE.search(text)
    if ecli_m:
        ecli = ecli_m.group(0).upper()
        html = _fetch_cellar_ecli(ecli, lang)
        if html:
            markdown = html_to_markdown(html)
            if len(markdown.strip()) > 80:
                return markdown, f"EUR-Lex (Cellar) • {ecli} • {lang}"
        raise ValueError(
            f"Kon {ecli} niet ophalen bij EUR-Lex (mogelijk niet beschikbaar in taal {lang})."
        )

    # CELEX from the input, or derived from an ELI URL (e.g. /eli/reg/2016/679/oj).
    celex = extract_celex(text) or eli_to_celex(text)
    if not celex:
        raise ValueError(
            "Geen geldig CELEX-nummer of EUR-Lex URL herkend. "
            "Voorbeeld: 32016R0679 of https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32016R0679"
        )

    # Strategy 1: official content from the Cellar repository (reliable).
    try:
        html = _fetch_cellar(celex, lang)
        if html:
            markdown = html_to_markdown(html)
            if len(markdown.strip()) > 80:
                return markdown, f"EUR-Lex (Cellar) • CELEX:{celex} • {lang}"
    except ValueError:
        raise
    except Exception:
        pass

    # Strategy 2: the EUR-Lex portal HTML endpoint (retries on HTTP 202).
    html = _fetch_portal_html(celex, lang)
    markdown = html_to_markdown(html)
    return markdown, f"EUR-Lex portal • CELEX:{celex} • {lang}"


def _fetch_cellar(celex: str, lang: str) -> str | None:
    """Fetch the document content from Cellar via content negotiation."""
    url = f"http://publications.europa.eu/resource/celex/{celex}"
    headers = {
        "Accept": "application/xhtml+xml",
        "Accept-Language": lang.lower(),
        "User-Agent": _UA,
    }
    r = requests.get(url, headers=headers, timeout=45, allow_redirects=True)
    if r.status_code == 300:
        # Multiple choices returned without a language match.
        raise ValueError(
            f"Document niet beschikbaar in taal {lang} (CELEX:{celex}). Probeer een andere taal."
        )
    if r.status_code != 200:
        return None
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _fetch_cellar_ecli(ecli: str, lang: str) -> str | None:
    """Fetch EU case-law content from Cellar addressed by ECLI."""
    from urllib.parse import quote
    url = f"http://publications.europa.eu/resource/ecli/{quote(ecli, safe='')}"
    headers = {
        "Accept": "application/xhtml+xml",
        "Accept-Language": lang.lower(),
        "User-Agent": _UA,
    }
    r = requests.get(url, headers=headers, timeout=45, allow_redirects=True)
    if r.status_code != 200:
        return None
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _fetch_portal_html(celex: str, lang: str) -> str:
    url = f"https://eur-lex.europa.eu/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"
    session = requests.Session()
    session.headers["User-Agent"] = _UA
    r = None
    for _ in range(6):
        r = session.get(url, timeout=30)
        if r.status_code == 200 and r.text.strip():
            break
        if r.status_code == 202:  # EUR-Lex is still rendering the page
            time.sleep(2)
            continue
        break
    if r is None or r.status_code != 200 or not r.text.strip():
        code = r.status_code if r is not None else "?"
        raise ValueError(
            f"Kon het document niet ophalen van EUR-Lex (status {code}) voor CELEX:{celex} "
            f"in taal {lang}. Controleer het nummer/de taal, of download de Formex-XML en "
            f"upload die via het andere tabblad."
        )
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def html_to_markdown(html: str) -> str:
    """Clean an EUR-Lex HTML page and convert its body to Markdown."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "head", "meta", "link"]):
        tag.decompose()

    # EUR-Lex lays out numbered paragraphs (recitals, judgment points, lettered
    # lists) as two-column "marker | text" tables. Turn those into real list
    # items / paragraphs before conversion, so they don't become ugly tables.
    _unwrap_marker_tables(soup)

    # EUR-Lex wraps the act text; prefer the main text container when present.
    container = (
        soup.find(id="text")
        or soup.find(id="TexteOnly")
        or soup.find("div", class_="tabContent")
        or soup.body
        or soup
    )

    markdown = md(
        str(container),
        heading_style="ATX",
        strip=["a"],  # drop portal links; keep readable text
        bullets="-",
    )
    return _tidy(_promote_headings(markdown))


# A short "marker" cell: a number, letter or roman numeral, optionally wrapped
# in parentheses / followed by a dot or bracket. e.g. 1  1.  1)  (1)  a)  ii)
_MARKER_RE = re.compile(
    r"^\(?(?:\d{1,4}|[ivxlcdm]{1,6}|[a-z])\)?[.)]?$", re.I
)
_BULLET_CHARS = {"–", "—", "-", "‑", "•", "·", "*"}
_BLOCK_TAGS = {"p", "div", "li", "blockquote", "table", "ul", "ol"}


def _is_marker(text: str) -> bool:
    text = text.strip()
    if len(text) > 6:
        return False
    return text == "" or text in _BULLET_CHARS or bool(_MARKER_RE.match(text))


def _own_rows(table):
    """Rows belonging directly to this table (not to nested tables)."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _marker_prefix(marker: str) -> str:
    marker = marker.strip()
    if re.fullmatch(r"\d{1,4}", marker):
        return f"{marker}. "        # pure number → ordered-list item, number kept
    if marker in _BULLET_CHARS:
        return "- "                 # dash / bullet → bullet-list item
    if marker:
        return f"{marker} "         # keep legal marker verbatim, e.g. "a)" "(1)"
    return ""


def _unwrap_marker_tables(soup):
    # Handle innermost tables first so nested layout tables convert correctly.
    for table in reversed(soup.find_all("table")):
        rows = _own_rows(table)
        if not rows:
            continue
        cell_sets = [tr.find_all(["td", "th"], recursive=False) for tr in rows]
        if any(len(cells) != 2 for cells in cell_sets):
            continue
        markers = [cells[0].get_text(" ", strip=True) for cells in cell_sets]
        # Every first cell must look like a marker, and not all of them empty.
        if not all(_is_marker(m) for m in markers) or not any(m for m in markers):
            continue

        replacements = []
        for cells, marker in zip(cell_sets, markers):
            content = cells[1]
            prefix = _marker_prefix(marker)
            if prefix:
                # Prepend the marker inside the cell's first block element
                # (e.g. <p>), so it stays on the same line as the text.
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
            # Move the cell's content out to replace the table.
            for child in list(content.contents):
                replacements.append(child.extract())

        for node in replacements:
            table.insert_before(node)
        table.decompose()


# Structural keywords in the major EU languages. Matched only on standalone,
# fully-anchored lines so running text is never mis-promoted.
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


def _promote_headings(markdown: str) -> str:
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


def _tidy(md_text: str) -> str:
    lines = md_text.split("\n")
    out = []
    blank = 0
    for line in lines:
        if line.strip() == "":
            blank += 1
            if blank <= 2:
                out.append("")
        else:
            blank = 0
            out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"
