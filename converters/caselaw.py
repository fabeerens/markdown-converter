"""Convert case law from a link: Dutch Rechtspraak (ECLI) and ECtHR (HUDOC).

A single entry point, `convert_link`, detects the source from the given URL or
identifier and routes to the right fetcher:

- **Rechtspraak.nl** — via the official Open Data API
  (`https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>`), which returns a
  clean structured XML document.
- **HUDOC (ECtHR)** — via the document HTML export
  (`https://hudoc.echr.coe.int/app/conversion/docx/html/body?...`).
- Anything else falls back to the EUR-Lex fetcher.
"""

from __future__ import annotations

import re

import requests
from lxml import etree

from .eurlex import fetch_and_convert, html_to_markdown
from .wetten import fetch_wetten, is_wetten

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_ECLI_RE = re.compile(r"ECLI:[A-Z]{2}:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+", re.I)
# An ECtHR ECLI, e.g. ECLI:CE:ECHR:2021:0525JUD005817013 (Council of Europe / ECHR).
_ECHR_ECLI_RE = re.compile(r"ECLI:CE:ECHR:\d{4}:[A-Za-z0-9]+", re.I)
# HUDOC item ids look like 001-210077. The id is often embedded in an encoded
# URL fragment (…%22001-210077%22…), so no word boundaries are required.
_HUDOC_ID_RE = re.compile(r"(00\d-\d{3,})")

# Map the UI language choice to HUDOC's 3-letter language codes.
_LANG_ISO3 = {
    "NL": "DUT", "EN": "ENG", "FR": "FRE", "DE": "GER",
    "ES": "SPA", "IT": "ITA", "PT": "POR", "PL": "POL",
}


def detect_source(query: str) -> str | None:
    """Return 'rechtspraak', 'hudoc', 'wetten', or None (→ EUR-Lex)."""
    q = query.strip()
    low = q.lower()
    if _ECHR_ECLI_RE.search(q) or "hudoc.echr.coe.int" in low:
        return "hudoc"
    if is_wetten(q):
        return "wetten"
    if _HUDOC_ID_RE.search(q) and "rechtspraak" not in low and not _ECLI_RE.search(q):
        # A bare HUDOC item id like 001-210077.
        return "hudoc"
    # Only Dutch ECLIs belong to Rechtspraak.nl. EU ECLIs (ECLI:EU:…) are
    # handled by EUR-Lex; other ECLIs fall through as well.
    if "rechtspraak.nl" in low or re.search(r"ECLI:NL:", q, re.I):
        return "rechtspraak"
    return None


def convert_link(query: str, lang: str = "NL") -> tuple[str, str]:
    """Resolve any supported link/identifier to (markdown, source_note)."""
    source = detect_source(query)
    if source == "rechtspraak":
        return _fetch_rechtspraak(query)
    if source == "hudoc":
        return _fetch_hudoc(query, lang)
    if source == "wetten":
        return fetch_wetten(query)
    return fetch_and_convert(query, lang)


# --------------------------------------------------------------------------
# Rechtspraak.nl
# --------------------------------------------------------------------------

def _fetch_rechtspraak(query: str) -> tuple[str, str]:
    m = _ECLI_RE.search(query)
    if not m:
        raise ValueError("Geen geldig ECLI-nummer herkend (bv. ECLI:NL:HR:2012:BQ9251).")
    ecli = m.group(0).upper()
    url = f"https://data.rechtspraak.nl/uitspraken/content?id={ecli}"
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    if r.status_code != 200 or not r.content.strip():
        raise ValueError(f"Kon uitspraak {ecli} niet ophalen (status {r.status_code}).")
    markdown = _rechtspraak_xml_to_md(r.content, ecli)
    if len(markdown.strip()) < 40:
        raise ValueError(
            f"Uitspraak {ecli} bevat geen (open) tekst. Mogelijk is alleen metadata beschikbaar."
        )
    return markdown, f"Rechtspraak.nl • {ecli}"


