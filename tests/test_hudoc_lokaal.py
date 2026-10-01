"""De lokale HUDOC-route: zelf gedownloade Word-bestanden, dezelfde bundel als online.

Sinds september 2026 houdt een Cloudflare-botcontrole de converter bij HUDOC buiten
(kennisbank, foutlog `Fouten_test_25_09.md`, T2-F5). De gebruiker downloadt de bestanden
dan in de browser, en `kb_fetch --hudoc-map` zet ze om. Hier wordt vastgehouden:

- dat de lokale route op dezelfde bytes **byte voor byte** dezelfde Markdown, hetzelfde
  bronbestand en hetzelfde zijbestand geeft als de online route, op precies de velden na
  die de herkomst eerlijk houden (wanneer en hoe de bytes binnenkwamen);
- dat een map die niet eenduidig is als geheel wordt geweigerd voordat er iets op schijf
  komt, en een document dat niet klopt als dat document;
- dat de Cloudflare-controle niet meer als verzoeklimiet wordt gemeld.

De fixture is het gouden EHRM-bestand Kagirov (`tests/fixtures/hudoc/README.md`). Geen
netwerk: `net.documents` wordt vervangen, zoals `AGENTS.md` voorschrijft.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

import pytest

from kbwortel import kb_golden
from mdconv import kb_fetch
from mdconv.errors import ConversionError
from mdconv.sources import from_link, hudoc

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "hudoc"
ITEM_ID = "001-153906"
ECLI = "ECLI:CE:ECHR:2015:0423JUD003636709"
PAD_ID = "ECLI-CE-ECHR-2015-0423JUD003636709"
# 25 september 2026, 13:03:34 UTC: de tijd waarop de browser het bestand bewaarde.
DOWNLOADTIJD = 1790341414


def _record() -> dict:
    return json.loads((FIXTURE / "hudoc-records.json").read_text(encoding="utf-8"))["results"][0]["columns"]


def _map(tmp_path: pathlib.Path, records: list[dict] | None = None,
         bestanden: dict[str, bytes] | None = None) -> pathlib.Path:
    """Een map zoals `ehrm-handmatig.sh verzamelen` hem neerzet."""
    map_ = tmp_path / "ehrm-handmatig"
    map_.mkdir()
    records = [_record()] if records is None else records
    (map_ / "hudoc-records.json").write_text(
        json.dumps({"resultcount": len(records), "results": [{"columns": r} for r in records]}),
        encoding="utf-8")
    if bestanden is None:
        bestanden = {f"{ITEM_ID}.docx": (FIXTURE / f"{ITEM_ID}.docx").read_bytes()}
    for naam, data in bestanden.items():
        (map_ / naam).write_bytes(data)
        os.utime(map_ / naam, (DOWNLOADTIJD, DOWNLOADTIJD))
    return map_


class _Antwoord:
    def __init__(self, status, data=b"", js=None, headers=None):
        self.status_code, self.content, self._js = status, data, js
        self.headers = headers or {}
        self.text, self.apparent_encoding, self.url = "", "utf-8", ""

    def json(self):
        return self._js


def _online(monkeypatch, *, zoek=None, docx=None):
    """HUDOC zoals het antwoordde vóór de botcontrole: het record, dan de DOCX."""
    calls = []
    zoek = zoek or (lambda: _Antwoord(200, js={"results": [{"columns": _record()}]}))
    docx = docx or (lambda: _Antwoord(200, (FIXTURE / f"{ITEM_ID}.docx").read_bytes()))

    def get(url, params=None, timeout=None, headers=None, allow_redirects=None):
        calls.append(url)
        return zoek() if "query/results" in url else docx()

    monkeypatch.setattr(hudoc.net, "documents", lambda: type("S", (), {"get": staticmethod(get)})())
    monkeypatch.setattr(hudoc, "_pauze", lambda s: None)
    return calls


# --------------------------------------------------------------------------
# Dezelfde bundel als online
# --------------------------------------------------------------------------

def test_de_lokale_route_geeft_byte_voor_byte_de_bundel_van_de_online_route(tmp_path, monkeypatch):
    _online(monkeypatch)
    online, lokaal = tmp_path / "online", tmp_path / "lokaal"
    assert kb_fetch.main(["--uit", str(online), ITEM_ID]) == 0
    assert kb_fetch.main(["--hudoc-map", str(_map(tmp_path)), "--uit", str(lokaal)]) == 0

    md = f"raw/jurisprudentie/{PAD_ID}.md"
    zij = f"raw/jurisprudentie/{PAD_ID}.source.json"
    bewijs = f"raw/source-evidence/{PAD_ID}"
    docx = hashlib.sha256((FIXTURE / f"{ITEM_ID}.docx").read_bytes()).hexdigest() + ".docx"
    namen = lambda wortel: sorted(str(p.relative_to(wortel)) for p in wortel.rglob("*") if p.is_file())
    assert namen(online) == namen(lokaal) == sorted(["ophaal.json", md, zij, f"{bewijs}/fetch.json",
                                                     f"{bewijs}/{docx}"])

    # De Markdown en de bronbytes: byte voor byte.
    assert (lokaal / md).read_bytes() == (online / md).read_bytes()
    assert (lokaal / bewijs / docx).read_bytes() == (online / bewijs / docx).read_bytes()

    # Het zijbestand: alleen wat zegt hoe en wanneer de bytes binnenkwamen verschilt.
    z_on = json.loads((online / zij).read_text(encoding="utf-8"))
    z_lo = json.loads((lokaal / zij).read_text(encoding="utf-8"))
    assert z_lo["requested_url"] == ("https://hudoc.echr.coe.int/app/conversion/docx/"
                                     f"?library=ECHR&id={ITEM_ID}&filename={ITEM_ID}.docx")
    assert z_on["requested_url"] == ITEM_ID
    assert z_lo["source_url"] == z_on["source_url"] == f"https://hudoc.echr.coe.int/eng?i={ITEM_ID}"
    assert z_lo["fetched_at"] == "2026-09-25T13:03:34Z"
    assert z_lo["waarschuwingen"] == z_on["waarschuwingen"] + [hudoc.HANDMATIG]
    assert z_lo["extra"].pop("handmatig") == {
        "bestand": f"{ITEM_ID}.docx", "download_url": z_lo["requested_url"],
        "records": "hudoc-records.json",
        "records_sha256": hashlib.sha256((tmp_path / "ehrm-handmatig" / "hudoc-records.json").read_bytes()).hexdigest(),
        "fetched_at_uit": "wijzigingstijd van het bestand",
    }
    for sleutel in ("requested_url", "fetched_at", "geraadpleegd", "waarschuwingen"):
        z_on.pop(sleutel), z_lo.pop(sleutel)
    assert json.dumps(z_lo, sort_keys=True) == json.dumps(z_on, sort_keys=True)

    f_on = json.loads((online / bewijs / "fetch.json").read_text(encoding="utf-8"))
    f_lo = json.loads((lokaal / bewijs / "fetch.json").read_text(encoding="utf-8"))
    for sleutel in ("requested_url", "fetched_at"):
        f_on.pop(sleutel), f_lo.pop(sleutel)
    assert f_lo == f_on

    # ophaal.json: per itemid, met de melding dat het handmatig is.
    ophaal = json.loads((lokaal / "ophaal.json").read_text(encoding="utf-8"))
    assert set(ophaal) == {ITEM_ID}
    regel = ophaal[ITEM_ID]
    assert (regel["status"], regel["pad_id"], regel["ecli"], regel["handmatig"]) == ("ok", PAD_ID, ECLI, True)
    assert "handmatig in de browser gedownload" in regel["melding"]


def test_de_lokale_route_geeft_het_gouden_bestand(tmp_path):
    """De fixture is het gouden bestand; de raw-vorm moet dus ook de gouden zijn."""
    gouden = kb_golden("jurisprudentie", PAD_ID, bestand="bron.md")
    uit = tmp_path / "uit"
    assert kb_fetch.main(["--hudoc-map", str(_map(tmp_path)), "--uit", str(uit)]) == 0
    assert (uit / f"raw/jurisprudentie/{PAD_ID}.md").read_bytes() == (gouden / "bron.md").read_bytes()


# --------------------------------------------------------------------------
# Een map die niet eenduidig is: als geheel geweigerd, niets geschreven
# --------------------------------------------------------------------------

DOCX = (FIXTURE / f"{ITEM_ID}.docx").read_bytes()


@pytest.mark.parametrize("records, bestanden, reden", [
    ([_record()], {}, f"record {ITEM_ID} heeft geen bestand {ITEM_ID}.docx"),
    ([_record()], {f"{ITEM_ID}.docx": DOCX, "001-999999.docx": DOCX},
     "bestand 001-999999.docx heeft geen record"),
    ([_record(), dict(_record(), docname="CASE OF KAGIROV v. RUSSIA (2)")], None,
     f"itemid {ITEM_ID} staat twee keer in hudoc-records.json"),
    ([dict(_record(), itemid="../elders")], None, "geen geldig itemid"),
    ([], {}, "bevat geen enkel record"),
])
def test_een_map_die_niet_klopt_wordt_als_geheel_geweigerd(tmp_path, capsys, records, bestanden, reden):
    uit = tmp_path / "uit"
    assert kb_fetch.main(["--hudoc-map", str(_map(tmp_path, records, bestanden)), "--uit", str(uit)]) == 1
    uitvoer = capsys.readouterr().out
    assert "er is niets geschreven" in uitvoer and reden in uitvoer
    assert not uit.exists()


def test_een_bestand_dat_niet_met_zijn_sha256sums_klopt_weigert_de_map(tmp_path, capsys):
    map_ = _map(tmp_path)
    (map_ / "SHA256SUMS").write_text(f"{'0' * 64}  {map_ / (ITEM_ID + '.docx')}\n"
                                     f"{'0' * 64}  {map_ / 'SHA256SUMS'}\n", encoding="utf-8")
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(tmp_path / "uit")]) == 1
    assert f"{ITEM_ID}.docx klopt niet met SHA256SUMS" in capsys.readouterr().out
    # Met de juiste som gaat hij door; de regel over SHA256SUMS zelf telt niet.
    (map_ / "SHA256SUMS").write_text(f"{hashlib.sha256(DOCX).hexdigest()}  {map_ / (ITEM_ID + '.docx')}\n"
                                     f"{'0' * 64}  {map_ / 'SHA256SUMS'}\n", encoding="utf-8")
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(tmp_path / "uit")]) == 0


def test_een_map_zonder_records_wordt_geweigerd(tmp_path, capsys):
    map_ = _map(tmp_path)
    (map_ / "hudoc-records.json").unlink()
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(tmp_path / "uit")]) == 1
    assert "hudoc-records.json ontbreekt" in capsys.readouterr().out
    (map_ / "hudoc-records.json").write_text("<html>Just a moment...</html>", encoding="utf-8")
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(tmp_path / "uit")]) == 1
    assert "geen JSON van de HUDOC-zoek-API" in capsys.readouterr().out


def test_hudoc_map_gaat_niet_samen_met_vragen(tmp_path):
    with pytest.raises(SystemExit):
        kb_fetch.main(["--hudoc-map", str(_map(tmp_path)), "--uit", str(tmp_path / "uit"), ITEM_ID])


# --------------------------------------------------------------------------
# Een document dat niet klopt: dat document geweigerd, dezelfde reden als online
# --------------------------------------------------------------------------

@pytest.mark.parametrize("record, data, reden", [
    (_record(), b"<!DOCTYPE html><title>Just a moment...</title>", "begint niet met PK"),
    (dict(_record(), doctype="HFJUD", docname="AFFAIRE KAGIROV c. RUSSIE"), DOCX, "Franstalige uitspraken"),
    (dict(_record(), doctype="HEDEC"), DOCX, "Alleen het Engelse origineel"),
    (dict(_record(), ecli=""), DOCX, "geen EHRM-ECLI"),
])
def test_een_document_dat_niet_klopt_wordt_geweigerd(tmp_path, record, data, reden):
    uit = tmp_path / "uit"
    map_ = _map(tmp_path, [record], {f"{ITEM_ID}.docx": data})
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(uit)]) == 1
    regel = json.loads((uit / "ophaal.json").read_text(encoding="utf-8"))[ITEM_ID]
    assert regel["status"] == "geweigerd" and regel["pad_id"] is None
    assert reden in regel["melding"]
    assert not (uit / "raw").exists()


def test_twee_engelse_originelen_onder_een_ecli_blijven_een_keuze(tmp_path):
    """`_kies_origineel()` geldt ook lokaal: welk van de twee het is, raadt de route niet."""
    tweede = dict(_record(), itemid="001-999999")
    map_ = _map(tmp_path, [_record(), tweede], {f"{ITEM_ID}.docx": DOCX, "001-999999.docx": DOCX})
    uit = tmp_path / "uit"
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(uit)]) == 1
    ophaal = json.loads((uit / "ophaal.json").read_text(encoding="utf-8"))
    assert {r["status"] for r in ophaal.values()} == {"geweigerd"}
    assert all("2 Engelse originelen" in r["melding"] for r in ophaal.values())


def test_de_rest_van_de_map_gaat_door_na_een_weigering(tmp_path):
    frans = dict(_record(), itemid="001-999999", doctype="HFJUD", ecli="ECLI:CE:ECHR:2015:0423JUD999999999")
    map_ = _map(tmp_path, [_record(), frans], {f"{ITEM_ID}.docx": DOCX, "001-999999.docx": DOCX})
    uit = tmp_path / "uit"
    assert kb_fetch.main(["--hudoc-map", str(map_), "--uit", str(uit)]) == 1
    ophaal = json.loads((uit / "ophaal.json").read_text(encoding="utf-8"))
    assert (ophaal[ITEM_ID]["status"], ophaal["001-999999"]["status"]) == ("ok", "geweigerd")
    assert (uit / f"raw/jurisprudentie/{PAD_ID}.md").exists()


# --------------------------------------------------------------------------
# De botcontrole is geen verzoeklimiet
# --------------------------------------------------------------------------

WACHTPAGINA = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"


@pytest.mark.parametrize("antwoord", [
    lambda: _Antwoord(403, WACHTPAGINA, headers={"Server": "cloudflare"}),
    lambda: _Antwoord(403, b"", headers={"server": "cloudflare"}),
    lambda: _Antwoord(403, WACHTPAGINA),
])
def test_de_cloudflare_controle_op_het_zoekverzoek_is_geen_verzoeklimiet(monkeypatch, antwoord):
    _online(monkeypatch, zoek=antwoord)
    with pytest.raises(ConversionError, match="geen verzoeklimiet") as fout:
        from_link(ITEM_ID)
    assert "over een minuut" not in str(fout.value)
    assert "--hudoc-map" in str(fout.value)


def test_de_cloudflare_controle_op_het_word_bestand_is_geen_verzoeklimiet(monkeypatch):
    _online(monkeypatch, docx=lambda: _Antwoord(403, WACHTPAGINA, headers={"server": "cloudflare"}))
    with pytest.raises(ConversionError, match=f"{ITEM_ID}: HUDOC laat deze client niet toe"):
        from_link(ITEM_ID)


@pytest.mark.parametrize("status", [403, 429])
def test_een_weigering_zonder_cloudflare_blijft_een_storing(monkeypatch, status):
    # Een 429 is wél een verzoeklimiet, ook met Cloudflare ervoor; een kale 403 blijft
    # de oude melding, want daar is de oorzaak niet vastgesteld.
    _online(monkeypatch, zoek=lambda: _Antwoord(status, b"", headers={"server": "cloudflare"} if status == 429 else {}))
    with pytest.raises(ConversionError, match="geen 'niet gevonden'"):
        from_link(ITEM_ID)
