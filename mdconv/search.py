"""Zoeken in de Open-overheid-bronnen, met één uniforme resultaatvorm.

Twee bronnen, die ook samen als één lijst te doorzoeken zijn:
- `pub`: parlementaire publicaties via SRU (kamerstukken, Kamervragen, Handelingen);
- `woo`: open.overheid.nl (Woo-documenten en andere openbaarmakingen);
- `alles`: beide tegelijk, samengevoegd (zie `_search_all`).

Elk resultaat heeft een `query` die `/api/convert/overheid` direct begrijpt.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .errors import ConversionError
from .sources import consultatie, sru, wgk, woo

SCOPES = ("alles", "pub", "woo", "consultatie", "wgk")

# Bij "alles" halen we per bron de eerste `start + n` resultaten op en voegen die
# samen; dieper dan dit bladeren we niet (anders groeit elke pagina-aanvraag mee).
MERGE_LIMIT = 200


def _flat(text: str | None, limit: int = 240) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_KST_ID = re.compile(r"^kst-(\d{5}(?:-[0-9A-Z]+)*)-([0-9A-Z]+)$")


def _stuk_label(ident: str) -> str:
    """kst-36600-VII-1 → "36600-VII, nr. 1": zo herken je een stuk in een lange dossierlijst,
    en het is dezelfde schrijfwijze als Woo ("36913, nr. 6 - …")."""
    m = _KST_ID.match(ident)
    return f"{m.group(1)}, nr. {m.group(2)}" if m else ""


def _pub_result(r: sru.Record) -> dict:
    facts = [f for f in (
        f"Vergaderjaar {r.vergaderjaar}" if r.vergaderjaar else "",
        f"dossier {r.dossiernummer}" if r.dossiernummer else "",
        r.indiener,
    ) if f]
    return {
        "id": r.ident,
        "query": r.ident,
        "titel": " - ".join(p for p in (_stuk_label(r.ident), r.title or r.ident) if p),
        "soort": r.subsoort or r.soort,
        "datum": r.date,
        "bron": r.creator.replace(" der Staten-Generaal", ""),
        "meta": " · ".join(facts),
        "snippet": "",
        "open_url": r.page_url or f"https://zoek.officielebekendmakingen.nl/{r.ident}.html",
        "bronsoort": "pub",
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
        "bronsoort": "woo",
    }


def _cons_result(x: dict) -> dict:
    return {
        "id": x["slug"], "query": consultatie.canonical(x["slug"]), "titel": x["titel"],
        "soort": x["status"], "datum": x["sluiting"], "bron": x["organisatie"],
        "meta": "sluitingsdatum" if x["sluiting"] else "",     # de datum zelf staat al in `datum`
        "snippet": _flat(x["samenvatting"]), "open_url": consultatie.canonical(x["slug"]),
        "bronsoort": "consultatie",
    }


def _wgk_result(x: dict) -> dict:
    return {
        "id": x["id"], "query": x["id"], "titel": x["titel"], "soort": x["fase"], "datum": "",
        "bron": x["ministerie"], "meta": "", "snippet": "",
        "open_url": f"{wgk.BASE}/Regeling/{x['id']}", "bronsoort": "wgk",
    }


_NEED_TERM = "Vul een zoekterm in (of kies een filter) om in de documenten van open overheid te zoeken."


def _search_pub(q, soort, van, tot, sort, start, n):
    total, records = sru.search(q, soort=soort or "alles", van=van, tot=tot,
                                sort=sort, start=start, n=n)
    return total, [_pub_result(r) for r in records]


def _search_woo(q, soort, van, tot, sort, start, n):
    raw = woo.search(q, soort=soort, van=van, tot=tot, sort=sort, start=start, n=n)
    return raw.get("totaal", 0), [_woo_result(i) for i in raw.get("resultaten", [])], raw


def _woo_soorten(raw: dict) -> list[dict]:
    return [
        {"key": f["naam"], "label": f"{f['naam']} ({f['aantal']})"}
        for f in sorted((raw.get("filters") or {}).get("documentsoort", []),
                        key=lambda f: -f.get("aantal", 0))[:40]
    ]


# -- Alles: beide bronnen in één lijst ---------------------------------------

# "36913, nr. 6 - Titel" → "Titel": Woo zet het dossier- en stuknummer voor de titel.
_WOO_PREFIX = re.compile(r"^\s*\d[\d;, \-A-Za-z]*?,\s*nr\.\s*\S+\s*-\s*")
_DUP_DAYS = 21


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _pub_keys(title: str) -> set[str]:
    """Een kamerstuk-titel is "dossiertitel; soort; stuktitel", een bijlage heeft alleen de
    eigen titel: vergelijk daarom met zowel het geheel als het laatste deel."""
    title = _WOO_PREFIX.sub("", title or "")
    parts = [p for p in (t.strip() for t in title.split(";")) if p]
    return {k for k in (_norm(title), _norm(parts[-1]) if parts else "") if k}


def _days_apart(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return 10**6


def _merge(pub: list[dict], woo_: list[dict], sort: str) -> list[dict]:
    """Eén lijst: dubbelen (dezelfde titel binnen drie weken) blijven één keer staan, met de
    officiële publicatie als hoofdresultaat en de vermelding dat het ook bij Woo staat."""
    by_key: dict[str, list[dict]] = {}
    for r in pub:
        for key in _pub_keys(r["titel"]):
            by_key.setdefault(key, []).append(r)
    extra: list[dict] = []
    for w in woo_:
        key = _norm(_WOO_PREFIX.sub("", w["titel"]))
        twin = next((r for r in by_key.get(key, []) if _days_apart(r["datum"], w["datum"]) <= _DUP_DAYS), None)
        if twin is not None:
            twin["ook_woo"] = True
        else:
            extra.append(w)
    # Afwisselend, zodat bij "relevantie" geen bron de kop van de lijst overneemt.
    merged: list[dict] = []
    for i in range(max(len(pub), len(extra))):
        merged += [r for r in (pub[i:i + 1] + extra[i:i + 1])]
    if sort in ("nieuwste", "oudste"):
        merged.sort(key=lambda r: r["datum"] or "", reverse=(sort == "nieuwste"))
    return merged


def _collect(fetch, need: int, size: int) -> tuple[int, list[dict], object]:
    """De eerste `need` resultaten van één bron, in pagina's van `size`."""
    total, results, extra = 0, [], None
    for st in range(0, need, size):
        part = fetch(st, min(size, need - st))
        total, results = part[0], results + part[1]
        extra = part[2] if len(part) > 2 else extra
        if len(results) >= total or not part[1]:
            break
    return total, results[:need], extra