def _ln(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def _norm(s: str) -> str:
    return " ".join(s.split())


def _rechtspraak_xml_to_md(xml_bytes: bytes, ecli: str) -> str:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(xml_bytes, parser=parser)
    blocks: list[str] = []

    # Header from Dublin Core metadata, if present.
    title = None
    for el in root.iter():
        if _ln(el.tag) == "title" and el.getparent() is not None \
                and _ln(el.getparent().tag) == "Description":
            title = _norm("".join(el.itertext()))
            break
    blocks.append(f"# {title}" if title else f"# {ecli}")

    body = next((el for el in root.iter() if _ln(el.tag) in ("uitspraak", "conclusie")), None)
    if body is not None:
        pending = ""

        def walk(el, depth: int):
            nonlocal pending
            for child in el:
                tag = _ln(child.tag)
                if tag == "title":
                    txt = _norm("".join(child.itertext()))
                    if txt:
                        blocks.append(f"{'#' * min(depth + 2, 6)} {txt}")
                elif tag == "nr":
                    pending = _norm("".join(child.itertext()))
                elif tag == "para":
                    txt = _norm("".join(child.itertext()))
                    if pending:
                        txt = f"{pending} {txt}".strip()
                        pending = ""
                    if txt:
                        blocks.append(txt)
                elif tag in ("section", "parablock", "list", "item", "listitem",
                             "uitspraak.info", "footnote"):
                    walk(child, depth + (1 if tag == "section" else 0))
                elif len(child):
                    walk(child, depth)
                else:
                    txt = _norm("".join(child.itertext()))
                    if txt:
                        blocks.append(txt)

        walk(body, 0)

    return "\n\n".join(b for b in blocks if b.strip()).strip() + "\n"


# --------------------------------------------------------------------------
# HUDOC (ECtHR)
# --------------------------------------------------------------------------

def _hudoc_query(query: str, select: str, length: int = 30) -> list[dict]:
    """Run a HUDOC search query and return the per-result `columns` dicts."""
    params = {
        "query": query,
        "select": select,
        "sort": "",
        "start": "0",
        "length": str(length),
        "rankingmodelid": "11111_Ranking",
        "facetquery": "",
    }
    r = requests.get(
        "https://hudoc.echr.coe.int/app/query/results",
        params=params, headers={"User-Agent": _UA}, timeout=30,
    )
    if r.status_code != 200:
        return []
    return [it.get("columns", {}) for it in r.json().get("results", [])]


def _hudoc_candidates_from_ecli(ecli: str, lang: str) -> list[str]:
    """Ordered HUDOC item ids for an ECtHR ECLI, best language first.

    One ECLI can map to several documents (English/French originals plus
    translations). Order: requested language → English → French → the rest.
    Many translations exist only as PDF (no HTML body), so the caller tries
    each id in turn and uses the first that actually has an HTML rendition.
    """
    cols = _hudoc_query(f'ecli:"{ecli}"', "itemid,ecli,languageisocode", length=30)
    matches = [c for c in cols
               if c.get("itemid") and (c.get("ecli") or "").upper() == ecli.upper()]

    ordered: list[str] = []

    def add(code: str):
        for c in matches:
            if (c.get("languageisocode") or "").upper() == code and c["itemid"] not in ordered:
                ordered.append(c["itemid"])

    for code in (_LANG_ISO3.get(lang.upper()), "ENG", "FRE"):
        if code:
            add(code)
    for c in matches:  # any remaining language versions as a last resort
        if c["itemid"] not in ordered:
            ordered.append(c["itemid"])
    return ordered


def _fetch_hudoc_body(item_id: str) -> str | None:
    """Fetch a HUDOC document's HTML body, or None if it has no HTML rendition."""
    url = (
        "https://hudoc.echr.coe.int/app/conversion/docx/html/body"
        f"?library=ECHR&id={item_id}"
    )
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=45)
    if r.status_code != 200 or not r.text.strip():
        return None
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _fetch_hudoc(query: str, lang: str = "EN") -> tuple[str, str]:
    ecli_m = _ECHR_ECLI_RE.search(query)
    if ecli_m:
        ecli = ecli_m.group(0).upper()
        candidates = _hudoc_candidates_from_ecli(ecli, lang)
        if not candidates:
            raise ValueError(f"Geen HUDOC-document gevonden voor {ecli}.")
        for item_id in candidates:
            html = _fetch_hudoc_body(item_id)
            if html:
                markdown = html_to_markdown(html)
                if len(markdown.strip()) >= 40:
                    return markdown, f"HUDOC (EHRM) • {item_id} • {ecli}"
        raise ValueError(
            f"Voor {ecli} is geen tekstversie (HTML) beschikbaar op HUDOC — "
            f"mogelijk alleen als PDF."
        )

    m = _HUDOC_ID_RE.search(query)
    if not m:
        raise ValueError(
            "Geen geldig HUDOC item-id of EHRM-ECLI herkend "
            "(bv. 001-210077, ECLI:CE:ECHR:…, of plak de volledige HUDOC-link)."
        )
    item_id = m.group(1)
    html = _fetch_hudoc_body(item_id)
    if not html:
        raise ValueError(f"Kon HUDOC-document {item_id} niet ophalen (geen HTML-versie).")
    markdown = html_to_markdown(html)
    if len(markdown.strip()) < 40:
        raise ValueError(f"HUDOC-document {item_id} bevat geen leesbare tekst.")
    return markdown, f"HUDOC (EHRM) • {item_id}"
