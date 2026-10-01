"""Wetgevingskalender (wetgevingskalender.overheid.nl): wetgevingstrajecten en hun mijlpalen.

De wetgevingskalender volgt een wet of AMvB van voorbereiding tot bekendmaking. Server-gerenderde
site met een XML-versie per regeling, die hier de bron is:

  /Regeling/{WGKnummer}                      de regeling (HTML)
  /Regeling/{WGKnummer}/xml                  dezelfde als `regelgevingFiche`-XML (metadata, fasen,
                                             mijlpalen, documenten met download-url)
  /Regeling/{WGKnummer}/Download/{guid}.pdf  een document
  /Regeling/ZoekResultaten?Zinsdeel=…&Status=…&Fase=…&RegelgevingType=…&Type=Regeling
        &Pagina=n&Paginagrootte=10|25|100    zoeken (bij één treffer volgt een redirect)
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup
from lxml import etree

from .. import net
from ..errors import ConversionError, UpstreamError
from ..render import tidy
from . import consultatie
from .common import Fetched, bijlage, header, nl_date, slug as make_slug

BASE = "https://wetgevingskalender.overheid.nl"
_TIMEOUT = 45
_ID = re.compile(r"\bWGK\d{3,}\b", re.I)
PAGE_SIZE = 25

STATUSSEN = (("", "Alle statussen"), ("inwording", "In wording"), ("naderend", "Naderend"),
             ("beeindigd", "Beëindigd"))
FASEN = ("Voorbereiding", "Raad van State", "Tweede Kamer", "Eerste Kamer", "Bekendmaking")
TYPEN = ("Wet", "Amvb")


def matches(query: str) -> bool:
    low = query.lower()
    return "wetgevingskalender.overheid.nl" in low or bool(_ID.search(query))


def _get(url: str, **kw):
    try:
        return net.documents().get(url, timeout=_TIMEOUT, **kw)
    except Exception as e:
        raise UpstreamError(f"De wetgevingskalender is niet bereikbaar ({type(e).__name__}).") from e


# --------------------------------------------------------------------------
# Ophalen
# --------------------------------------------------------------------------

def fetch(query: str) -> Fetched:
    q = query.strip()
    if "/Download/" in q and "wetgevingskalender.overheid.nl" in q.lower():
        wid = _ID.search(q)
        back = f"{BASE}/Regeling/{wid.group(0).upper()}" if wid else None
        got = consultatie.fetch_file(q, "Wetgevingskalender", back, wid.group(0).upper() if wid else None)
        title = _document_title(wid.group(0).upper(), q) if wid else None
        if title:      # de titel uit de fiche is beter dan de GUID-bestandsnaam
            got.markdown = re.sub(r"^# .*", f"# {title}", got.markdown, count=1)
            got.name = make_slug(title, 60)
        return got
    m = _ID.search(q)
    if not m:
        raise ConversionError(
            "Geen wetgevingskalender-regeling herkend. Geef een WGK-nummer (WGK027200) of een link "
            "naar wetgevingskalender.overheid.nl/Regeling/…."
        )
    wid = m.group(0).upper()
    r = _get(f"{BASE}/Regeling/{wid}/xml")
    if r.status_code == 404 or b"regelgevingFiche" not in r.content[:3000]:
        raise ConversionError(f"Regeling {wid} niet gevonden in de wetgevingskalender.")
    if r.status_code != 200:
        raise UpstreamError(f"De wetgevingskalender gaf een fout (status {r.status_code}).")
    return xml_to_fetched(r.content, wid)


def _document_title(wid: str, url: str) -> str | None:
    """Titel (en type) van een document uit de fiche van de regeling; best-effort."""
    try:
        r = _get(f"{BASE}/Regeling/{wid}/xml")
        root = etree.fromstring(r.content, etree.XMLParser(resolve_entities=False, no_network=True))
        for d in root.iter("document"):
            if (d.findtext("url") or "").strip() == url.strip():
                titel, typ = (d.findtext("titel") or "").strip(), (d.findtext("type") or "").strip()
                return f"{titel} ({typ})" if titel and typ else titel or None
    except Exception:
        return None
    return None


def _t(el, path: str, ns: dict) -> str:
    found = el.find(path, ns)
    return re.sub(r"\s+", " ", (found.text or "")).strip() if found is not None else ""


def xml_to_fetched(xml: bytes, wid: str) -> Fetched:
    ns = {"d": "http://purl.org/dc/terms/", "w": "http://standaarden.overheid.nl/wk/terms/"}
    try:
        root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise ConversionError(f"De XML van {wid} is niet leesbaar: {e}") from e
    meta = root.find("meta")
    kern, mantel, ipm = (meta.find(x) for x in ("owmskern", "owmsmantel", "regelgevingipm"))
    title = _t(kern, "d:title", ns) or wid
    bool_nl = {"true": "Ja", "false": "Nee"}
    status, fase = _t(ipm, "w:status", ns), _t(ipm, "w:actieveFase", ns)
    consult = mantel.find("d:isPartOf", ns) if mantel is not None else None

    head = header(title, [
        ("Publicatie", f"Wetgevingskalender (`{wid}`)"),
        ("Soort", _t(ipm, "w:type", ns)),
        ("Status", f"{status} — huidige fase: {fase}" if status and fase else status or fase),
        ("Ministerie", _t(ipm, "w:departementEersteOndertekenaar", ns)),
        ("Eerste ondertekenaar", _t(ipm, "w:eersteOndertekenaar", ns)),
        ("Onderwerp", _t(mantel, "d:subject", ns)),
        ("Inwerkingtreding per KB", bool_nl.get(_t(ipm, "w:inwerkingtredingPerKB", ns))),
        ("Laatst gewijzigd", nl_date(_t(kern, "d:modified", ns))),
        ("Bron", f"<{BASE}/Regeling/{wid}>"),
    ])
    parts = [head]
    samenvatting = (root.findtext("body/regeling/samenvatting") or root.findtext("body/regelgeving/samenvatting") or "")
    if samenvatting.strip():
        parts.append("> " + re.sub(r"\s+", " ", samenvatting).strip())

    items: list[dict] = []
    parts.append("## Voortgang")
    for fase_el in root.iter("fase"):
        lines = [f"### {fase_el.get('faseTitel', 'Fase')}"]
        for m in fase_el.iter("mijlpaal"):
            datum = nl_date(_t(m, "mijlpaalDatum", {})) or ""
            titel = _t(m, "mijlpaalTitel", {})
            ref = m.find("mijlpaalDocument")
            line = f"- **{datum}** — {titel}" if datum else f"- {titel}"
            if ref is not None and ref.get("resourceIdentifier"):
                line += f" · [{(ref.text or 'verwijzing').strip()}]({ref.get('resourceIdentifier')})"
            lines.append(line)
            for d in m.iter("document"):
                url = _t(d, "url", {})
                dtitel = _t(d, "titel", {}) or _t(d, "filename", {})
                dtype = _t(d, "type", {})
                extra = ", ".join(x for x in (f"v{_t(d, 'versie', {})}" if _t(d, "versie", {}) else "",
                                              nl_date(_t(d, "publicatieDatum", {}))) if x)
                lines.append(f"  - [{dtitel}]({url})" + (f" — {dtype}" if dtype else "")
                             + (f" ({extra})" if extra else ""))
                if url:
                    items.append(bijlage(url, dtitel, dtype or "Document", url))
        parts.append("\n".join(lines))
    if consult is not None and consult.get("resourceIdentifier"):
        items.append(bijlage(consult.get("resourceIdentifier").replace("http://", "https://"),
                             "Internetconsultatie", "Consultatie", consult.get("resourceIdentifier")))
    return Fetched(tidy("\n\n".join(parts)), f"Wetgevingskalender • {title} ({wid})", [], items,
                   ident=wid, name=f"WGK-{make_slug(title, 50)}")


# --------------------------------------------------------------------------
# Zoeken
# --------------------------------------------------------------------------

def search(q: str, *, status: str = "", fase: str = "", type_: str = "", page: int = 1,
           size: int = PAGE_SIZE) -> tuple[int, list[dict]]:
    """Zoek regelingen; geeft (totaal, [{id, titel, fase, ministerie}])."""
    params = [("Zinsdeel", quote((q or "").strip(), safe="")), ("Type", "Regeling"),
              ("Pagina", str(page)), ("Paginagrootte", str(size))]
    if status in dict(STATUSSEN) and status:
        params.append(("Status", status))
    if fase in FASEN:
        params.append(("Fase", quote(fase, safe="")))
    if type_ in TYPEN:
        params.append(("RegelgevingType", type_))
    r = _get(f"{BASE}/Regeling/ZoekResultaten?" + "&".join(f"{k}={v}" for k, v in params))
    if r.status_code != 200:
        raise UpstreamError(f"De wetgevingskalender gaf een fout (status {r.status_code}).")
    soup = BeautifulSoup(net.decoded_text(r), "lxml")
    # Eén treffer? Dan stuurt de site meteen door naar die regeling.
    final = urlparse(r.url).path
    m = re.match(r"^/Regeling/(WGK\d+)$", final, re.I)
    if m:
        h = soup.find("h1") or soup.find("h2")
        text = soup.get_text("\n", strip=True)
        mf = re.search(r"Huidige stap:\s*\n?([^\n]+)", text)
        # De paginatitel is "<regeling> | Overheid.nl | Wetgevingskalender".
        title = (soup.title.get_text(strip=True).split("|")[0].strip() if soup.title else "")
        return 1, [{"id": m.group(1).upper(), "titel": title or m.group(1),
                    "fase": mf.group(1).strip() if mf else "", "ministerie": ""}]
    total_m = re.search(r"(\d[\d.]*)\s+resultaten", soup.get_text(" ", strip=True))
    total = int(total_m.group(1).replace(".", "")) if total_m else 0
    out = []
    for a in soup.find_all("a", class_="result--title"):
        idm = re.search(r"WGK\d+", a.get("href", ""), re.I)
        if not idm:
            continue
        meta = [re.sub(r"\s+", " ", x.get_text(" ", strip=True)) for x in
                (a.find_next_sibling("ul").find_all("li") if a.find_next_sibling("ul") else [])]
        out.append({
            "id": idm.group(0).upper(), "titel": re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
            "fase": next((x.split(":", 1)[1].strip() for x in meta if x.startswith("Fase:")), ""),
            "ministerie": next((x for x in meta if not x.startswith("Fase:")), ""),
        })
    return total, out
