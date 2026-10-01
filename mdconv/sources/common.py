"""Gedeelde bouwstenen van de "Open overheid"-bronnen (kamerstukken, Woo).

`Fetched` is wat een bron teruggeeft vóór het een `Document` wordt; `bijlage()`
maakt de items voor het bijlagenpaneel rechts in de UI; `header()` bouwt het
metadatablok bovenaan de Markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Een bestand groter dan dit halen we niet binnen: een Woo-dossier kan honderden
# MB zijn, en de conversie draait synchroon in een verzoek.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

_EXT_BY_MIME = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/html": "html",
    "text/csv": "csv",
    "application/rtf": "rtf",
}


def extension_for(mime: str | None, fallback: str = "bin") -> str:
    return _EXT_BY_MIME.get((mime or "").split(";")[0].strip().lower(), fallback)


@dataclass
class Fetched:
    markdown: str
    source: str
    images: list[tuple[str, bytes]] = field(default_factory=list)
    bijlagen: list[dict] = field(default_factory=list)
    ident: str = ""      # het id waaronder de bron dit document kent (voor "al toegevoegd")
    name: str = ""       # voorstel voor de bestandsnaam (zonder extensie)
    # Wat er bij het omzetten opviel (een terugval, een weigering van de strenge route);
    # gaat als `Document.warnings` naar de UI en naar kb_fetch.
    warnings: tuple[str, ...] = ()
    # Herkomst met bronbewijs, alleen van een strenge route (een Kamerstuk uit de officiële
    # XML). Zonder herkomst is het een losse download: geen kennisbankbundel (WP-77).
    herkomst: object | None = None


def bijlage(query: str, titel: str, rol: str, open_url: str | None = None) -> dict:
    """Eén regel voor het bijlagenpaneel: `query` gaat naar /api/convert/overheid."""
    return {"query": query, "titel": titel or query, "rol": rol, "open_url": open_url}


BIJLAGEN_HEADING = "## Bijlagen en gerelateerde documenten"


def bijlagen_section(items: list[dict]) -> str:
    """Linklijst met bijlagen en gerelateerde documenten, als onderdeel van de Markdown.

    Elke link is een adres dat de tool zelf ook begrijpt: plak het bij "Ophalen" om dat
    document apart om te zetten."""
    if not items:
        return ""
    lines = []
    for it in items:
        url = it.get("open_url") or it["query"]
        lines.append(f"- [{it['titel']}]({url}) — {it['rol']}")
    return BIJLAGEN_HEADING + "\n\n" + "\n".join(lines)


def with_bijlagen(markdown: str, items: list[dict]) -> str:
    """Zet de bijlagenlijst onderaan, tenzij de bron hem al op de juiste plek heeft gezet."""
    if not items or BIJLAGEN_HEADING in markdown:
        return markdown
    return markdown.rstrip() + "\n\n" + bijlagen_section(items) + "\n"


def header(title: str, facts: list[tuple[str, str | None]]) -> str:
    """`# titel` + een opsomming `- **Label:** waarde` (lege waarden vallen weg)."""
    lines = [f"- **{label}:** {value}" for label, value in facts if value]
    clean = re.sub(r"\s+", " ", title).strip()
    return f"# {clean}\n\n" + "\n".join(lines)


def nl_date(iso: str | None) -> str | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return iso
    months = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
              "augustus", "september", "oktober", "november", "december"]
    return f"{int(m.group(3))} {months[int(m.group(2)) - 1]} {m.group(1)}"


def slug(text: str, limit: int = 60) -> str:
    """Titel → bestandsnaam: alleen letters/cijfers/streepjes, begrensd."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    plain = re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")
    return plain[:limit].rstrip("-") or "document"
