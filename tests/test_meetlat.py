"""De meetlat (`meetlat/meetlat.py`) meet offline en roept de echte Formex-route aan.

Deze tests houden twee dingen vast. Ten eerste dat `meten` het netwerk niet raakt
en een ontbrekend cachebestand nooit als "geen Formex" laat tellen:
`_fetch_formex` vangt netwerkfouten zelf af en valt dan terug op HTML, dus zonder
die controle zou een lege cache een schone meting lijken. Ten tweede dat de
meetlat meebreekt als iemand de functies van de route hernoemt, in plaats van
stil een andere route te meten.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from test_formex_source import ACT, formex_zip

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def meetlat():
    spec = importlib.util.spec_from_file_location("meetlat_script", ROOT / "meetlat" / "meetlat.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses zoeken hun module hier op
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("melding, verwacht", [
    ("Formex-element(en) met tekst zonder eigen behandeling: NO.GR.SEQ (14×), GENERAL (1×); "
     "omzetting geweigerd.", "element GENERAL, NO.GR.SEQ"),
    ("XML-element zonder eigen behandeling (inline:DIVISION); omzetting geweigerd.",
     "element inline:DIVISION"),
    ("Formex-bron geweigerd: een inclusie heeft het onbekende type 'TIFF'", "inclusie van type TIFF"),
    ("Formex-tekstbehoud faalt (woordmultiset verschilt; ontbreekt=['betreffende', '2016'], "
     "extra=['2016betreffende']).", "woordcontrole: woorden aan elkaar"),
    ("Formex-tekstbehoud faalt (woordmultiset verschilt; ontbreekt=['concordantietabel'], "
     "extra=niets).", "woordcontrole: tekst valt weg"),
    ("Formex-tekstbehoud faalt (woordmultiset verschilt; ontbreekt=niets, extra=['van']).",
     "woordcontrole: tekst dubbel"),
    ("Formex-structuurcontrole faalt: dubbele structurele ankers: annex-1, annex-1-8 (de bron "
     "nummert twee eenheden gelijk; alleen een herhaalde markering wordt onderscheiden)",
     "structuur: dubbele structurele ankers"),
    ("Formex-structuurcontrole faalt: artikel: bron 3, Markdown 2; lid: bron 4, Markdown 3",
     "structuur: artikel, lid"),
    ("Formex-bron geweigerd: inclusie(s) nergens in de tekst aangeroepen: L_2013162NL.01001302.xml",
     "inclusie nergens in de tekst aangeroepen"),
    ("Formex-uitspraak geweigerd: de zip bevat 2 XML-onderdelen; precies één is vereist",
     "de zip bevat # XML-onderdelen"),
])
def test_oorzaak_groepeert_wat_dezelfde_reparatie_vraagt(meetlat, melding, verwacht):
    assert meetlat.oorzaak(melding) == verwacht


def test_meten_is_offline_en_een_gemiste_cache_is_geen_schone_meting(meetlat, tmp_path, monkeypatch):
    from mdconv import net

    def geen_netwerk():
        raise AssertionError("de meetlat mag bij meten het netwerk niet raken")

    monkeypatch.setattr(net, "documents", geen_netwerk)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "32022R1925.nld.zip").write_bytes(formex_zip())
    onbekend = ACT.replace(b"<FINAL>", b"<MYSTERY>Losse tekst.</MYSTERY><FINAL>")
    (cache / "32022R0002.nld.zip").write_bytes(formex_zip(act=onbekend))
    (cache / "32022R0003.nld.http404").write_bytes(b"")
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("[proef]\n32022R1925\n32022R0002\n32022R0003\n32022R0004\n", encoding="utf-8")
    paden = meetlat.Paden(corpus=corpus, basislijn=tmp_path / "basislijn.json",
                          cache=cache, uitvoer=tmp_path / "uitvoer")

    uitkomst = meetlat.meet(meetlat.lees_corpus(corpus), paden, uitvoer=tmp_path / "uitvoer")

    assert {i: r["status"] for i, r in uitkomst.items()} == {
        "32022R1925": "ok",
        "32022R0002": "weigering",
        "32022R0003": "geen-formex",
        "32022R0004": "niet-in-cache",
    }
    assert uitkomst["32022R0002"]["oorzaak"] == "element MYSTERY"
    assert "### Artikel 1" in (tmp_path / "uitvoer" / "32022R1925.md").read_text(encoding="utf-8")
    assert net.documents is geen_netwerk  # de vervanging is na afloop teruggezet


def test_een_verslechtering_tegenover_de_basislijn_maakt_de_exitcode_1(meetlat):
    ok = {"sectie": "s", "route": "wetgeving", "status": "ok", "oorzaak": None, "melding": None,
          "sha256": "a", "tekens": 10, "waarschuwingen": []}
    geweigerd = dict(ok, status="weigering", oorzaak="element GENERAL", sha256=None, tekens=None)
    basis = {"gemeten": "x", "commit": "y", "documenten": {"A": ok, "B": ok, "C": geweigerd}}

    _, probleem = meetlat.rapport({"A": ok, "B": ok, "C": dict(ok, sha256="c")}, basis)
    assert not probleem  # alleen beter geworden

    _, probleem = meetlat.rapport({"A": geweigerd, "B": ok, "C": geweigerd}, basis)
    assert probleem  # nieuw geweigerd

    _, probleem = meetlat.rapport({"A": ok, "B": dict(ok, sha256="b"), "C": geweigerd}, basis)
    assert probleem  # andere uitvoer vraagt een bewuste --bijwerken
