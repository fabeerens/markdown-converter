"""De KOOP-route: een Kamerstuk uit de officiële XML van officielebekendmakingen.nl.

Geen netwerk: `net.documents` wordt vervangen, zoals bij `_fake_cellar` in
`test_characterisation.py`. De fixture is een klein, met de hand gebouwd stuk in de vorm van
`kst-34851-4`; het echte stuk staat als gouden voorbeeld in de kennisbank en wordt hier
als vast testbestand gelezen (gedeeld wordt alleen het bestand, nooit code).
"""

from __future__ import annotations

import io
import json
import pathlib
import zipfile

import pytest

from mdconv import kb_bundle, net
from mdconv.errors import ConversionError
from mdconv.sources import detect_source, from_link
from mdconv.sources import officiele_bekendmakingen as ob

XML = """﻿<?xml version="1.0" encoding="utf-8"?>
<officiele-publicatie><metadata><meta name="x" content="y"/></metadata>
<kamerstuk>
 <kamerstukkop><tekstregel inhoud="vergaderjaar">Vergaderjaar 2017-2018</tekstregel></kamerstukkop>
 <dossier><dossiernummer><dossiernr>34 851</dossiernr></dossiernummer><titel>Regels (Uitvoeringswet AVG)</titel></dossier>
 <stuk><stuknr>Nr. <ondernummer>4</ondernummer></stuknr><titel>ADVIES<noot id="t" type="voet"><noot.nr>1</noot.nr><noot.al>Titelnoot.</noot.al></noot></titel>
  <algemeen><vrije-tekst><tekst>
   <al>Hieronder staat het advies.</al>
   <divisie><kop><nr>1.</nr><titel>Inleiding</titel></kop>
    <al>De UZI-server<?xpp afbm?>certificaten zijn <nadruk type="cur">nodig</nadruk>.<noot id="a" type="voet"><noot.nr>2</noot.nr><noot.al>Zie de memorie.</noot.al></noot></al>
    <lijst type="expliciet" start="1" level="single" nr-sluiting=".">
     <li><li.nr>–</li.nr><al>Eerste punt.</al></li>
     <li><li.nr>2.</li.nr><al>Tweede punt.</al><lijst type="expliciet" start="1" level="single" nr-sluiting="."><li><li.nr>–</li.nr><al>Onderdeel.</al></li></lijst></li>
    </lijst>
    <divisie><kop><nr>a.</nr><titel>Onderdeel</titel></kop><al>Diep <extref soort="URL" doc="http://x">http://x/</extref> verwijs.</al></divisie>
   </divisie>
  </tekst></vrije-tekst></algemeen>
  <tekst-sluiting><ondertekening><functie>De Minister</functie> voor Rechtsbescherming,<naam><voornaam>S.</voornaam><achternaam>Dekker</achternaam></naam></ondertekening></tekst-sluiting>
  <bijlage status="goed"><kop><titel>Bijlage bij punt 7</titel></kop>
   <table><title>Overzicht</title><tgroup cols="2"><colspec colnum="1" colname="c1"/><colspec colnum="2" colname="c2"/>
    <thead><row><entry colname="c1"><al>AVG</al></entry><entry colname="c2"><al>RT<noot id="c" type="tabel"><noot.nr>1</noot.nr><noot.al>RT is rechtstreeks.</noot.al></noot></al></entry></row></thead>
    <tbody><row><entry colname="c1"><al>Artikel 9</al></entry><entry colname="c2"><al>ja</al></entry></row></tbody></tgroup></table>
  </bijlage>
 </stuk>
</kamerstuk></officiele-publicatie>
""".replace("\n", "\r\n").encode("utf-8")

METADATA = """<metadata_gegevens>
<metadata name="DC.title" scheme="" content="Regels; Advies" />
<metadata name="OVERHEIDop.documenttitel" scheme="" content="Advies Afdeling advisering" />
<metadata name="OVERHEIDop.dossiernummer" scheme="" content="34851" />
<metadata name="OVERHEIDop.dossiertitel" scheme="" content="Regels (Uitvoeringswet AVG)" />
<metadata name="OVERHEIDop.indiener" scheme="" content="S. Dekker" />
<metadata name="OVERHEIDop.indiener" scheme="" content="K.H. Ollongren" />
<metadata name="OVERHEIDop.ondernummer" scheme="" content="4" />
<metadata name="OVERHEIDop.vergaderjaar" scheme="" content="2017-2018" />
<metadata name="DCTERMS.language" scheme="DCTERMS.RFC4646" content="nl" />
<metadata name="DCTERMS.issued" scheme="DCTERMS.W3CDTF" content="2017-12-12" />
<metadata name="DC.creator" scheme="OVERHEID.StatenGeneraal" content="Tweede Kamer der Staten-Generaal" />
<metadata name="DC.identifier" scheme="OVERHEIDop.ParlID" content="kst-34851-4" />
</metadata_gegevens>""".encode("utf-8")


