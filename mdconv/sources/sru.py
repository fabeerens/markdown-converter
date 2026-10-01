"""Zoeken en opzoeken in officiële publicaties via de SRU-dienst van KOOP.

`https://repository.overheid.nl/sru` is keyless en bevat alle publicaties van
officielebekendmakingen.nl (kamerstukken, aanhangsels, Handelingen, bijlagen,
Staatscourant, …) met hun metadata. Hier alleen wat we nodig hebben:
- `search()`: CQL-zoekopdracht met paginering (zoeken in de UI);
- `by_identifier()`: één record op id (terugval als er geen XML is);
- `attachments_of()`: de bijlagen (`blg-…`) van een hoofddocument.

CQL-indexen die bevestigd werken: `dt.identifier`, `w.hoofddocument`,
`w.dossiernummer`, `w.publicatienaam`, `w.vergaderjaar`, `dt.type`, `dt.date`,
`cql.textAndIndexes` (volledige tekst). Een uitsluiting is `A NOT B` (niet
`AND NOT`), en sorteren gaat met `sortBy dt.date/sort.descending`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from .. import net
from ..errors import ConversionError, UpstreamError

SRU_URL = "https://repository.overheid.nl/sru"
_TIMEOUT = 45
_BASE = "c.product-area==officielepublicaties"

# publicatienaam-waarden waar onze converter iets mee kan.
_PUBLICATIES = {
    "kamerstuk": 'w.publicatienaam==Kamerstuk',
    "aanhangsel": 'w.publicatienaam=="Kamervragen (Aanhangsel)"',
    "handelingen": 'w.publicatienaam==Handelingen',
}
_PARLEMENTAIR = "(" + " OR ".join(_PUBLICATIES.values()) + ")"

# Zoek-"soorten" die de UI aanbiedt: (sleutel, label, CQL-deel, uitsluiting).
SOORTEN: tuple[tuple[str, str, str, str], ...] = (
    ("alles", "Alle parlementaire stukken", _PARLEMENTAIR, ""),
    ("kamerstuk", "Kamerstukken", _PUBLICATIES["kamerstuk"], "dt.type==Bijlage"),
    ("bijlage", "Bijlagen bij kamerstukken", _PUBLICATIES["kamerstuk"] + " AND dt.type==Bijlage", ""),
    ("aanhangsel", "Kamervragen met antwoord", _PUBLICATIES["aanhangsel"], ""),
    ("handelingen", "Handelingen", _PUBLICATIES["handelingen"], ""),
)

# Een dossiernummer is (vrijwel) altijd vijfcijferig: "36600", "36 600", "36600-VII",
# eventueel voorafgegaan door "dossier"/"kamerstuk(ken)". Vier cijfers ("2026") is een jaar.
_DOSSIER = re.compile(
    r"^\s*(?:(?:dossier|kamerstuk(?:ken)?)\s+)?(\d{2})[ .]?(\d{3})(?:[\s\-–]+([IVXLC]+[A-Z]?))?\s*$",
    re.I,
)


def dossier_of(q: str) -> str | None:
    """"36 600 vii" → "36600-VII"; None als de zoekterm geen dossiernummer is."""
    m = _DOSSIER.match(q or "")
    if not m:
        return None
    return m.group(1) + m.group(2) + (f"-{m.group(3).upper()}" if m.group(3) else "")


@dataclass(frozen=True)
class Record:
    ident: str
    title: str = ""
    soort: str = ""             # dcterms:type (Parlementair): Kamerstuk, Bijlage, …
    subsoort: str = ""          # KamerstukTypen: "Brief regering", "Motie", …
    publicatienaam: str = ""
    creator: str = ""
    date: str = ""
    vergaderjaar: str = ""
    dossiernummer: str = ""
    hoofddocument: str = ""
    indiener: str = ""
    page_url: str = ""
    files: dict = field(default_factory=dict)       # manifestatie → url

    @property
    def pdf_url(self) -> str | None:
        return self.files.get("pdf")


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _records(xml: bytes) -> tuple[int, list[Record]]:
    try:
        root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise UpstreamError(f"Onleesbaar antwoord van de zoekdienst: {e}") from e
    diag = [el.text for el in root.iter() if _local(el) in ("message", "details") and el.text]
    if diag and not any(_local(el) == "record" for el in root.iter()):
        raise ConversionError(f"De zoekdienst weigerde de zoekopdracht ({'; '.join(diag[:2])}).")
    total = next((int(el.text) for el in root.iter() if _local(el) == "numberOfRecords"), 0)
    out: list[Record] = []
    for rec in (el for el in root.iter() if _local(el) == "record"):
        vals: dict[str, str] = {}
        files: dict[str, str] = {}
        for el in rec.iter():
            name = _local(el)
            if name == "itemUrl" and el.get("manifestation") and el.text:
                files[el.get("manifestation")] = el.text.strip()
            elif name == "type" and el.get("scheme") == "OVERHEIDop.KamerstukTypen":
                vals.setdefault("subsoort", (el.text or "").strip())
            elif name == "type" and el.get("scheme") == "OVERHEIDop.Parlementair":
                vals.setdefault("soort", (el.text or "").strip())
            elif name == "preferredUrl":
                vals.setdefault("page_url", (el.text or "").strip())
            elif name in ("identifier", "title", "creator", "date", "publicatienaam",
                          "vergaderjaar", "dossiernummer", "hoofddocument", "indiener") \
                    and el.text and not len(el):
                vals.setdefault(name, el.text.strip())
        if vals.get("identifier"):
            out.append(Record(
                ident=vals["identifier"], title=vals.get("title", ""),
                soort=vals.get("soort", ""), subsoort=vals.get("subsoort", ""),
                publicatienaam=vals.get("publicatienaam", ""), creator=vals.get("creator", ""),
                date=vals.get("date", ""), vergaderjaar=vals.get("vergaderjaar", ""),
                dossiernummer=vals.get("dossiernummer", ""),
                hoofddocument=vals.get("hoofddocument", ""), indiener=vals.get("indiener", ""),
                page_url=vals.get("page_url", ""), files=files,
            ))
    return total, out


def _run(cql: str, *, start: int = 1, n: int = 20) -> tuple[int, list[Record]]:
    r = net.documents().get(
        SRU_URL,
        params={"query": cql, "version": "1.2", "startRecord": start, "maximumRecords": n},
        timeout=_TIMEOUT,
    )
    if r.status_code != 200:
        raise UpstreamError(f"De zoekdienst van overheid.nl gaf een fout (status {r.status_code}).")
    return _records(r.content)


def _quote(text: str) -> str:
    """Zoektekst veilig in een CQL-string: aanhalingstekens en backslashes eruit."""
    return re.sub(r'["\\]', " ", text).strip()


def build_query(q: str, soort: str, van: str, tot: str, sort: str) -> str:
    cql_soort, exclude = next(((c, x) for k, _l, c, x in SOORTEN if k == soort),
                              (SOORTEN[0][2], SOORTEN[0][3]))
    parts = [_BASE, cql_soort]
    q = (q or "").strip()
    dossier = dossier_of(q)
    if dossier:
        parts.append(f'w.dossiernummer=="{dossier}"')
    elif q:
        parts.append(f'cql.textAndIndexes="{_quote(q)}"')
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", van or ""):
        parts.append(f"dt.date>={van}")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tot or ""):
        parts.append(f"dt.date<={tot}")
    cql = " AND ".join(parts)
    if exclude:
        cql += f" NOT {exclude}"
    # Zonder zoektekst is "nieuwste eerst" de enige zinnige volgorde.
    # Een dossier leest chronologisch (oudste eerst) tenzij je zelf anders kiest.
    order = {"nieuwste": "descending", "oudste": "ascending"}.get(
        sort, "ascending" if dossier else "descending" if not q else None)
    if order:
        cql += f" sortBy dt.date/sort.{order}"
    return cql


def search(q: str, *, soort: str = "alles", van: str = "", tot: str = "",
           sort: str = "relevantie", start: int = 0, n: int = 20) -> tuple[int, list[Record]]:
    """Zoek parlementaire publicaties; `start` is 0-gebaseerd."""
    return _run(build_query(q, soort, van, tot, sort), start=start + 1, n=n)


def by_identifier(ident: str) -> Record | None:
    _total, records = _run(f"{_BASE} AND dt.identifier=={ident}", n=1)
    return records[0] if records else None


def attachments_of(ident: str, limit: int = 50) -> list[Record]:
    """Bijlagen (`blg-…`) die bij een hoofddocument horen."""
    _total, records = _run(f"{_BASE} AND w.hoofddocument=={ident}", n=limit)
    return records
