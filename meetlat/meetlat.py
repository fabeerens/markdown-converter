"""De meetlat: hoeveel van een vaste verzameling EU-documenten komt door de Formex-route?

**Waarom dit bestaat.** Tot 23 september 2026 werd de Formex-omzetter gerepareerd
per document dat een gebruiker tegenkwam, en getest op de handvol zips die er
toevallig lagen. Op 121 handelingen gemeten weigerde hij er 62 (51%), de AVG
incluis; elke nieuwe lijst raakte dus weer een nieuw gat, en een reparatie voor
het ene document kon een ander breken zonder dat iemand het zag. De meetlat laat
dat zien vóórdat de gebruiker het ziet: één vaste verzameling (`corpus.txt`), de
bronbytes lokaal bewaard, en per document de uitkomst van precies de route die
de tool ook gebruikt.

    .venv/bin/python meetlat/meetlat.py ophalen             # ontbrekende zips downloaden
    .venv/bin/python meetlat/meetlat.py meten               # offline meten en vergelijken
    .venv/bin/python meetlat/meetlat.py meten -v            # met elke weigering en haar melding
    .venv/bin/python meetlat/meetlat.py meten --bijwerken   # uitkomst wordt de nieuwe basislijn

**`meten` raakt het netwerk nooit.** Het vervangt `net.documents()` door een sessie
die alleen uit `cache/` antwoordt, en roept daarna dezelfde functies aan als de
tool (`eurlex._fetch_formex` en `eurlex._fetch_hof`). Vraagt de route iets op wat
niet in de cache staat — bij een geconsolideerde tekst bijvoorbeeld de
basishandeling — dan is de uitkomst `niet-in-cache`, geen stille terugval.

**De basislijn (`basislijn.json`) staat in git**: per document de status, de
oorzaak van een weigering en een hash van de Markdown. `meten` eindigt met code 1
als een document dat in de basislijn doorkwam nu geweigerd wordt, als een
document doorkomt met andere uitvoer, bij een crash (iets anders dan een
`ConversionError`) of bij `niet-in-cache`. Een bewuste uitvoerwijziging leg je
vast met `--bijwerken` en verantwoord je in de commit; de oude en nieuwe
Markdown staan dan naast elkaar in `uitvoer/basis/` en `uitvoer/laatste/`.

Alleen de Formex-tak wordt gemeten. Een handeling zonder Formex (vóór ± 2004)
gaat in de tool naar de HTML-ladder en telt hier als `geen-formex`, niet als
weigering.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

MAP = Path(__file__).resolve().parent
ROOT = MAP.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TAAL = "NL"
# Deze vaste zin staat bij elk Formex-document; in de basislijn is hij ruis.
_STANDAARDZIN = "EUR-Lex is via de officiële Formex-manifestatie opgehaald."
# Een melding is voor de mens; in de basislijn volstaat het begin.
_MELDING_MAX = 400


@dataclass(frozen=True)
class Paden:
    corpus: Path = MAP / "corpus.txt"
    basislijn: Path = MAP / "basislijn.json"
    cache: Path = MAP / "cache"
    uitvoer: Path = MAP / "uitvoer"


# ------------------------------------------------------------------ corpus


def lees_corpus(pad: Path) -> list[tuple[str, str]]:
    """`(identificatie, sectie)` per regel; `#` begint commentaar, `[naam]` een sectie."""
    items, sectie, gezien = [], "?", set()
    for regel in pad.read_text(encoding="utf-8").splitlines():
        regel = regel.split("#", 1)[0].strip()
        if not regel:
            continue
        kop = re.fullmatch(r"\[([\w-]+)\]", regel)
        if kop:
            sectie = kop.group(1)
            continue
        if regel in gezien:
            raise SystemExit(f"{pad}: {regel} staat er twee keer in")
        gezien.add(regel)
        items.append((regel, sectie))
    return items


def route_van(ident: str) -> str:
    """Dezelfde splitsing als `eurlex.fetch_and_convert`: ECLI en sector 6 zijn rechtspraak."""
    return "hof" if ident.upper().startswith("ECLI:") or ident.startswith("6") else "wetgeving"


def bestandsnaam(ident: str) -> str:
    return ident.replace(":", "_")


# ------------------------------------------------------------------ cache


class NietInCache(Exception):
    """De route vraagt iets op wat `ophalen` nog niet heeft bewaard."""


class _Antwoord:
    def __init__(self, status_code: int, content: bytes, url: str):
        self.status_code = status_code
        self.content = content
        self.url = url


def cachenaam(url: str, headers: dict | None) -> str:
    """`32016R0679.nld` of `ECLI_EU_C_2014_317.nld`; alleen Formex-verzoeken horen hier."""
    headers = headers or {}
    m = re.search(r"/resource/(?:celex|ecli)/([^/?#]+)$", url)
    if not m or "fmx4" not in headers.get("Accept", ""):
        raise RuntimeError(f"meetlat: onverwacht verzoek {url} met {headers}")
    return f"{bestandsnaam(unquote(m.group(1)))}.{headers.get('Accept-Language', '')}"


class CacheSessie:
    """Antwoordt uit de cache; alleen `online` haalt een ontbrekend antwoord op en bewaart het.

    Een zip komt in `<naam>.zip`. Elk ander antwoord met een 2xx- of 4xx-status
    komt in `<naam>.http<status>`, met de body erin, zodat het offline precies zo
    terugkomt. Een 5xx of een netwerkfout wordt niet bewaard: dat is een
    storing, geen eigenschap van het document.
    """

    def __init__(self, cache: Path, online: bool, echte_sessie=None):
        self.cache = cache
        self.online = online
        self.echte_sessie = echte_sessie
        self.gemist: list[str] = []
        self.opgehaald: list[str] = []

    def get(self, url, headers=None, **kwargs):
        naam = cachenaam(url, headers)
        zip_pad = self.cache / f"{naam}.zip"
        if zip_pad.exists():
            return _Antwoord(200, zip_pad.read_bytes(), url)
        for pad in self.cache.glob(f"{glob.escape(naam)}.http*"):
            return _Antwoord(int(pad.suffix[len(".http"):]), pad.read_bytes(), url)
        if not self.online:
            self.gemist.append(naam)
            raise NietInCache(naam)
        antwoord = self.echte_sessie().get(url, headers=headers, **kwargs)
        data = bytes(antwoord.content or b"")
        self.cache.mkdir(parents=True, exist_ok=True)
        if antwoord.status_code == 200 and data.startswith(b"PK"):
            doel = zip_pad
        elif antwoord.status_code < 500:
            doel = self.cache / f"{naam}.http{antwoord.status_code}"
        else:
            return antwoord
        tijdelijk = doel.with_name(doel.name + ".tmp")
        tijdelijk.write_bytes(data)
        os.replace(tijdelijk, doel)
        self.opgehaald.append(naam)
        return antwoord


# ------------------------------------------------------------------ oorzaken


def oorzaak(melding: str) -> str:
    """Een korte, groepeerbare naam voor de reden van een weigering.

    De meldingen zelf noemen bestanden, woorden en aantallen; die verschillen per
    document, terwijl de reparatie dezelfde is. Hier blijft alleen over wat de
    reparatie bepaalt.
    """
    m = re.search(r"tekst zonder eigen behandeling: (.*?);", melding)
    if m:
        return "element " + ", ".join(sorted(re.sub(r"\s*\(\d+×\)", "", m.group(1)).split(", ")))
    m = re.search(r"zonder eigen behandeling[^(]*\(([^)]*)\)", melding)
    if m:
        return f"element {m.group(1)}"
    m = re.search(r"inclusie heeft het onbekende type '([^']*)'", melding)
    if m:
        return f"inclusie van type {m.group(1)}"
    if "tekstbehoud faalt" in melding:
        return "woordcontrole: " + _woordverschil(melding)
    if "mist of verdubbelt tekst" in melding:
        return "woordcontrole"
    if "bladalinea" in melding:
        return "bladalinea-controle"
    if "structuurcontrole faalt" in melding:
        # Een toelichting tussen haakjes kan zelf een puntkomma bevatten.
        delen = re.sub(r"\([^)]*\)", "", melding.split("faalt:", 1)[1]).split(";")
        soorten = sorted({d.split(":", 1)[0].strip() for d in delen if d.strip()})
        return "structuur: " + ", ".join(soorten)
    if "niet terug te vinden in de Markdown" in melding:
        return "eenheid niet terug te vinden"
    if "de zip bevat naast de XML ook" in melding:
        return "zip bevat ook andere bestanden"
    kern = re.sub(r"^Formex-(?:bron|uitspraak) geweigerd: ", "", melding)
    kern = re.sub(r"\([^)]*\)", "", kern).split(";")[0].split(":")[0]
    kern = re.sub(r"\d+", "#", " ".join(kern.split()))
    return kern[:90].rstrip(" .") or "onbekend"


def _woordverschil(melding: str) -> str:
    """Drie soorten woordverschil vragen drie soorten reparatie.

    Gemeten op 23 september 2026: `2016betreffende` (een spatie weg tussen twee
    elementen) is een andere fout dan een tabeltitel die helemaal wegvalt
    (`concordantietabel`, 12 keer), en die weer een andere dan tekst die twee keer
    wordt geschreven.
    """
    def lijst(naam: str) -> list[str]:
        m = re.search(naam + r"=(\[.*?\]|niets)", melding)
        if not m or m.group(1) == "niets":
            return []
        return re.findall(r"'([^']*)'", m.group(1))

    ontbreekt, extra = lijst("ontbreekt"), lijst("extra")
    if extra and not ontbreekt:
        return "tekst dubbel"
    if ontbreekt and not extra:
        return "tekst valt weg"
    if extra and all(any(e.startswith(o) and e[len(o):] in ontbreekt for o in ontbreekt) for e in extra):
        return "woorden aan elkaar"
    return "tekst verschilt"


# ------------------------------------------------------------------ meten

_SESSIE: CacheSessie | None = None


def _init_werker(cache: str, online: bool) -> None:
    """Vervang `net.documents()` in dit proces door de cachesessie."""
    global _SESSIE
    from mdconv import net

    echte = net.documents
    _SESSIE = CacheSessie(Path(cache), online, echte)
    net.documents = lambda: _SESSIE


def _formexroute(ident: str):
    """Precies de Formex-tak van `eurlex.fetch_and_convert`, zonder de HTML-ladder.

    Geeft `(markdown, herkomst, melding)`; `markdown` is None als de Cellar geen
    Formex heeft en de tool naar HTML zou gaan.
    """
    from mdconv.sources import eurlex

    if route_van(ident) == "hof":
        markdown, _bron, herkomst = eurlex._fetch_hof(ident, TAAL, requested_url=ident)
        return markdown, herkomst, None
    if _SESSIE.online and ident.startswith("0"):
        # De basishandeling vraagt de route pas op als de geconsolideerde tekst
        # zelf door de omzetter komt. Nu al ophalen voorkomt dat een latere
        # reparatie op `niet-in-cache` stuit.
        basis = "3" + ident[1:].split("-", 1)[0]
        try:
            _SESSIE.get(f"http://publications.europa.eu/resource/celex/{basis}",
                        headers=eurlex._formex_headers(TAAL), timeout=eurlex._CELLAR_TIMEOUT,
                        allow_redirects=True)
        except Exception:
            pass
    resultaat, melding = eurlex._fetch_formex(ident, TAAL, requested_url=ident)
    if resultaat is None:
        return None, None, melding
    markdown, _bron, herkomst = resultaat
    return markdown, herkomst, None


def meet_een(ident: str, sectie: str, uitvoer: str | None) -> dict:
    """Eén document door de route; nooit een exceptie naar buiten."""
    from mdconv.errors import ConversionError

    _SESSIE.gemist.clear()
    begin = time.perf_counter()
    uitkomst = {"sectie": sectie, "route": route_van(ident), "status": None, "oorzaak": None,
                "melding": None, "sha256": None, "tekens": None, "waarschuwingen": []}
    try:
        markdown, herkomst, melding = _formexroute(ident)
    except ConversionError as exc:
        tekst = str(exc)
        if "geen Formex-manifestatie" in tekst:
            uitkomst.update(status="geen-formex", melding=tekst[:_MELDING_MAX])
        else:
            uitkomst.update(status="weigering", oorzaak=oorzaak(tekst), melding=tekst[:_MELDING_MAX])
    except NietInCache as exc:
        uitkomst.update(status="niet-in-cache", melding=f"{exc} ontbreekt in de cache")
    except Exception as exc:  # een crash is erger dan een weigering en telt apart
        uitkomst.update(status="crash", oorzaak=type(exc).__name__,
                        melding=f"{type(exc).__name__}: {exc}"[:_MELDING_MAX])
    else:
        if markdown is None:
            uitkomst.update(status="geen-formex", melding=melding)
        else:
            uitkomst.update(
                status="ok",
                sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                tekens=len(markdown),
                waarschuwingen=[w for w in herkomst.waarschuwingen if w != _STANDAARDZIN],
            )
            if uitvoer:
                pad = Path(uitvoer) / f"{bestandsnaam(ident)}.md"
                pad.write_text(markdown, encoding="utf-8")
    # `_fetch_formex` vangt netwerkfouten zelf af en valt dan terug; een gemiste
    # cache mag daardoor nooit als "geen Formex" of als onvolledige tekst tellen.
    if _SESSIE.gemist:
        uitkomst.update(status="niet-in-cache", oorzaak=None, sha256=None, tekens=None,
                        melding=", ".join(_SESSIE.gemist) + " ontbreekt in de cache")
    uitkomst["seconden"] = round(time.perf_counter() - begin, 2)
    return uitkomst


def meet(items: list[tuple[str, str]], paden: Paden, *, online: bool = False,
         werkers: int = 0, uitvoer: Path | None = None) -> dict[str, dict]:
    """Meet alle items; `werkers` <= 1 draait in dit proces (voor de tests)."""
    if uitvoer is not None:
        uitvoer.mkdir(parents=True, exist_ok=True)
    args = [(ident, sectie, str(uitvoer) if uitvoer else None) for ident, sectie in items]
    if werkers <= 1:
        from mdconv import net

        oud = net.documents
        _init_werker(str(paden.cache), online)
        try:
            return {a[0]: meet_een(*a) for a in args}
        finally:
            net.documents = oud
    with ProcessPoolExecutor(werkers, initializer=_init_werker,
                             initargs=(str(paden.cache), online)) as pool:
        resultaten = pool.map(meet_een, *zip(*args), chunksize=1)
        return {a[0]: r for a, r in zip(args, resultaten)}


# ------------------------------------------------------------------ vergelijken


def vergelijk(oud: dict[str, dict], nieuw: dict[str, dict]) -> dict[str, list[str]]:
    """Wat verschilt met de basislijn; alleen documenten die in beide staan."""
    v = defaultdict(list)
    for ident, n in nieuw.items():
        o = oud.get(ident)
        if o is None:
            v["nieuw in de verzameling"].append(ident)
            continue
        if n["status"] in ("crash", "niet-in-cache"):
            continue  # die staan al apart in de samenvatting
        if o["status"] == "ok" and n["status"] != "ok":
            v["nieuw geweigerd"].append(ident)
        elif o["status"] != "ok" and n["status"] == "ok":
            v["nieuw door"].append(ident)
        elif o["status"] == n["status"] == "ok" and o["sha256"] != n["sha256"]:
            v["andere uitvoer"].append(ident)
        elif o["status"] == n["status"] == "weigering" and o["oorzaak"] != n["oorzaak"]:
            v["andere oorzaak van weigering"].append(ident)
        if n["status"] == "ok" and o.get("waarschuwingen") != n.get("waarschuwingen"):
            v["andere waarschuwingen"].append(ident)
    return dict(v)


# Deze verschillen vragen een bewuste keuze; de rest is informatie.
BLOKKEREND = ("nieuw geweigerd", "andere uitvoer")


def _pct(deel: int, geheel: int) -> str:
    return f"{100 * deel / geheel:.1f}%".replace(".", ",") if geheel else "-"


def rapport(resultaat: dict[str, dict], basis: dict | None, *, uitgebreid: bool = False) -> tuple[str, bool]:
    """De tekst voor de terminal, en of er iets is dat de exitcode 1 maakt."""
    regels = []
    statussen = ("ok", "weigering", "crash", "geen-formex", "niet-in-cache")
    per_route = defaultdict(Counter)
    for r in resultaat.values():
        per_route[r["route"]][r["status"]] += 1
        per_route["totaal"][r["status"]] += 1

    regels.append(f"{'':16}{'door':>6}{'geweigerd':>11}{'crash':>7}{'geen Formex':>13}"
                  f"{'niet in cache':>15}   doorlaat")
    for route in ("wetgeving", "hof", "totaal"):
        t = per_route.get(route)
        if not t:
            continue
        gemeten = t["ok"] + t["weigering"] + t["crash"]
        regels.append(f"{route:16}" + "".join(f"{t[s]:>{w}}" for s, w in zip(statussen, (6, 11, 7, 13, 15)))
                      + f"   {t['ok']}/{gemeten} ({_pct(t['ok'], gemeten)})")

    regels.append("")
    regels.append("Per sectie (door/gemeten):")
    per_sectie = defaultdict(Counter)
    for r in resultaat.values():
        per_sectie[r["sectie"]][r["status"]] += 1
    for sectie, t in per_sectie.items():
        gemeten = t["ok"] + t["weigering"] + t["crash"]
        regels.append(f"  {sectie:24}{t['ok']:>4}/{gemeten:<4} {_pct(t['ok'], gemeten):>7}")

    for route in ("wetgeving", "hof"):
        oorzaken = Counter(r["oorzaak"] for r in resultaat.values()
                           if r["route"] == route and r["status"] in ("weigering", "crash"))
        if not oorzaken:
            continue
        regels.append("")
        regels.append(f"Oorzaken van weigering ({route}):")
        for naam, aantal in oorzaken.most_common():
            regels.append(f"  {aantal:>4}  {naam}")
            if uitgebreid:
                for ident, r in sorted(resultaat.items()):
                    if r["route"] == route and r["oorzaak"] == naam:
                        regels.append(f"          {ident}: {r['melding']}")

    probleem = False
    apart = [(i, r) for i, r in sorted(resultaat.items()) if r["status"] in ("crash", "niet-in-cache")]
    if apart:
        probleem = True
        regels.append("")
        regels.append("Crash of niet in de cache (draai `ophalen` bij het laatste):")
        for ident, r in apart:
            regels.append(f"  {ident}: {r['status']} — {r['melding']}")

    if basis is not None:
        verschil = vergelijk(basis["documenten"], resultaat)
        regels.append("")
        regels.append(f"Verschil met de basislijn ({basis.get('gemeten', '?')}, commit {basis.get('commit', '?')}):")
        if not verschil:
            regels.append("  geen")
        for soort, idents in verschil.items():
            probleem |= soort in BLOKKEREND
            regels.append(f"  {soort} ({len(idents)}):")
            for ident in idents:
                o, n = basis["documenten"].get(ident, {}), resultaat[ident]
                detail = n["oorzaak"] or ""
                if soort == "andere oorzaak van weigering":
                    detail = f"{o.get('oorzaak')} → {n['oorzaak']}"
                elif soort == "andere uitvoer":
                    detail = f"{o.get('tekens')} → {n['tekens']} tekens"
                regels.append(f"    {ident}" + (f"  ({detail})" if detail else ""))
        if verschil.get("andere uitvoer"):
            regels.append("  Vergelijk met: diff meetlat/uitvoer/basis/<naam>.md meetlat/uitvoer/laatste/<naam>.md")
    return "\n".join(regels), probleem


# ------------------------------------------------------------------ opdrachten


def _commit() -> str:
    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        vuil = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "mdconv"],
                              capture_output=True, text=True, check=True).stdout.strip()
        return rev + ("+wijzigingen" if vuil else "")
    except Exception:
        return "?"


def _kies(items, idents: list[str], sectie: str | None):
    if idents:
        bekend = {i for i, _ in items}
        onbekend = [i for i in idents if i not in bekend]
        if onbekend:
            raise SystemExit(f"niet in corpus.txt: {', '.join(onbekend)}")
        items = [(i, s) for i, s in items if i in idents]
    if sectie:
        items = [(i, s) for i, s in items if s == sectie]
    return items


def opdracht_ophalen(args, paden: Paden) -> int:
    items = _kies(lees_corpus(paden.corpus), args.idents, args.sectie)
    paden.cache.mkdir(parents=True, exist_ok=True)
    begin = time.perf_counter()
    # Vier tegelijk is genoeg; meer is vragen om een blokkade van de Cellar.
    resultaat = meet(items, paden, online=True, werkers=args.werkers or 4)
    tel = Counter(r["status"] for r in resultaat.values())
    print(f"{len(items)} documenten in {time.perf_counter() - begin:.0f} s; "
          f"cache: {sum(1 for _ in paden.cache.glob('*.zip'))} zips. Status: {dict(tel)}")
    return 1 if tel["niet-in-cache"] else 0


def opdracht_meten(args, paden: Paden) -> int:
    alle = lees_corpus(paden.corpus)
    items = _kies(alle, args.idents, args.sectie)
    laatste = paden.uitvoer / "laatste"
    begin = time.perf_counter()
    resultaat = meet(items, paden, werkers=args.werkers or min(8, os.cpu_count() or 2),
                     uitvoer=laatste)
    duur = time.perf_counter() - begin
    basis = json.loads(paden.basislijn.read_text(encoding="utf-8")) if paden.basislijn.exists() else None
    print(f"Meetlat Formex — {len(items)} documenten, taal {TAAL}, commit {_commit()} ({duur:.0f} s)\n")
    tekst, probleem = rapport(resultaat, basis, uitgebreid=args.verbose)
    print(tekst)
    (paden.uitvoer / "laatste.json").write_text(
        json.dumps(resultaat, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    if args.bijwerken:
        documenten = dict(basis["documenten"]) if basis else {}
        for ident, r in resultaat.items():
            documenten[ident] = {k: v for k, v in r.items() if k != "seconden"}
        bekend = {i for i, _ in alle}
        documenten = {i: r for i, r in documenten.items() if i in bekend}
        paden.basislijn.write_text(json.dumps(
            {"gemeten": time.strftime("%Y-%m-%d %H:%M"), "commit": _commit(), "taal": TAAL,
             "documenten": documenten},
            ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        basismap = paden.uitvoer / "basis"
        basismap.mkdir(parents=True, exist_ok=True)
        for ident, r in resultaat.items():
            naam = f"{bestandsnaam(ident)}.md"
            if r["status"] == "ok":
                shutil.copyfile(laatste / naam, basismap / naam)
            else:
                (basismap / naam).unlink(missing_ok=True)
        print(f"\nBasislijn bijgewerkt: {paden.basislijn.relative_to(ROOT)} ({len(documenten)} documenten).")
        return 1 if any(r["status"] in ("crash", "niet-in-cache") for r in resultaat.values()) else 0
    return 1 if probleem else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Meet de Formex-route op een vaste verzameling.")
    sub = parser.add_subparsers(dest="opdracht", required=True)
    for naam, hulp in (("ophalen", "download wat nog niet in de cache staat"),
                       ("meten", "meet offline en vergelijk met de basislijn")):
        p = sub.add_parser(naam, help=hulp)
        p.add_argument("idents", nargs="*", help="alleen deze documenten (uit corpus.txt)")
        p.add_argument("--sectie", help="alleen deze sectie uit corpus.txt")
        p.add_argument("--werkers", type=int, default=0, help="aantal processen")
        if naam == "meten":
            p.add_argument("-v", "--verbose", action="store_true", help="toon elke weigering")
            p.add_argument("--bijwerken", action="store_true", help="leg de uitkomst vast als basislijn")
    args = parser.parse_args(argv)
    paden = Paden()
    return opdracht_ophalen(args, paden) if args.opdracht == "ophalen" else opdracht_meten(args, paden)


if __name__ == "__main__":
    sys.exit(main())
