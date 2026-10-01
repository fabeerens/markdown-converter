"""Woo-documenten en andere openbaarmakingen van open.overheid.nl.

open.overheid.nl (de Generieke Woo-voorziening / PLOOI) heeft een keyless JSON-API
waar de eigen website op draait — dezelfde als de zoekpagina gebruikt:

  GET /overheid/openbaarmakingen/api/v0/zoek?zoektekst=…        zoeken (+ facetten)
  GET /overheid/openbaarmakingen/api/v0/zoek/{id}               metadata van één document
  GET /overheid/openbaarmakingen/api/v0/documenten/{id}         het bestand zelf (meestal PDF)

Filters (`documentsoort`, `informatiecategorie`, `thema`, `organisatie`) gaan
**dubbel URL-gecodeerd** mee, net als de site het doet. Een paginagrootte mag
alleen 10, 20 of 50 zijn.

Een document kan relaties hebben (`documentrelaties`): "heeft bijlage" /
"is bijlage bij" / bundel / onderdeel. Die komen als bijlagen in het paneel.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote, urlparse

from .. import net
from ..errors import ConversionError, UpstreamError
from .common import (
    Fetched, MAX_DOWNLOAD_BYTES, bijlage, extension_for, header, nl_date, slug,
)

API = "https://open.overheid.nl/overheid/openbaarmakingen/api/v0"
SITE = "https://open.overheid.nl/documenten"
_TIMEOUT = 60
PAGE_SIZES = (10, 20, 50)

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Ids: een UUID (soms met `_2`-versiesuffix) of een bronvoorvoegsel (ronl-, oep-, …).
_ID_RE = re.compile(
    rf"\b((?:{_UUID}|(?:ronl|oep|plooi)-[0-9A-Za-z\-]+)(?:_\d+)?)\b", re.I
)

# Rollen uit de TOOI-thesaurus (labels opgehaald bij identifier.overheid.nl).
_ROLES = {
    "https://identifier.overheid.nl/tooi/def/thes/kern/c_05f4a5f3": "Bijlage",
    "https://identifier.overheid.nl/tooi/def/thes/kern/c_4d1ea9ba": "Is bijlage bij",
    "https://identifier.overheid.nl/plooi/def/thes/documentrelatie/bundel": "Dossier",
    "https://identifier.overheid.nl/plooi/def/thes/documentrelatie/onderdeel": "Onderdeel",
}
_MAX_RELATIONS = 30


def matches(query: str) -> bool:
    """Een link naar open.overheid.nl of een id met een bekend bronvoorvoegsel.

    Een kale UUID matcht hier bewust níet: die is ook een Tweede Kamer-Document-Id.
    `is_known_id()` beslist dat met één verzoek."""
    low = query.lower()
    if "open.overheid.nl" in low:
        return True
    return bool(re.search(r"\b(?:ronl|oep|plooi)-[0-9a-z\-]+(?:_\d+)?\b", low))


def parse_id(query: str) -> str:
    q = unquote(query.strip())
    path = urlparse(q).path if q.lower().startswith("http") else q
    m = _ID_RE.search(path) or _ID_RE.search(q)
    if not m:
        raise ConversionError(
            "Geen document van open.overheid.nl herkend. Geef een link "
            "(open.overheid.nl/documenten/…) of het document-id."
        )
    return m.group(1)


def _get(url: str, **kw):
    try:
        return net.documents().get(url, timeout=_TIMEOUT, **kw)
    except Exception as e:                      # netwerkfout: duidelijke melding
        raise UpstreamError(f"open.overheid.nl is niet bereikbaar ({type(e).__name__}).") from e


def detail(doc_id: str) -> dict | None:
    r = _get(f"{API}/zoek/{quote(doc_id, safe='')}")
    if r.status_code in (400, 404):
        return None
    if r.status_code != 200:
        raise UpstreamError(f"open.overheid.nl gaf een fout (status {r.status_code}).")
    return r.json()


def is_known_id(doc_id: str) -> bool:
    """Staat dit id bij open.overheid.nl? (voor het onderscheid met een TK-Document-Id)"""
    try:
        return detail(doc_id) is not None
    except UpstreamError:
        return False


# --------------------------------------------------------------------------
# Ophalen en omzetten
# --------------------------------------------------------------------------

def _labels(items) -> list[str]:
    return [i.get("label") for i in (items or []) if isinstance(i, dict) and i.get("label")]


def _title(det: dict, fallback: str) -> str:
    doc = det.get("document", {})
    return ((doc.get("titelcollectie") or {}).get("officieleTitel")
            or next(iter(doc.get("titels") or []), None) or fallback)


_CONVERTIBLE_PREFIXES = ("application/pdf", "application/msword", "application/vnd.", "text/",
                         "application/rtf")
# Een bestand mag van een extern adres komen (de API geeft dat soms mee); alleen
# overheidshosts vertrouwen we daarvoor.
_TRUSTED_HOSTS = ("overheid.nl", "rijksoverheid.nl", "tweedekamer.nl")


def _pick_file(det: dict) -> dict | None:
    """Het te converteren bestand uit de nieuwste versie: PDF/Office/tekst, geen zip."""
    versies = det.get("versies") or []
    files = (versies[-1].get("bestanden") or []) if versies else []
    ok = [f for f in files if (f.get("mime-type") or "").lower().startswith(_CONVERTIBLE_PREFIXES)]
    ok.sort(key=lambda f: 0 if "pdf" in (f.get("mime-type") or "") else 1)
    return (ok or [None])[0]


def _base_id(doc_id: str) -> str:
    """`…_2` is het versienummer; het bestand zit onder het id zonder suffix."""
    return re.sub(r"_\d+$", "", doc_id)


def _download(doc_id: str, entry: dict) -> tuple[bytes, str]:
    expected = entry.get("grootte")
    if expected and expected > MAX_DOWNLOAD_BYTES:
        raise ConversionError("Het bestand is te groot om hier om te zetten (meer dan 100 MB).")
    url = entry.get("url") or ""
    host = urlparse(url).hostname or ""
    if not (url.startswith("https://") and host.endswith(_TRUSTED_HOSTS)):
        url = f"{API}/documenten/{quote(_base_id(doc_id), safe='')}"
    r = _get(url, stream=True)
    if r.status_code in (400, 403, 404, 410):
        raise ConversionError(
            "Het bestand van dit document is niet beschikbaar (mogelijk ingetrokken of nog niet "
            f"gepubliceerd). Bekijk het op {SITE}/{doc_id}."
        )
    if r.status_code != 200:
        raise UpstreamError(f"Kon het bestand niet ophalen (status {r.status_code}).")
    data = bytearray()
    for chunk in r.iter_content(1 << 20):
        data += chunk
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ConversionError("Het bestand is te groot om hier om te zetten (meer dan 100 MB).")
    return bytes(data), r.headers.get("Content-Type", "")


def _related(det: dict) -> list[dict]:
    rels = [r for r in (det.get("documentrelaties") or []) if r.get("role") in _ROLES]
    rels = rels[:_MAX_RELATIONS]

    def one(rel):
        rid = rel["relation"].rstrip("/").rsplit("/", 1)[-1]
        title = rel.get("titel")
        try:
            d = detail(rid)
            if d:
                title = _title(d, title or rid)
        except Exception:
            pass
        return bijlage(rid, title or rid, _ROLES[rel["role"]], f"{SITE}/{rid}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(one, rels))


def fetch(query: str) -> Fetched:
    from . import files

    doc_id = parse_id(query)
    det = detail(doc_id)
    if det is None:
        raise ConversionError(f"Document {doc_id} niet gevonden op open.overheid.nl.")
    doc = det.get("document", {})
    cls = doc.get("classificatiecollectie") or {}
    entry = _pick_file(det)
    if entry is None:
        raise ConversionError(
            "Dit document heeft geen bestand dat omgezet kan worden (bv. alleen een zip of "
            f"niets gepubliceerd). Bekijk het op {SITE}/{doc_id}."
        )
    name, size = entry.get("bestandsnaam"), entry.get("grootte")

    with ThreadPoolExecutor(max_workers=2) as pool:
        related = pool.submit(_related, det)
        data, mime = _download(doc_id, entry)
        bijlagen = related.result()

    ext = (name.rsplit(".", 1)[-1].lower() if name and "." in name else None) or extension_for(mime)
    filename = name or f"{doc_id}.{ext}"
    markdown, engine = files.convert(data, filename)
    if filename.lower().endswith(".pdf"):
        markdown = files.warn_if_unmapped_glyphs(markdown)

    title = _title(det, name or doc_id)
    omschrijving = " ".join(doc.get("omschrijvingen") or [])
    head = header(title, [
        ("Publicatie", f"open.overheid.nl (`{doc_id}`)"),
        ("Soort", ", ".join(_labels(cls.get("documentsoorten")))),
        ("Organisatie", (doc.get("publisher") or {}).get("label")),
        ("Openbaarmakingsdatum", nl_date((det.get("versies") or [{}])[-1].get("openbaarmakingsdatum"))),
        ("Thema", ", ".join(_labels(cls.get("themas")))),
        ("Informatiecategorie", ", ".join(_labels(cls.get("informatiecategorieen")))),
        ("Bestand", f"{name} ({size / 1_048_576:.2f} MB" + (f", {entry['paginas']} p." if entry.get("paginas") else "") + ")" if name and size else name),
        ("Bron", f"<{SITE}/{doc_id}>"),
    ])
    parts = [head]
    if omschrijving:
        parts.append(f"> {omschrijving}")
    if len(markdown.strip()) < 50:
        parts.append(
            "*Er is vrijwel geen tekst uit dit bestand gehaald: het is waarschijnlijk een scan "
            "zonder tekstlaag. Download het origineel en gebruik de wiskunde-modus (OCR) bij "
            "Documentupload.*"
        )
    parts.append(markdown.lstrip())
    return Fetched(
        "\n\n".join(parts), f"open.overheid.nl • {title} ({doc_id}) • {engine}", [], bijlagen,
        ident=doc_id, name=f"Woo-{slug(title, 50)}",
    )


# --------------------------------------------------------------------------
# Zoeken
# --------------------------------------------------------------------------

def _enc(value: str) -> str:
    """De site codeert filterwaarden twee keer (en bouwt de URL zelf)."""
    return quote(quote(value, safe=""), safe="")


def search(q: str, *, soort: str = "", van: str = "", tot: str = "",
           sort: str = "relevantie", start: int = 0, n: int = 20) -> dict:
    """Zoek in open.overheid.nl; geeft de ruwe JSON (totaal, resultaten, filters)."""
    if n not in PAGE_SIZES:
        n = 20
    params = [("zoektekst", quote((q or "").strip(), safe="")), ("start", str(max(0, start))),
              ("aantalResultaten", str(n))]
    if sort in ("nieuwste", "oudste"):
        params.append(("sort", "publicatiedatum"))
        if sort == "oudste":
            params.append(("order", "asc"))
    # De API wil dd-mm-jjjj; de UI levert ISO-datums.
    for key, value in (("publicatiedatumVan", van), ("publicatiedatumTot", tot)):
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value or "")
        if m:
            params.append((key, f"{m.group(3)}-{m.group(2)}-{m.group(1)}"))
    if soort:
        params.append(("documentsoort", _enc(soort)))
    url = f"{API}/zoek?" + "&".join(f"{k}={v}" for k, v in params if v != "")
    r = _get(url)
    if r.status_code == 400:
        try:
            msg = r.json().get("detail")
        except Exception:
            msg = None
        raise ConversionError(msg or "open.overheid.nl weigerde de zoekopdracht.")
    if r.status_code != 200:
        raise UpstreamError(f"open.overheid.nl gaf een fout (status {r.status_code}).")
    return r.json()
