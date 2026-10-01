"""Internetconsultatie (internetconsultatie.nl): consultaties, hun documenten en reacties.

De site is server-gerenderd HTML zonder API; alles hier is dus scrapen, met de
structuur die de site zelf gebruikt:

  /{slug}                        de consultatie (redirect naar /{slug}/b1)
  /{slug}/document/{id}          een document bij de consultatie (bestand)
  /{slug}/reacties[/datum/{n}]   lijst met openbare reacties
  /{slug}/reactie/{guid}         één reactie (tekstvelden en/of een bijlage)
  /{slug}/reactie/{nr}/bestand   de bijlage van een reactie
  /{nummer}                      kort adres; redirect naar de consultatie
  /zoeken/resultaat?Trefwoorden=…&TrefwoordenSearchScope=TitelEnTekst
        &ConsultatiedatumVan=d-m-jjjj 00:00:00&ConsultatiedatumTotEnMet=…&Pagina=n

Wat er uit komt: de consultatie als Markdown (met metadata, tekst en documenten), met in het
bijlagenpaneel de documenten, de wetgevingskalender-fiche en "alle reacties" — dat laatste is één
Markdown-document met de tekst van elke openbare reactie.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .. import net
from ..errors import ConversionError, UpstreamError
from ..render import container_to_markdown, tidy
from .common import Fetched, MAX_DOWNLOAD_BYTES, bijlage, header, slug as make_slug

BASE = "https://www.internetconsultatie.nl"
_TIMEOUT = 45
PAGE_SIZE = 10               # zoekresultaten per pagina (vast bij de site)
MAX_REACTIES = 400          # per reacties-document; meer wordt gemeld, niet stil weggelaten
_RESERVED = {"zoeken", "consultatie", "home", "content", "koop-cb", "reactie", "reacties"}

_HOST = re.compile(r"^https?://(?:www\.)?internetconsultatie\.nl(?=/|$)", re.I)
_GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def matches(query: str) -> bool:
    return bool(_HOST.match(query.strip()))


def canonical(slug: str) -> str:
    return f"{BASE}/{slug}"


# --------------------------------------------------------------------------
# Adres herkennen
# --------------------------------------------------------------------------

def parse_url(url: str) -> tuple[str, str, str]:
    """→ (soort, slug, extra). soort ∈ consultatie | reacties | reactie | document | bestand | nummer."""
    path = unquote(urlparse(url.strip()).path).strip("/")
    parts = path.split("/") if path else []
    if not parts:
        raise ConversionError("Geef een link naar een consultatie op internetconsultatie.nl.")
    if len(parts) == 1 and parts[0].isdigit():
        return "nummer", "", parts[0]
    slug = parts[0]
    if slug.lower() in _RESERVED:
        raise ConversionError("Dit is geen link naar een consultatie (maar naar een zoekpagina o.i.d.).")
    rest = parts[1:]
    if not rest or re.fullmatch(r"b\d+", rest[0]):
        return "consultatie", slug, ""
    if rest[0] == "reacties":
        return "reacties", slug, ""
    if rest[0] == "reactie" and len(rest) >= 3 and rest[2] == "bestand":
        return "bestand", slug, rest[1]
    if rest[0] == "reactie" and len(rest) >= 2:
        return "reactie", slug, rest[1]
    if rest[0] == "document" and len(rest) >= 2:
        return "document", slug, rest[1]
    return "consultatie", slug, ""


def _get(url: str, **kw):
    try:
        return net.documents().get(url, timeout=_TIMEOUT, **kw)
    except Exception as e:
        raise UpstreamError(f"internetconsultatie.nl is niet bereikbaar ({type(e).__name__}).") from e


def _soup(url: str) -> tuple[BeautifulSoup, str]:
    r = _get(url)
    if r.status_code == 404:
        raise ConversionError("Deze pagina bestaat niet op internetconsultatie.nl.")
    if r.status_code != 200:
        raise UpstreamError(f"internetconsultatie.nl gaf een fout (status {r.status_code}).")
    return BeautifulSoup(net.decoded_text(r), "lxml"), r.url


def _content(soup: BeautifulSoup):
    c = soup.find(id="content") or soup.find("main") or soup.body
    for t in c(["script", "style", "noscript"]):
        t.decompose()
    return c


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


# --------------------------------------------------------------------------
# De consultatie
# --------------------------------------------------------------------------

def _slug_from_final_url(url: str) -> str:
    return unquote(urlparse(url).path).strip("/").split("/")[0]


def fetch_consultatie(slug: str) -> Fetched:
    soup, final = _soup(f"{BASE}/{quote(slug)}")
    slug = _slug_from_final_url(final) or slug
    c = _content(soup)
    title = _text(c.find("h1")) or slug

    # Metadata uit de tabel/lijst "Consultatiegegevens" (label → waarde).
    facts: dict[str, str] = {}
    for row in c.find_all("tr"):
        cells = [_text(x) for x in row.find_all(["th", "td"])]
        if len(cells) == 2 and cells[0]:
            facts[cells[0]] = cells[1]
    for dt in c.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            facts.setdefault(_text(dt), _text(dd))

    # Documenten: "Relevante documenten" → [(titel, absolute url)].
    docs = []
    for a in c.find_all("a", href=re.compile(r"/document/\d+")):
        li = a.find_parent("li")
        label = _text(li.find(class_="list--source__information")) if li else ""
        label = re.sub(r"\s*(PDF|DOCX?|XLSX?|PPTX?)\s*$", "", label).strip() or _text(a)
        docs.append((label, urljoin(final, a["href"])))

    # Reacties: aantal en de link naar de lijst.
    n_reacties = None
    h = next((h for h in c.find_all(["h2", "h3"]) if "Reacties op deze consultatie" in h.get_text()), None)
    if h:
        m = re.search(r"(\d+)", h.get_text(" ", strip=True))
        n_reacties = int(m.group(1)) if m else None
    wgk = next((a["href"] for a in c.find_all("a", href=True)
                if "wetgevingskalender.overheid.nl/Regeling/" in a["href"]), None)

    # Thema's staan tussen de titel en "In het kort" (naast "Consultatie gesloten").
    themas = []
    h1 = c.find("h1")
    first_h2 = c.find("h2")
    if h1 and first_h2:
        for el in h1.find_all_next():
            if el is first_h2:
                break
            if el.name in ("p", "li", "span") and not el.find(["p", "li", "span"]):
                t = _text(el)
                if t and t not in ("Printen",) and not t.startswith("Consultatie "):
                    themas.append(t)
    # Markdown van de rest: alles vanaf "In het kort", zonder de feitentabel (staat al in de kop),
    # de documentenlijst (komt apart) en de reactievoorbeelden.
    for ul in c.find_all("ul", class_=re.compile(r"list--sources")):
        ul.decompose()
    for h in c.find_all("h3"):
        if "Consultatiegegevens" in h.get_text():
            for sib in list(h.find_next_siblings())[:1]:
                if sib.name in ("table", "dl", "div", "ul"):
                    sib.decompose()
            h.decompose()
    cut = next((h for h in c.find_all(["h2", "h3"]) if "Reacties op deze consultatie" in h.get_text()), None)
    if cut:
        for sib in list(cut.find_next_siblings()):
            sib.decompose()
        cut.decompose()
    if first_h2 is not None:
        # Alleen wat vanaf "In het kort" komt; de titel en labels erboven staan al in de kop.
        # Voorouders van die kop blijven staan (anders verdwijnt de inhoud mee).
        ancestors = {id(p) for p in first_h2.parents}
        for el in list(first_h2.find_all_previous()):
            if id(el) not in ancestors and not el.decomposed:
                el.decompose()
    for t in c.find_all("table"):                       # de feitentabel staat al in de kop
        if "Startdatum consultatie" in t.get_text():
            t.decompose()
    for h in c.find_all(["h2", "h3"]):                  # de documenten komen onderaan als linklijst
        if "Relevante documenten" in h.get_text():
            h.decompose()
    body = container_to_markdown(c)
    body = re.sub(r"^# .*\n", "", body.lstrip(), count=1)

    head = header(title, [
        ("Publicatie", f"Internetconsultatie (`{slug}`)"),
        ("Status", facts.get("Status")),
        ("Start", facts.get("Startdatum consultatie")),
        ("Einde", facts.get("Einddatum consultatie")),
        ("Organisatie", facts.get("Organisatie")),
        ("Type", facts.get("Type consultatie")),
        ("Thema", ", ".join(dict.fromkeys(themas)) or facts.get("Onderwerpen")),
        ("Keten-ID", facts.get("Keten-ID")),
        ("Reacties", f"{n_reacties} openbaar" if n_reacties is not None else None),
        ("Bron", f"<{canonical(slug)}>"),
    ])
    parts = [head, body.lstrip()]

    items = [bijlage(u, t, "Document", u) for t, u in docs]
    if n_reacties:
        items.append(bijlage(f"{canonical(slug)}/reacties", f"Alle reacties ({n_reacties})",
                             "Reacties", f"{canonical(slug)}/reacties"))
    if wgk:
        wid = re.search(r"WGK\d+", wgk)
        if wid:
            items.append(bijlage(wid.group(0), f"Wetgevingskalender {wid.group(0)}",
                                 "Wetgevingskalender", wgk))
    return Fetched(
        tidy("\n\n".join(parts)), f"Internetconsultatie • {title} ({slug})", [], items,
        ident=canonical(slug), name=f"Consultatie-{make_slug(title, 50)}",
    )


# --------------------------------------------------------------------------
# Reacties
# --------------------------------------------------------------------------

def _reactie_links(slug: str) -> tuple[list[tuple[str, str, str]], int | None]:
    """Alle openbare reacties: [(guid-url, naam, 'plaats | datum')], plus het totaal."""
    out: list[tuple[str, str, str]] = []
    total = None
    page, last = 1, 1
    while page <= last and len(out) < MAX_REACTIES + 1:
        # `/datum/{pagina}/{aantal}`: de site laat 10, 25 of 100 per pagina toe.
        url = f"{BASE}/{quote(slug)}/reacties/datum/{page}/100"
        soup, _ = _soup(url)
        c = _content(soup)
        if total is None:
            m = re.search(r"In totaal zijn (\d+) reacties", _text(c))
            total = int(m.group(1)) if m else None
        for a in c.find_all("a", href=re.compile(rf"/reactie/{_GUID}$")):
            out.append((urljoin(url, a["href"]), _text(a), _text(a.find_next_sibling()) if a.find_next_sibling() else
                        _text(a.parent).replace(_text(a), "").strip()))
        pages = [int(m.group(1)) for a in c.find_all("a", href=True)
                 if (m := re.search(r"/reacties/datum/(\d+)(?:/\d+)?$", a["href"]))]
        last = max([last, *pages])
        page += 1
    return out, total


_BOILERPLATE_Q = re.compile(r"^wilt u reageren", re.I)


def _parse_reactie(url: str) -> dict:
    soup, _ = _soup(url)
    c = _content(soup)
    lines = [x for x in (re.sub(r"\s+", " ", ln).strip() for ln in c.get_text("\n").split("\n")) if x]
    fields: dict[str, str] = {}
    start = 0
    for label in ("Naam", "Plaats", "Datum"):
        if label in lines:
            idx = lines.index(label)
            if idx + 1 < len(lines):
                fields[label] = lines[idx + 1]
                start = max(start, idx + 2)
    bestand = next((urljoin(url, a["href"]) for a in c.find_all("a", href=re.compile(r"/bestand$"))), None)
    # Wat volgt: eventueel "Bijlage"-labels, en per vraag "VraagN", de vraag zelf en het antwoord.
    texts: list[str] = []
    rest = [ln for ln in lines[start:] if ln not in ("Bijlage", "Reactie")]
    i = 0
    while i < len(rest):
        if re.fullmatch(r"Vraag\s*\d+", rest[i]) and i + 1 < len(rest):
            question = rest[i + 1]
            # De standaardvraag ("Wilt u reageren…") staat bij elke reactie en voegt niets toe.
            if not _BOILERPLATE_Q.match(question):
                texts.append(f"**{question}**")
            i += 2
        else:
            texts.append(rest[i])
            i += 1
    return {"url": url, "fields": fields, "tekst": texts, "bestand": bestand}


def _reactie_markdown(r: dict, level: int = 3) -> str:
    f = r["fields"]
    naam = f.get("Naam") or "Anoniem"
    plaats = f.get("Plaats")
    meta = " · ".join(x for x in (plaats, f.get("Datum")) if x)
    out = [f"{'#' * level} {naam}" + (f" — {meta}" if meta else "")]
    out += r["tekst"]
    if r["bestand"]:
        out.append(f"Bijlage: <{r['bestand']}>")
    return "\n\n".join(out)


def fetch_reacties(slug: str) -> Fetched:
    soup, _ = _soup(f"{BASE}/{quote(slug)}")
    title = _text(_content(soup).find("h1")) or slug
    links, total = _reactie_links(slug)
    shown = links[:MAX_REACTIES]
    with ThreadPoolExecutor(max_workers=12) as pool:
        reacties = list(pool.map(lambda t: _parse_reactie(t[0]), shown))
    parts = [header(f"Reacties op: {title}", [
        ("Publicatie", f"Internetconsultatie (`{slug}`)"),
        ("Openbare reacties", str(total if total is not None else len(links))),
        ("Bron", f"<{canonical(slug)}/reacties>"),
    ])]
    if total and total > len(shown):
        parts.append(f"*Alleen de eerste {len(shown)} van {total} reacties zijn opgenomen.*")
    parts += [_reactie_markdown(r) for r in reacties]
    return Fetched(
        tidy("\n\n".join(parts)), f"Internetconsultatie • reacties op {title} ({slug})", [],
        [bijlage(canonical(slug), title, "Consultatie", canonical(slug))],
        ident=f"{canonical(slug)}/reacties", name=f"Reacties-{make_slug(title, 45)}",
    )


def fetch_reactie(slug: str, guid: str) -> Fetched:
    url = f"{BASE}/{quote(slug)}/reactie/{guid}"
    r = _parse_reactie(url)
    soup, _ = _soup(f"{BASE}/{quote(slug)}")
    title = _text(_content(soup).find("h1")) or slug
    naam = r["fields"].get("Naam") or "Anoniem"
    md = header(f"Reactie van {naam} op: {title}", [
        ("Publicatie", f"Internetconsultatie (`{slug}`)"),
        ("Plaats", r["fields"].get("Plaats")), ("Datum", r["fields"].get("Datum")),
        ("Bron", f"<{url}>"),
    ]) + "\n\n" + "\n\n".join(r["tekst"])
    items = [bijlage(canonical(slug), title, "Consultatie", canonical(slug))]
    if r["bestand"]:
        items.insert(0, bijlage(r["bestand"], "Bijlage bij de reactie", "Bijlage", r["bestand"]))
    return Fetched(tidy(md), f"Internetconsultatie • reactie van {naam} ({slug})", [], items,
                   ident=url, name=f"Reactie-{make_slug(naam, 30)}")


# --------------------------------------------------------------------------
# Een document of reactiebijlage (bestand)
# --------------------------------------------------------------------------

def filename_from(response) -> str:
    cd = response.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    return unquote(m.group(1)).strip() if m else ""


def download(url: str) -> tuple[bytes, str]:
    r = _get(url, stream=True)
    if r.status_code != 200:
        raise ConversionError(f"Kon het bestand niet ophalen (status {r.status_code}).")
    if int(r.headers.get("Content-Length") or 0) > MAX_DOWNLOAD_BYTES:
        raise ConversionError("Het bestand is te groot om hier om te zetten (meer dan 100 MB).")
    data = bytearray()
    for chunk in r.iter_content(1 << 20):
        data += chunk
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ConversionError("Het bestand is te groot om hier om te zetten (meer dan 100 MB).")
    name = filename_from(r) or unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "bestand"
    if "." not in name:
        from .common import extension_for
        name += "." + extension_for(r.headers.get("Content-Type"), "pdf")
    return bytes(data), name


def fetch_file(url: str, bron: str, back_to: str | None = None, back_title: str | None = None) -> Fetched:
    """Een document (of reactiebijlage) omzetten; `bron` is de naam van de site voor de bronvermelding."""
    from . import files

    data, name = download(url)
    markdown, engine = files.convert(data, name)
    if name.lower().endswith(".pdf"):
        markdown = files.warn_if_unmapped_glyphs(markdown)
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    head = header(stem, [("Bestand", name), ("Bron", f"<{url}>")])
    items = [bijlage(back_to, back_title or back_to, "Hoofddocument", back_to)] if back_to else []
    return Fetched(f"{head}\n\n{markdown.lstrip()}", f"{bron} • {name} • {engine}", [], items,
                   ident=url, name=make_slug(stem, 60))


# --------------------------------------------------------------------------
# Ophalen (router) en zoeken
# --------------------------------------------------------------------------

def fetch(url: str) -> Fetched:
    soort, slug, extra = parse_url(url)
    if soort == "nummer":
        r = _get(f"{BASE}/{extra}")
        if r.status_code != 200:
            raise ConversionError(f"Consultatie {extra} niet gevonden op internetconsultatie.nl.")
        slug = _slug_from_final_url(r.url)
        if not slug or slug.isdigit():
            raise ConversionError(f"Consultatie {extra} niet gevonden op internetconsultatie.nl.")
        soort = "consultatie"
    if soort == "consultatie":
        return fetch_consultatie(slug)
    if soort == "reacties":
        return fetch_reacties(slug)
    if soort == "reactie":
        return fetch_reactie(slug, extra)
    if soort == "document":
        return fetch_file(f"{BASE}/{quote(slug)}/document/{extra}", "Internetconsultatie",
                          canonical(slug), slug)
    return fetch_file(f"{BASE}/{quote(slug)}/reactie/{extra}/bestand", "Internetconsultatie",
                      canonical(slug), slug)


def _nl_date(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{int(m.group(3))}-{int(m.group(2))}-{m.group(1)} 00:00:00" if m else ""


def _iso(nl: str) -> str:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", nl or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""


def search(q: str, *, titel_alleen: bool = False, van: str = "", tot: str = "",
           page: int = 1) -> tuple[int, list[dict]]:
    """Zoek consultaties; 10 per pagina. Geeft (totaal, [ruwe resultaten])."""
    params = [("Trefwoorden", quote((q or "").strip(), safe="")),
              ("TrefwoordenSearchScope", "Titel" if titel_alleen else "TitelEnTekst")]
    if _nl_date(van):
        params.append(("ConsultatiedatumVan", quote(_nl_date(van), safe="")))
    if _nl_date(tot):
        params.append(("ConsultatiedatumTotEnMet", quote(_nl_date(tot), safe="")))
    if page > 1:
        params.append(("Pagina", str(page)))
    soup, _ = _soup(f"{BASE}/zoeken/resultaat?" + "&".join(f"{k}={v}" for k, v in params))
    c = _content(soup)
    m = re.search(r"(\d[\d.]*)\s+resultat", _text(c))      # "160 resultaten"
    total = int(m.group(1).replace(".", "")) if m else 0
    out = []
    for a in c.find_all("a", class_="result--title"):
        li = a.find_parent("li")
        meta = [_text(x) for x in li.find_all("li")] if li else []
        summary = _text(li.find("p")) if li else ""
        out.append({
            "slug": _slug_from_final_url(urljoin(BASE, a["href"])),
            "titel": _text(a), "samenvatting": summary,
            "status": next((x.split(":", 1)[1].strip() for x in meta if x.startswith("Status:")), ""),
            "sluiting": _iso(next((x for x in meta if x.startswith("Sluitingsdatum")), "")),
            "organisatie": next((x for x in meta if not x.startswith(("Status:", "Sluitingsdatum"))), ""),
        })
    return total or len(out), out
