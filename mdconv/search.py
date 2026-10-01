"""Zoeken in de Open-overheid-bronnen, met één uniforme resultaatvorm.

Twee bronnen, één lijst voor de UI:
- `pub`: parlementaire publicaties via SRU (kamerstukken, Kamervragen, Handelingen);
- `woo`: open.overheid.nl (Woo-documenten en andere openbaarmakingen).

Elk resultaat heeft een `query` die `/api/convert/overheid` direct begrijpt.
"""

from __future__ import annotations

import re

from .errors import ConversionError
from .sources import sru, woo

SCOPES = ("pub", "woo")


def _flat(text: str | None, limit: int = 240) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _pub_result(r: sru.Record) -> dict:
    facts = [f for f in (
        f"Vergaderjaar {r.vergaderjaar}" if r.vergaderjaar else "",
        f"dossier {r.dossiernummer}" if r.dossiernummer else "",
        r.indiener,
    ) if f]
    return {
        "id": r.ident,
        "query": r.ident,
        "titel": r.title or r.ident,
        "soort": r.subsoort or r.soort,
        "datum": r.date,
        "bron": r.creator.replace(" der Staten-Generaal", ""),
        "meta": " · ".join(facts),
        "snippet": "",
        "open_url": r.page_url or f"https://zoek.officielebekendmakingen.nl/{r.ident}.html",
    }


def _woo_result(item: dict) -> dict:
    d = item.get("document", {})
    kind = (item.get("bestandsType") or "").split("/")[-1].upper()
    facts = [kind or None,
             f"{item['aantalPaginas']} p." if item.get("aantalPaginas") else None,
             item.get("bestandsgrootte")]
    return {
        "id": d.get("id"),
        "query": d.get("id"),
        "titel": d.get("titel") or d.get("id"),
        "soort": "",
        "datum": d.get("openbaarmakingsdatum") or "",
        "bron": d.get("publisher") or "",
        "meta": " · ".join(f for f in facts if f),
        "snippet": _flat(item.get("highlightedText") or d.get("omschrijving")),
        "open_url": d.get("pid") or f"{woo.SITE}/{d.get('id')}",
    }


def search(scope: str, q: str, *, soort: str = "", van: str = "", tot: str = "",
           sort: str = "relevantie", start: int = 0, n: int = 20) -> dict:
    """Uniform antwoord: totaal, resultaten, soorten (voor het filter) en sorteermogelijkheden."""
    if scope not in SCOPES:
        raise ConversionError("Onbekende zoekbron.")
    if scope == "pub":
        total, records = sru.search(q, soort=soort or "alles", van=van, tot=tot,
                                    sort=sort, start=start, n=n)
        return {
            "scope": scope, "total": total, "start": start, "n": n,
            "results": [_pub_result(r) for r in records],
            "soorten": [{"key": k, "label": label} for k, label, _c, _x in sru.SOORTEN],
        }
    if not (q or "").strip() and not soort and not van and not tot:
        raise ConversionError("Vul een zoekterm in (of kies een filter) om in de Woo-documenten te zoeken.")
    raw = woo.search(q, soort=soort, van=van, tot=tot, sort=sort, start=start, n=n)
    soorten = [
        {"key": f["naam"], "label": f"{f['naam']} ({f['aantal']})"}
        for f in sorted((raw.get("filters") or {}).get("documentsoort", []),
                        key=lambda f: -f.get("aantal", 0))[:40]
    ]
    return {
        "scope": scope, "total": raw.get("totaal", 0), "start": start, "n": n,
        "results": [_woo_result(i) for i in raw.get("resultaten", [])],
        "soorten": soorten,
    }
