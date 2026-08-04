"""Belgische rechtspraak via Juportal.

In tegenstelling tot Duitsland is dit een **stateloze, directe** route: geen
zoek-sessie nodig, geen tokens — gewoon één GET op de ECLI zelf.

  GET https://juportal.be/content/{ECLI}

Een geldige ECLI geeft HTTP 200 met een statische HTML-pagina (geen
JavaScript-rendering nodig); een onbekende geeft **HTTP 400** met een korte
foutpagina. Is een uitspraak later gerectificeerd, dan toont Juportal
gewoon 200 met de **vervangende** tekst (het veld "Vervangt nummer:" in de
metadatatabel verwijst dan naar de oorspronkelijke ECLI) — geen HTTP-redirect,
dus de canonieke ECLI in de bronvermelding komt uit die metadatatabel, niet
uit de aangevraagde URL.

De pagina bestaat uit `<fieldset>`-blokken; de volledige uitspraaktekst staat
in het blok met `<legend>Tekst van de beslissing</legend>`, als één doorlopend
`<p>` met `<br>`-regeleinden — geen aparte structuurelementen. Secties zoals
"I. RECHTSPLEGING VOOR HET HOF" staan op hun eigen regel met een Romeins
cijfer; genummerde overwegingen ("1.", "2.", …) blijven bewust gewone
alinea's (net als bij de andere bronnen in dit project).
"""

from __future__ import annotations

import re

from .. import net
from ..errors import ConversionError
from ..render import collapse_ws, tidy

ECLI_RE = re.compile(r"ECLI:BE:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+", re.I)

_TIMEOUT = 30
_MIN_USEFUL_LENGTH = 40

# Een Romeins-cijfer sectiekop op eigen regel, bv. "I.\tRECHTSPLEGING VOOR HET HOF".
# Alleen VOLLEDIG geankerd en in hoofdletters, anders blijft het een gewone regel —
# net als bij de HOOFDSTUK/Artikel-herkenning in eurlex.py.
_HEADING_RE = re.compile(r"^([IVXLCDM]+)\.\s*([A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý0-9\s,'\-]{3,})$")


def fetch(query: str) -> tuple[str, str]:
    """Haal een Belgische uitspraak op; geeft (markdown, bronvermelding)."""
    m = ECLI_RE.search(query)
    if not m:
        raise ConversionError("Geen geldig Belgisch ECLI-nummer herkend (bv. ECLI:BE:CASS:2021:ARR.20211019.2N.25).")
    requested_ecli = m.group(0).upper()

    r = net.documents().get(f"https://juportal.be/content/{requested_ecli}", timeout=_TIMEOUT)
    if r.status_code == 400:
        raise ConversionError(f"Geen uitspraak gevonden voor {requested_ecli} op Juportal.")
    if r.status_code != 200:
        raise ConversionError(f"Kon uitspraak {requested_ecli} niet ophalen (status {r.status_code}).")

    markdown, canonical_ecli = _html_to_markdown(net.decoded_text(r))
    if len(markdown.strip()) < _MIN_USEFUL_LENGTH:
        raise ConversionError(f"Uitspraak {requested_ecli} bevat geen leesbare tekst.")

    ecli = canonical_ecli or requested_ecli
    label = f"Juportal • {ecli}"
    if canonical_ecli and canonical_ecli != requested_ecli:
        label += f" (vervangt {requested_ecli})"
    return markdown, label


def _html_to_markdown(html: str) -> tuple[str, str | None]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    canonical_ecli = None
    ecli_label = soup.find("p", string=re.compile(r"^\s*ECLI nr:\s*$"))
    if ecli_label is not None:
        value_cell = ecli_label.find_parent("td").find_next_sibling("td")
        if value_cell is not None:
            canonical_ecli = collapse_ws(value_cell.get_text())

    body_fieldset = None
    for fieldset in soup.find_all("fieldset"):
        legend = fieldset.find("legend")
        if legend is not None and "Tekst van de beslissing" in legend.get_text():
            body_fieldset = fieldset
            break
    if body_fieldset is None:
        return "", canonical_ecli

    # De uitspraaktekst zelf zit in een <p> ín het div — niet de div zelf: die
    # bevat bij sommige documenten ook een losse, foutief gelekte server-regel
    # ("ERROR JUPORTARobotRecordLienECLI WARNING …") als tekstnode vóór de <p>.
    outer = body_fieldset.find("div") or body_fieldset
    container = outer.find("p") or outer
    # <br> is het enige regeleinde-signaal in deze platte tekst; zonder deze
    # vervanging plakt get_text() alle regels aan elkaar.
    for br in container.find_all("br"):
        br.replace_with("\n")

    heading_legend = None
    legend = body_fieldset.find("legend")
    if legend is not None and legend.get("title"):
        heading_legend = collapse_ws(legend["title"])

    lines = [collapse_ws(ln) for ln in container.get_text().split("\n")]
    blocks: list[str] = [f"# {heading_legend}"] if heading_legend else []
    for line in lines:
        if not line:
            continue
        m = _HEADING_RE.match(line)
        blocks.append(f"## {line}" if m else line)

    return tidy("\n\n".join(blocks)), canonical_ecli