class _Antwoord:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class _Sessie:
    def __init__(self, antwoorden):
        self.antwoorden, self.verzoeken = antwoorden, []

    def get(self, url, timeout=None):
        self.verzoeken.append(url)
        return self.antwoorden.get(url, _Antwoord(404))


def _netwerk(monkeypatch, **antwoorden):
    sessie = _Sessie(antwoorden)
    monkeypatch.setattr(net, "documents", lambda: sessie)
    return sessie


BASIS = "https://zoek.officielebekendmakingen.nl"


def test_recognition_is_strict_and_does_not_open_links():
    assert ob.matches("kst-34851-4") and ob.matches("KST-34851-4")
    assert ob.matches("https://zoek.officielebekendmakingen.nl/kst-34851-4.html")
    assert ob.matches("https://zoek.officielebekendmakingen.nl/blg-1014762.pdf")
    assert not ob.matches("kst")
    assert not ob.matches("32016R0679") and not ob.matches("BWBR0040940") and not ob.matches("ECLI:NL:HR:2017:316")
    assert detect_source("kst-34851-4") == "officiele-bekendmakingen"
    assert detect_source("BWBR0040940") == "wetten"
    assert ob.publicatie_id("https://zoek.officielebekendmakingen.nl/kst-34851-4.html") == "kst-34851-4"


def test_conversion_writes_the_raw_form_the_documenten_profile_accepts():
    markdown, note, herkomst = ob.converteer(XML, METADATA, "kst-34851-4")
    regels = markdown.split("\n")
    # De kop van het stuk draagt zijn eigen noot; de nummering staat in de koptekst.
    assert regels[0] == "Vergaderjaar 2017-2018"
    assert "# 34 851 Regels (Uitvoeringswet AVG). Nr. 4 ADVIES[^1]" in regels
    assert "## 1. Inleiding" in regels and "### a. Onderdeel" in regels
    # Een verwerkingsinstructie midden in een woord is onzichtbaar; opmaak levert geen `*`.
    assert "De UZI-servercertificaten zijn nodig.[^2]" in regels
    assert "- Eerste punt." in regels and "2. Tweede punt." in regels and "  - Onderdeel." in regels
    assert "S. Dekker" not in markdown or "De Minister voor Rechtsbescherming, S. Dekker" in regels
    assert "## Bijlage bij punt 7" in regels
    assert "| AVG | RT[^t1-1] |" in regels and "| Artikel 9 | ja |" in regels
    assert "[^t1-1]: RT is rechtstreeks." in regels and "[^1]: Titelnoot." in regels
    assert herkomst.document_id == "kst-34851-nr-4" and herkomst.extra["noten"] == 3
    assert herkomst.extra["tabellen"] == 1 and herkomst.extra["bijlagen"] == 1
    assert note == "Officiële Bekendmakingen • kst-34851-4"


def test_fetch_takes_both_files_and_ends_up_as_a_kb_bundle(monkeypatch):
    sessie = _netwerk(monkeypatch, **{f"{BASIS}/kst-34851-4.xml": _Antwoord(200, XML),
                                      f"{BASIS}/kst-34851-4/metadata.xml": _Antwoord(200, METADATA)})
    doc = from_link("kst-34851-4")
    assert sessie.verzoeken == [f"{BASIS}/kst-34851-4.xml", f"{BASIS}/kst-34851-4/metadata.xml"]
    zij = doc.provenance.as_json()
    assert kb_bundle.identiteit(zij) == ("documenten", "kst-34851-nr-4", "kst-34851-nr-4")
    stream, pad_id = kb_bundle.build(kb_bundle.store(zij), doc.markdown, bewerkt_met_ai=False)
    with zipfile.ZipFile(stream) as archive:
        namen = archive.namelist()
        assert "raw/documenten/kst-34851-nr-4.md" in namen
        # De bron gaat byte voor byte mee, BOM en CRLF inbegrepen.
        bron = next(n for n in namen if n.endswith(".xml"))
        assert archive.read(bron) == XML
        zijbestand = json.loads(archive.read("raw/documenten/kst-34851-nr-4.source.json"))
        assert zijbestand["extra"]["metadata"]["indieners"] == ["S. Dekker", "K.H. Ollongren"]
        assert zijbestand["extra"]["metadata"]["datum"] == "2017-12-12"
        assert json.loads(archive.read("raw/source-evidence/kst-34851-nr-4/fetch.json"))["source_format"] == "op-xml"


def test_no_xml_is_a_refusal_and_never_a_fallback_to_the_pdf(monkeypatch):
    sessie = _netwerk(monkeypatch)   # alles geeft 404, zoals bij een `blg-`-bijlage
    with pytest.raises(ConversionError) as fout:
        from_link("blg-1014762")
    assert "geen officiële XML" in str(fout.value) and "geen terugval" in str(fout.value)
    assert all(not u.endswith(".pdf") for u in sessie.verzoeken)