def _search_all(q, van, tot, sort, start, n) -> dict:
    need = min(start + n, MERGE_LIMIT)

    def pub():
        return _collect(lambda st, k: _search_pub(q, "", van, tot, sort, st, k), need, 100)

    def woo_():
        return _collect(lambda st, k: _search_woo(q, "", van, tot, sort, st, k), need, 50)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {"pub": pool.submit(pub), "woo": pool.submit(woo_)}
    got, warnings = {}, []
    for name, label in (("pub", "parlementaire stukken"), ("woo", "documenten van open overheid")):
        try:
            got[name] = futures[name].result()
        except Exception as e:                      # één bron mag uitvallen
            warnings.append(f"Zoeken in {label} mislukte: {e}")
    if not got:
        raise ConversionError(" ".join(warnings))
    merged = _merge(got.get("pub", (0, []))[1], got.get("woo", (0, []))[1], sort)
    totals = {k: v[0] for k, v in got.items()}
    total = sum(totals.values())
    return {
        "scope": "alles", "total": total, "start": start, "n": n,
        "limit": min(total, MERGE_LIMIT), "totals": totals,
        "results": merged[start:start + n], "soorten": [],
        **({"waarschuwing": " ".join(warnings)} if warnings else {}),
    }


def search(scope: str, q: str, *, soort: str = "", van: str = "", tot: str = "",
           sort: str = "relevantie", start: int = 0, n: int = 20,
           status: str = "", fase: str = "", type_: str = "", zoekin: str = "") -> dict:
    """Uniform antwoord: totaal, resultaten, soorten (voor het filter) en sorteermogelijkheden."""
    if scope not in SCOPES:
        raise ConversionError("Onbekende zoekbron.")
    if scope == "consultatie":
        # De site geeft vaste pagina's van 10; een paginagrootte kiezen kan niet.
        n = consultatie.PAGE_SIZE
        total, raw = consultatie.search(q, titel_alleen=(zoekin == "titel"), van=van, tot=tot,
                                        page=start // n + 1)
        return {"scope": scope, "total": total, "start": start, "n": n, "limit": total,
                "results": [_cons_result(x) for x in raw], "soorten": []}
    if scope == "wgk":
        n = wgk.PAGE_SIZE
        total, raw = wgk.search(q, status=status, fase=fase, type_=type_, page=start // n + 1, size=n)
        return {"scope": scope, "total": total, "start": start, "n": n, "limit": total,
                "results": [_wgk_result(x) for x in raw], "soorten": []}
    pub_soorten = [{"key": k, "label": label} for k, label, _c, _x in sru.SOORTEN]
    dossier = sru.dossier_of(q)
    if dossier and scope in ("alles", "pub"):
        # Een dossiernummer is geen tekstzoekopdracht maar vraagt om álle stukken van het
        # dossier. Die staan in de officiële publicaties (Woo-kopieën zijn dubbelen), en een
        # volledige lijst is handiger in grote pagina's.
        n = max(n, 50)
        total, results = _search_pub(q, soort if scope == "pub" else "", van, tot, sort, start, n)
        return {
            "scope": scope, "total": total, "start": start, "n": n, "limit": total,
            "results": results, "soorten": pub_soorten if scope == "pub" else [],
            "dossier": dossier,
        }
    if scope == "pub":
        total, results = _search_pub(q, soort, van, tot, sort, start, n)
        return {
            "scope": scope, "total": total, "start": start, "n": n, "limit": total,
            "results": results, "soorten": pub_soorten,
        }
    if not (q or "").strip() and not soort and not van and not tot:
        raise ConversionError(_NEED_TERM)
    if scope == "alles":
        return _search_all(q, van, tot, sort, start, n)
    total, results, raw = _search_woo(q, soort, van, tot, sort, start, n)
    return {
        "scope": scope, "total": total, "start": start, "n": n, "limit": total,
        "results": results, "soorten": _woo_soorten(raw),
    }
