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

**HUDOC uit een lokale map.** Sinds september 2026 houdt een Cloudflare-botcontrole de
Python-client van HUDOC tegen (T2-F5 in de foutlog van de kennisbank), en die wordt niet
omzeild. De gebruiker downloadt dan zelf in de browser, per arrest `<itemid>.docx` van de
DOCX-URL van `hudoc._haal_docx()` en één keer het zoekresultaat van `hudoc._zoek()` als
`hudoc-records.json`, en zet die map om:

    .venv/bin/python -m mdconv.kb_fetch --hudoc-map <map> --uit <map>

Dat gaat door dezelfde omzetting als online (`hudoc.omzetten_record()`) en dezelfde
bundel; `ophaal.json` krijgt per itemid een regel. Een map die niet klopt (een record
zonder bestand, een bestand zonder record, een itemid dat twee keer voorkomt, een bestand
dat niet met zijn `SHA256SUMS` klopt) wordt als geheel geweigerd voordat er iets op schijf
komt; een fout in één document (geen `PK`, geen HEJUD) is een weigering van dat document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from . import kb_bundle, sources
from .errors import ConversionError

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
        # Een bron zonder bronbewijs (Woo, een consultatie, de terugval van een Kamerstuk op
        # de PDF) is voor de kennisbank een weigering met reden, geen crash: `haal_op()` maakt
        # van een ConversionError `geweigerd` (besluit 2 van WP-77). De waarschuwingen van de
        # bron zeggen waarom er geen bundel is, en gaan mee in de melding.
        melding = "de bron levert geen bronbewijs; zonder bronbewijs is er geen kennisbankbundel"
        if document.warnings:
            melding += " (" + "; ".join(document.warnings) + ")"
        raise ConversionError(melding)
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


HUDOC_RECORDS = "hudoc-records.json"
_ITEM_ID = re.compile(r"00\d-\d{3,}")


class MapGeweigerd(ValueError):
    """Een HUDOC-map die als geheel niet klopt; `problemen` noemt ze allemaal tegelijk."""

    def __init__(self, problemen: list[str]):
        super().__init__("; ".join(problemen))
        self.problemen = problemen


def lees_hudoc_map(map_: Path) -> tuple[list[tuple[dict, Path]], list[dict], str]:
    """Koppel elk record uit `hudoc-records.json` aan zijn `<itemid>.docx`.

    Geeft (paren, alle records, sha256 van het recordbestand). Weigert de hele map bij
    alles wat de koppeling onzeker maakt, want een bestand dat bij het verkeerde record
    landt, landt onder de verkeerde ECLI in de kennisbank: een record zonder bestand of
    andersom, een dubbel itemid, een itemid dat geen itemid is (het wordt een bestandsnaam),
    en een bestand dat niet meer is wat `SHA256SUMS` zegt. Wat alleen één document raakt
    (de soort, `PK`) weigert `hudoc.uit_bestand()` voor dat document.
    """
    pad = map_ / HUDOC_RECORDS
    if not pad.is_file():
        raise MapGeweigerd([f"{pad} ontbreekt; bewaar het HUDOC-zoekresultaat onder die naam"])
    ruw = pad.read_bytes()
    try:
        resultaten = json.loads(ruw.decode("utf-8")).get("results")
    except (UnicodeDecodeError, ValueError, AttributeError) as exc:
        raise MapGeweigerd([f"{pad} is geen JSON van de HUDOC-zoek-API ({exc})"]) from exc
    if not isinstance(resultaten, list) or not all(
            isinstance(r, dict) and isinstance(r.get("columns"), dict) for r in resultaten):
        raise MapGeweigerd([f"{pad} heeft niet de vorm {{\"results\": [{{\"columns\": …}}]}}"])
    records = [r["columns"] for r in resultaten]
    if not records:
        # Een leeg zoekresultaat is een zoekopdracht die niets vond, geen map die klaar is.
        raise MapGeweigerd([f"{pad} bevat geen enkel record"])

    problemen = []
    per_id: dict[str, dict] = {}
    for record in records:
        item_id = record.get("itemid")
        if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id):
            problemen.append(f"een record heeft geen geldig itemid ({item_id!r})")
        elif item_id in per_id:
            problemen.append(f"itemid {item_id} staat twee keer in {HUDOC_RECORDS}")
        else:
            per_id[item_id] = record
    bestanden = {b.name[:-len(".docx")]: b for b in map_.iterdir()
                 if b.is_file() and b.name.lower().endswith(".docx")}
    for item_id in sorted(per_id.keys() - bestanden.keys()):
        problemen.append(f"record {item_id} heeft geen bestand {item_id}.docx")
    for naam in sorted(bestanden.keys() - per_id.keys()):
        problemen.append(f"bestand {bestanden[naam].name} heeft geen record in {HUDOC_RECORDS}")

    # De gebruiker legt bij het verzamelen een SHA256SUMS aan; staat een bestand erin, dan
    # moet het nog precies dat bestand zijn. Wat er niet in staat, wordt niet geraden.
    sommen = map_ / "SHA256SUMS"
    if sommen.is_file():
        for regel in sommen.read_text(encoding="utf-8").splitlines():
            delen = regel.split(maxsplit=1)
            if len(delen) != 2:
                continue
            naam = Path(delen[1].lstrip("*")).name
            doel = map_ / naam
            if naam.lower().endswith(".docx") and doel.is_file() \
                    and hashlib.sha256(doel.read_bytes()).hexdigest() != delen[0].lower():
                problemen.append(f"{naam} klopt niet met SHA256SUMS")
    if problemen:
        raise MapGeweigerd(problemen)
    paren = [(per_id[i], bestanden[i]) for i in sorted(per_id)]
    return paren, records, hashlib.sha256(ruw).hexdigest()


