"""Convert Dutch legislation from wetten.overheid.nl to Markdown.

Input can be a full wetten.overheid.nl URL or a bare BWB identifier
(optionally with a version date), e.g.:
  https://wetten.overheid.nl/BWBR0040940/2021-07-01
  BWBR0040940
  BWBR0040940/2021-07-01

The portal page is server-rendered and contains the full text in a
`#regeling` container with proper heading tags (h1 title, h3 chapters,
h4 articles), so it converts cleanly with markdownify.
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# BWB identifiers: BWBR (regelingen), BWBV (verdragen), BWBW, BWBS, …
_BWB_RE = re.compile(r"BWB[A-Z]\d+", re.I)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Portal-melding die per artikel wordt herhaald; geen onderdeel van de wettekst.
_NOTICE_RE = re.compile(r"Wijziging\(en\) zonder datum inwerkingtreding aanwezig", re.I)


def is_wetten(query: str) -> bool:
    low = query.lower()
    return "wetten.overheid.nl" in low or bool(_BWB_RE.search(query))


def fetch_wetten(query: str) -> tuple[str, str]:
    """Fetch and convert a Dutch regulation. Returns (markdown, source_note)."""
    m = _BWB_RE.search(query)
    if not m:
        raise ValueError(
            "Geen geldig BWB-nummer of wetten.overheid.nl-link herkend "
            "(bv. BWBR0040940 of https://wetten.overheid.nl/BWBR0040940/2021-07-01)."
        )
    bwb = m.group(0).upper()
    date = _DATE_RE.search(query)
    path = bwb + (f"/{date.group(0)}" if date else "")
    url = f"https://wetten.overheid.nl/{path}"

    r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    if r.status_code != 200 or not r.text.strip():
        raise ValueError(f"Kon regeling {bwb} niet ophalen (status {r.status_code}).")
    r.encoding = r.apparent_encoding or "utf-8"

    markdown = _wetten_html_to_md(r.text)
    if len(markdown.strip()) < 40:
        raise ValueError(f"Geen leesbare wettekst gevonden voor {bwb}.")
    label = f"wetten.overheid.nl • {path}"
    return markdown, label


def _wetten_html_to_md(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    container = soup.select_one("#regeling") or soup.select_one("#content") or soup.body
    if container is None:
        return ""

    # Strip scripts/styles, the portal's tool/nav noise, and the top status-notice
    # block ("Geraadpleegd op…", "Geldend van…") — portal-meldingen, geen wettekst.
    for tag in container(["script", "style", "noscript"]):
        tag.decompose()
    for el in container.select(
        '[class*="action--"], .visually-hidden, .regeling-toestand-meldingen'
    ):
        el.decompose()

    # Verwijder de per-artikel melding "[Wijziging(en) zonder datum
    # inwerkingtreding aanwezig. Zie het wijzigingenoverzicht.]". Die staat als
    # los tekst-blokje zonder eigen class; matchen op tekst, met een lengtegrens
    # zodat alleen het melding-element sneuvelt en niet een heel artikel.
    for el in container.find_all(["p", "div", "span"]):
        txt = el.get_text(" ", strip=True)
        if txt and _NOTICE_RE.search(txt) and len(txt) < 140:
            el.decompose()

    markdown = md(str(container), heading_style="ATX", strip=["a"], bullets="-")
    return _tidy(markdown)


def _tidy(md_text: str) -> str:
    lines = md_text.split("\n")
    out: list[str] = []
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
