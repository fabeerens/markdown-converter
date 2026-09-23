"""Opdrachtregel die kennisbankbundels op schijf zet, zonder browser.

**Waarom dit bestaat.** De kennisbank meet haar keten op een uitgehouden set van
35 documenten (`~/Documents/kb/holdout/`), en die set moet één keer worden bevroren
en daarna alleen nog bewust worden aangevuld. Tot 23 september 2026 was de enige weg
naar een bundel de browser: `app.py` start Flask, en de download komt uit
`/api/download`. Vijfendertig keer klikken is geen reproduceerbare handeling, en de
`ophaal.json` die de meetlat nodig heeft (welke vraag onder welke naam is geland)
zou dan met de hand moeten worden bijgehouden.

Dit script doet precies wat de browser ook doet, en niets anders:
`sources.from_link()` → `kb_bundle.store()` → `kb_bundle.build()`, dezelfde drie
stappen als `tests/test_kb_bundle.py` via de testclient. De bundel wordt uitgepakt
zoals de kennisbank hem verwacht:

    <uit>/raw/<profiel>/<pad_id>.md
    <uit>/raw/<profiel>/<pad_id>.source.json
    <uit>/raw/source-evidence/<pad_id>/fetch.json + bronbytes

en `<uit>/ophaal.json` legt per vraag vast onder welke `pad_id` en welk profiel het
document is geland. Die koppeling is nodig omdat een geconsolideerde CELEX
(`02015R0848-20251106`) als haar basishandeling (`32015R0848`) landt
(`kb_bundle.identiteit`), en de meetlat de vraag uit `set.txt` moet kunnen
terugvinden.

    .venv/bin/python -m mdconv.kb_fetch --uit ~/Documents/kb/holdout --lijst ~/Documents/kb/holdout/set.txt
    .venv/bin/python -m mdconv.kb_fetch --uit <map> 32022L2464 BWBR0002320 ECLI:EU:C:2019:801

Eén mislukte ophaal maakt de afloopcode 1, maar de rest gaat door: de gebruiker
vervangt daarna het ene id in `set.txt` en draait alleen dat opnieuw. Dit script
importeert niets uit de kennisbank (regel 1 van `AGENTS.md`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from . import kb_bundle, sources

# Meer dan twee tegelijk is vragen om een blokkade van de Cellar of HUDOC; het
# ophalen is eenmalig, dus snelheid is hier geen doel.
_MAX_WERKERS = 2


def lees_lijst(pad: Path) -> list[str]:
    """De eerste kolom van een TSV; `#` begint commentaar, lege regels tellen niet.

    Dezelfde vorm als `holdout/set.txt` in de kennisbank: `query<TAB>profiel<TAB>
    categorie<TAB>reden`. Alleen de vraag is hier van belang; de rest is voor de
    meetlat.
    """
    vragen, gezien = [], set()
    for regel in pad.read_text(encoding="utf-8").splitlines():
        regel = regel.split("#", 1)[0].strip()
        if not regel:
            continue
        vraag = regel.split("\t", 1)[0].strip()
        if vraag in gezien:
            raise SystemExit(f"{pad}: {vraag} staat er twee keer in")
        gezien.add(vraag)
        vragen.append(vraag)
    return vragen


def _veilig_pad(uit: Path, naam: str) -> Path:
    """Een archiefnaam mag de uitvoermap niet verlaten; `kb_bundle` schrijft ze zelf,
    maar een controle hier kost niets en een `..` in een padnaam is een fout die
    stil bestanden elders zou overschrijven."""
    delen = PurePosixPath(naam).parts
    if not delen or any(deel in ("..", "") for deel in delen) or PurePosixPath(naam).is_absolute():
        raise ValueError(f"onveilige naam in de bundel: {naam!r}")
    return uit.joinpath(*delen)


def _ruim_bewijsmap_op(bewijsmap: Path, nieuwe_namen: set[str]) -> list[str]:
    """Haal losse bestanden weg die de nieuwe bundel niet meer noemt.

    Een bronbewijsmap mag nooit meer dan één `.fmx4.zip` bevatten: de zip-container
    stempelt bij een herhaalde download een nieuw tijdstip, dus dezelfde bron krijgt
    een andere hash en `structure_gate` weet dan niet welke van de twee de bron is
    (kennisbank, `references/source-structure.md`; foutlog 2026-09-22 addendum §1.G).
    Een tweede ophaal van hetzelfde document laat daarom geen weesbestand achter.
    Submappen blijven staan: daar bewaart `place_file.py` de werkartefacten van een
    eerdere plaatsing (`processing/<bodyhash>/`), en die zijn van de kennisbank.
    """
    weg = []
    if not bewijsmap.is_dir():
        return weg
    for pad in sorted(bewijsmap.iterdir()):
        if pad.is_file() and pad.name not in nieuwe_namen:
            pad.unlink()
            weg.append(pad.name)
    return weg


def zet_neer(document, uit: Path) -> dict:
    """Bouw de bundel van één omgezet document en pak hem uit onder `uit`."""
    herkomst = document.provenance
    if herkomst is None:
        raise ValueError("de bron levert geen herkomst; zonder herkomst is er geen kennisbankbundel")
    token = kb_bundle.store(herkomst.as_json())
    if token is None:
        raise ValueError("de herkomst draagt geen kennisbankidentiteit (BWB, CELEX, ECLI of slug)")
    try:
        gebouwd = kb_bundle.build(token, document.markdown, bewerkt_met_ai=False)
    finally:
        # `store()` bewaart de herkomst mét bronbytes in een tijdelijke map en ruimt
        # die pas na twee uur op, bij een volgende `store()`. Voor een browser is dat
        # goed; een opdrachtregel die 35 bundels achter elkaar bouwt zou tientallen
        # megabytes laten liggen. Dezelfde opruiming als `_sweep()`, maar meteen.
        with kb_bundle._lock:
            bewaard = kb_bundle._stores.pop(token, None)
        if bewaard is not None:
            shutil.rmtree(bewaard[0], ignore_errors=True)
    if gebouwd is None:
        raise ValueError("de bundel kon niet worden gebouwd")
    stream, pad_id = gebouwd
    with zipfile.ZipFile(stream) as archief:
        namen = archief.namelist()
        bewijsmap = uit / "raw" / "source-evidence" / pad_id
        weg = _ruim_bewijsmap_op(
            bewijsmap, {PurePosixPath(n).name for n in namen
                        if PurePosixPath(n).parent == PurePosixPath("raw/source-evidence") / pad_id})
        for naam in namen:
            doel = _veilig_pad(uit, naam)
            doel.parent.mkdir(parents=True, exist_ok=True)
            doel.write_bytes(archief.read(naam))
    profiel = next(PurePosixPath(n).parts[1] for n in namen
                   if PurePosixPath(n).parts[0] == "raw" and PurePosixPath(n).parts[1] != "source-evidence")
    fetch = json.loads((bewijsmap / "fetch.json").read_text(encoding="utf-8"))
    return {
        "pad_id": pad_id,
        "profiel": profiel,
        "fetched_at": fetch.get("fetched_at"),
        "sha256": fetch.get("sha256"),
        "source_format": fetch.get("source_format"),
        "status": "ok",
        "melding": ("; ".join(herkomst.waarschuwingen) or None),
        "opgeruimd": weg or None,
    }


def haal_op(vraag: str, uit: Path, lang: str) -> dict:
    """Eén vraag door dezelfde route als de browser; nooit een exceptie naar buiten.

    Elke fout wordt een regel in `ophaal.json` met de melding erbij. Een crash
    (iets anders dan een `ConversionError`) is erger dan een weigering en houdt
    daarom de naam van het uitzonderingstype.
    """
    from .errors import ConversionError

    begin = time.perf_counter()
    try:
        document = sources.from_link(vraag, lang)
        uitkomst = zet_neer(document, uit)
    except ConversionError as exc:
        uitkomst = {"pad_id": None, "profiel": None, "fetched_at": None, "sha256": None,
                    "source_format": None, "status": "geweigerd", "melding": str(exc)}
    except Exception as exc:  # noqa: BLE001 - de rest van de lijst moet doorgaan
        uitkomst = {"pad_id": None, "profiel": None, "fetched_at": None, "sha256": None,
                    "source_format": None, "status": "fout",
                    "melding": f"{type(exc).__name__}: {exc}"}
    uitkomst["lang"] = lang
    uitkomst["seconden"] = round(time.perf_counter() - begin, 1)
    return uitkomst


def schrijf_ophaal(pad: Path, nieuw: dict[str, dict]) -> dict[str, dict]:
    """Voeg de uitkomsten toe aan `ophaal.json`; wat er al stond en niet opnieuw is
    opgehaald blijft staan, zodat één vervangen id niet de hele koppeling wist."""
    bestaand = json.loads(pad.read_text(encoding="utf-8")) if pad.exists() else {}
    bestaand.update(nieuw)
    pad.write_text(json.dumps(bestaand, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    return bestaand


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zet kennisbankbundels op schijf, zoals de browserdownload ze levert.")
    parser.add_argument("vragen", nargs="*", help="CELEX-nummers, BWB-nummers, ECLI's of links")
    parser.add_argument("--lijst", type=Path, help="TSV met de vragen in de eerste kolom (holdout/set.txt)")
    parser.add_argument("--uit", type=Path, required=True, help="de map waar raw/ en ophaal.json komen")
    parser.add_argument("--lang", default="NL", help="taal voor EUR-Lex en HUDOC (standaard NL)")
    parser.add_argument("--werkers", type=int, default=1, help=f"tegelijk ophalen, hoogstens {_MAX_WERKERS}")
    args = parser.parse_args(argv)

    vragen = list(args.vragen)
    if args.lijst:
        vragen = lees_lijst(args.lijst) + vragen
    if not vragen:
        parser.error("geef vragen op de opdrachtregel of met --lijst")
    if len(set(vragen)) != len(vragen):
        parser.error("een vraag staat er twee keer in")
    uit = args.uit.expanduser()
    uit.mkdir(parents=True, exist_ok=True)
    werkers = max(1, min(args.werkers, _MAX_WERKERS))

    begin = time.perf_counter()
    if werkers == 1:
        uitkomsten = {vraag: haal_op(vraag, uit, args.lang) for vraag in vragen}
    else:
        with ThreadPoolExecutor(werkers) as pool:
            resultaten = pool.map(lambda v: haal_op(v, uit, args.lang), vragen)
            uitkomsten = dict(zip(vragen, resultaten))

    for vraag, r in uitkomsten.items():
        if r["status"] == "ok":
            print(f"ok         {vraag:24} → {r['profiel']}/{r['pad_id']}  ({r['source_format']}, {r['seconden']} s)")
            if r.get("opgeruimd"):
                print(f"           weesbestanden verwijderd: {', '.join(r['opgeruimd'])}")
        else:
            print(f"{r['status'].upper():10} {vraag:24} {r['melding']}")
    schrijf_ophaal(uit / "ophaal.json", uitkomsten)
    mislukt = [v for v, r in uitkomsten.items() if r["status"] != "ok"]
    print(f"\n{len(vragen) - len(mislukt)} van {len(vragen)} bundels geschreven onder {uit} "
          f"in {time.perf_counter() - begin:.0f} s; koppeling in {uit / 'ophaal.json'}.")
    if mislukt:
        print(f"Mislukt ({len(mislukt)}): {', '.join(mislukt)}. Vervang het id in de lijst en haal "
              "alleen dat opnieuw op; ophaal.json houdt de rest.")
    return 1 if mislukt else 0


if __name__ == "__main__":
    sys.exit(main())