def test_metadata_must_name_the_requested_publication(monkeypatch):
    fout_meta = METADATA.replace(b"kst-34851-4", b"kst-99999-1")
    _netwerk(monkeypatch, **{f"{BASIS}/kst-34851-4.xml": _Antwoord(200, XML),
                             f"{BASIS}/kst-34851-4/metadata.xml": _Antwoord(200, fout_meta)})
    with pytest.raises(ConversionError, match="noemt zichzelf"):
        from_link("kst-34851-4")
    # Zonder metadata is de identiteit niet vast te stellen, en die wordt niet geraden.
    _netwerk(monkeypatch, **{f"{BASIS}/kst-34851-4.xml": _Antwoord(200, XML)})
    with pytest.raises(ConversionError, match="metadata"):
        from_link("kst-34851-4")


@pytest.mark.parametrize("wijziging, reden", [
    (lambda x: x.replace(b"<al>Hieronder staat het advies.</al>", b"<al>Hieronder <blink>staat</blink> het advies.</al>"),
     "zonder eigen behandeling binnen een alinea \\(blink\\)"),
    (lambda x: x.replace(b"<tekst-sluiting>", b"<voorstel-wet><al>x</al></voorstel-wet><tekst-sluiting>"),
     "zonder eigen behandeling \\(voorstel-wet\\)"),
    (lambda x: x.replace(b'type="expliciet" start="1" level="single" nr-sluiting="."><li><li.nr>\xe2\x80\x93</li.nr><al>Onderdeel', b'type="impliciet"><li><li.nr>\xe2\x80\x93</li.nr><al>Onderdeel'),
     "alleen `expliciet`"),
    (lambda x: x.replace(b'type="tabel"', b'type="eind"'), "alleen `voet` en `tabel`"),
    (lambda x: x.replace(b"<noot.nr>2</noot.nr>", b"<noot.nr>1</noot.nr>"), "Twee noten met hetzelfde label"),
    (lambda x: x.replace(b"<kamerstuk>", b"<staatsblad>").replace(b"</kamerstuk>", b"</staatsblad>"),
     "geen enkel <kamerstuk>"),
    (lambda x: x.replace(b"<kop><nr>1.</nr><titel>Inleiding</titel></kop>", b"<kop><nr>1.</nr><label>x</label><titel>Inleiding</titel></kop>"),
     "kop met onverwachte inhoud"),
])
def test_what_the_route_does_not_know_is_refused_with_the_reason(wijziging, reden):
    with pytest.raises(ConversionError, match=reden):
        ob.converteer(wijziging(XML), METADATA, "kst-34851-4")


def test_self_check_refuses_lost_text(monkeypatch):
    oorspronkelijk = ob._Lezer.inline

    def slordig(self, el, **kw):
        t = oorspronkelijk(self, el, **kw)
        return t.replace("Tweede punt.", "Punt.")

    monkeypatch.setattr(ob._Lezer, "inline", slordig)
    with pytest.raises(ConversionError, match="mist of verdubbelt tekst"):
        ob.converteer(XML, METADATA, "kst-34851-4")


KB_GOLDEN = pathlib.Path.home() / "Documents" / "kb" / "golden" / "documenten" / "kst-34851-nr-4-xml"


@pytest.mark.skipif(not (KB_GOLDEN / "bron.xml").exists(), reason="het gouden voorbeeld van de kennisbank ontbreekt")
def test_the_real_kamerstuk_gives_exactly_the_golden_raw_form():
    """`kst-34851-4`: 85 koppen, 146 noten, twee tabellen. De kennisbank bewaart de bron en de
    raw-vorm als vast testbestand; de omzetting moet die letterlijk reproduceren."""
    zij = json.loads((KB_GOLDEN / "kst-34851-nr-4.source.json").read_text(encoding="utf-8"))
    metadata = zij["extra"]["metadata"]
    xml_metadata = "<m>" + "".join(
        f'<metadata name="{naam}" content="{waarde}"/>' for naam, waarde in (
            ("DC.identifier", "kst-34851-4"), ("OVERHEIDop.dossiernummer", metadata["dossiernummer"]),
            ("OVERHEIDop.ondernummer", metadata["ondernummer"]))) + "</m>"
    markdown, _, herkomst = ob.converteer((KB_GOLDEN / "bron.xml").read_bytes(),
                                          xml_metadata.replace("<m>", "<metadata_gegevens>").replace("</m>", "</metadata_gegevens>").encode(),
                                          "kst-34851-4")
    assert markdown == (KB_GOLDEN / "kst-34851-nr-4.raw.md").read_text(encoding="utf-8")
    assert herkomst.extra["noten"] == 146 and herkomst.extra["tabellen"] == 2