def zet_hudoc_neer(record: dict, bestand: Path, records: list[dict], records_sha256: str,
                   uit: Path) -> dict:
    """Eén lokaal Word-bestand door dezelfde omzetting en bundel als een online vraag."""
    begin = time.perf_counter()
    try:
        document = sources.from_hudoc_file(
            bestand.read_bytes(), record, records, bestand=bestand.name,
            downloadtijd=bestand.stat().st_mtime, records_bestand=HUDOC_RECORDS,
            records_sha256=records_sha256)
        uitkomst = zet_neer(document, uit)
    except ConversionError as exc:
        uitkomst = {"pad_id": None, "profiel": None, "fetched_at": None, "sha256": None,
                    "source_format": None, "status": "geweigerd", "melding": str(exc)}
    except Exception as exc:  # noqa: BLE001 - de rest van de map moet doorgaan
        uitkomst = {"pad_id": None, "profiel": None, "fetched_at": None, "sha256": None,
                    "source_format": None, "status": "fout",
                    "melding": f"{type(exc).__name__}: {exc}"}
    uitkomst.update(lang="EN", ecli=record.get("ecli"), bestand=bestand.name, handmatig=True,
                    seconden=round(time.perf_counter() - begin, 1))
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
    parser.add_argument("--hudoc-map", type=Path,
                        help=f"map met zelf gedownloade <itemid>.docx en {HUDOC_RECORDS} (EHRM)")
    parser.add_argument("--uit", type=Path, required=True, help="de map waar raw/ en ophaal.json komen")
    parser.add_argument("--lang", default="NL", help="taal voor EUR-Lex en HUDOC (standaard NL)")
    parser.add_argument("--werkers", type=int, default=1, help=f"tegelijk ophalen, hoogstens {_MAX_WERKERS}")
    args = parser.parse_args(argv)
    if args.hudoc_map is not None:
        if args.vragen or args.lijst:
            parser.error("--hudoc-map gaat niet samen met vragen of --lijst")
        return _main_hudoc_map(args.hudoc_map.expanduser(), args.uit.expanduser())

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


def _main_hudoc_map(map_: Path, uit: Path) -> int:
    begin = time.perf_counter()
    try:
        paren, records, records_sha256 = lees_hudoc_map(map_)
    except MapGeweigerd as exc:
        print(f"GEWEIGERD  {map_}: de map is niet eenduidig; er is niets geschreven.")
        for probleem in exc.problemen:
            print(f"           - {probleem}")
        return 1
    uit.mkdir(parents=True, exist_ok=True)
    uitkomsten = {record["itemid"]: zet_hudoc_neer(record, bestand, records, records_sha256, uit)
                  for record, bestand in paren}
    for item_id, r in uitkomsten.items():
        if r["status"] == "ok":
            print(f"ok         {item_id:12} → {r['profiel']}/{r['pad_id']}  ({r['source_format']}, {r['seconden']} s)")
            if r.get("opgeruimd"):
                print(f"           weesbestanden verwijderd: {', '.join(r['opgeruimd'])}")
        else:
            print(f"{r['status'].upper():10} {item_id:12} {r['melding']}")
    schrijf_ophaal(uit / "ophaal.json", uitkomsten)
    mislukt = [i for i, r in uitkomsten.items() if r["status"] != "ok"]
    print(f"\n{len(paren) - len(mislukt)} van {len(paren)} bundels geschreven onder {uit} uit de "
          f"handmatig gedownloade bestanden in {map_}, in {time.perf_counter() - begin:.0f} s; "
          f"koppeling in {uit / 'ophaal.json'}.")
    if mislukt:
        print(f"Mislukt ({len(mislukt)}): {', '.join(mislukt)}.")
    return 1 if mislukt else 0


if __name__ == "__main__":
    sys.exit(main())
