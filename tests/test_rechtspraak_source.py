"""De rechtspraak-XML-route: raw-vorm, bronbewijs en de weigeringen.

Geen netwerk: `net.documents` wordt vervangen, net als bij `_fake_cellar` in
`test_characterisation.py`. Het nepantwoord draagt `content` én
`apparent_encoding`, want `net.decoded_text` valt daar anders over.
"""

from __future__ import annotations

import hashlib

import pytest

from mdconv import sources
from mdconv.errors import ConversionError
from mdconv.source_structure import capture_source_documents
from mdconv.sources import rechtspraak


UITSPRAAK = """<?xml version="1.0" encoding="utf-8"?>
<open-rechtspraak>
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
           xmlns:dcterms="http://purl.org/dc/terms/" xmlns:psi="http://psi.rechtspraak.nl/">
    <rdf:Description>
      <dcterms:identifier>ECLI:NL:HR:2026:1</dcterms:identifier>
      <dcterms:title>ECLI:NL:HR:2026:1 Hoge Raad , 09-01-2026 / 25/00001</dcterms:title>
      <dcterms:language>nl</dcterms:language>
      <dcterms:creator>Hoge Raad</dcterms:creator>
      <dcterms:date>2026-01-09</dcterms:date>
      <psi:zaaknummer>25/00001</psi:zaaknummer>
      <dcterms:subject>Civiel recht</dcterms:subject>
    </rdf:Description>
  </rdf:RDF>
  <inhoudsindicatie><para>Korte samenvatting.</para></inhoudsindicatie>
  <uitspraak id="ECLI:NL:HR:2026:1:DOC" lang="nl" xml:space="preserve"
             xmlns="http://www.rechtspraak.nl/schema/rechtspraak-1.0">
    <uitspraak.info>
      <bridgehead role="bold caps">HOGE RAAD DER NEDERLANDEN</bridgehead>
      <parablock><para>Nummer<?linebreak?>   25/00001</para></parablock>
    </uitspraak.info>
    <section role="procesverloop">
      <title><nr>1</nr>De procedure</title>
      <paragroup>
        <nr>1.1</nr>
        <para>Het verloop blijkt uit de stukken.<footnote-ref linkend="_n1"/></para>
      </paragroup>
    </section>
    <section role="beslissing">
      <title><nr>2</nr>De beslissing</title>
      <informaltable>
        <tgroup cols="2">
          <colspec colname="a"/><colspec colname="b"/>
          <tbody>
            <row><entry><para>griffier</para></entry><entry><para>raadsheer</para></entry></row>
            <row><entry namest="a" nameend="b"><para>samen</para></entry></row>
          </tbody>
        </tgroup>
      </informaltable>
      <itemizedlist mark="-">
        <listitem><para>eerste punt</para></listitem>
        <listitem><para>tweede punt</para></listitem>
      </itemizedlist>
    </section>
    <footnote id="_n1" label="1"><para>HR 1 januari 2020, ECLI:NL:HR:2020:1.</para></footnote>
  </uitspraak>
</open-rechtspraak>
"""


def _fake(monkeypatch, body: str, status: int = 200):
    """Laat net.documents() één vast antwoord geven, en tel de aanroepen."""
    calls = []
    data = body.encode("utf-8")

    class FakeResp:
        status_code = status
        apparent_encoding = "utf-8"
        content = data
        text = body

    def fake_get(url, headers=None, timeout=None, params=None, allow_redirects=None):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(rechtspraak.net, "documents",
                        lambda: type("S", (), {"get": staticmethod(fake_get)})())
    return calls


def test_fetch_levert_de_rawvorm_die_het_profiel_kent(monkeypatch):
    calls = _fake(monkeypatch, UITSPRAAK)
    with capture_source_documents() as documents:
        markdown, bron, herkomst = rechtspraak.fetch("ECLI:NL:HR:2026:1")

    assert calls == ["https://data.rechtspraak.nl/uitspraken/content?id=ECLI:NL:HR:2026:1"]
    assert bron == "Rechtspraak.nl • ECLI:NL:HR:2026:1"
    regels = markdown.splitlines()

    # De H1 is de paginatitel, letterlijk - inclusief de spatie vóór de komma.
    assert regels[0] == "# ECLI:NL:HR:2026:1 Hoge Raad , 09-01-2026 / 25/00001"
    # De genummerde sectie komt als `### 1De procedure`; H6 in preclean maakt
    # daar `### 1. De procedure` van.
    assert "### 1De procedure" in regels
    # De overweging draagt haar bronnummer, zonder verzonnen punt erachter.
    assert "1.1 Het verloop blijkt uit de stukken.[^1]" in markdown
    # De nootrelatie blijft heel: marker én definitie.
    assert "[^1]: HR 1 januari 2020, ECLI:NL:HR:2020:1." in markdown
    # De tabel is een pipe-tabel; de overspannen cel staat op beide plekken.
    assert "| griffier | raadsheer |" in markdown
    assert "| samen | samen |" in markdown
    # Een opsomming houdt het teken dat de bron noemt.
    assert "- eerste punt" in markdown
    # `<?linebreak?>` wordt een vervolgregel met één spatie, geen plakwerk.
    assert "Nummer\n 25/00001" in markdown
    # De inhoudsindicatie is de samenvatting van het gerecht en geen uitspraak.
    assert "Korte samenvatting" not in markdown

    assert herkomst.format == "rechtspraak-xml"
    assert herkomst.ecli == "ECLI:NL:HR:2026:1"
    assert herkomst.celex is None and herkomst.bwb is None
    assert herkomst.extra["zaaknummer"] == "25/00001"
    assert herkomst.extra["instantie"] == "Hoge Raad"
    assert herkomst.extra["inhoudsindicatie"] == "Korte samenvatting."
    assert {"kop": "1De procedure", "niveau": 3, "rol": "procesverloop"} in herkomst.extra["secties"]

    # Het bronbewijs draagt de bytes die werkelijk zijn opgehaald.
    assert len(documents) == 1
    bewijs = documents[0]
    assert bewijs["source_format"] == "rechtspraak-xml"
    assert bewijs["media_type"] == "application/xml"
    assert bewijs["source_sha256"] == hashlib.sha256(UITSPRAAK.encode("utf-8")).hexdigest()


def test_fetch_accepteert_percent_gecodeerde_ecli_link(monkeypatch):
    calls = _fake(monkeypatch, UITSPRAAK)

    _, bron, herkomst = rechtspraak.fetch(
        "https://uitspraken.rechtspraak.nl/details?id=ECLI%3ANL%3AHR%3A2026%3A1"
    )

    assert calls == ["https://data.rechtspraak.nl/uitspraken/content?id=ECLI:NL:HR:2026:1"]
    assert bron == "Rechtspraak.nl • ECLI:NL:HR:2026:1"
    assert herkomst.ecli == "ECLI:NL:HR:2026:1"


def test_een_andere_ecli_in_de_bron_is_een_weigering(monkeypatch):
    _fake(monkeypatch, UITSPRAAK)
    with pytest.raises(ConversionError, match="noemt zichzelf"):
        rechtspraak.fetch("ECLI:NL:HR:2026:2")


def test_een_inhoudsafbeelding_wordt_weggelaten_met_waarschuwing(monkeypatch):
    body = UITSPRAAK.replace(
        "<para>Het verloop blijkt uit de stukken.",
        '<para><inlinemediaobject><imageobject><imagedata fileref="x" depth="240" '
        'width="500" format="image/png"/></imageobject></inlinemediaobject>'
        "Het verloop blijkt uit de stukken.")
    _fake(monkeypatch, body)
    document = sources.from_link("ECLI:NL:HR:2026:1")
    markdown = document.markdown
    herkomst = document.provenance

    assert "Het verloop blijkt uit de stukken." in markdown
    assert "![" not in markdown
    assert any(
        "1 inhoudsafbeelding niet overgenomen" in melding
        and "500x240 pixels" in melding
        and "bron-id x" in melding
        for melding in herkomst.waarschuwingen
    )
    assert herkomst.extra["afbeeldingen_weggelaten"] == [{
        "fileref": "x", "width": "500", "depth": 240, "format": "image/png",
    }]
    assert document.as_json()["warnings"] == list(herkomst.waarschuwingen)


def test_een_spacer_van_twee_pixels_blijft_decoratie(monkeypatch):
    body = UITSPRAAK.replace(
        "<para>Het verloop blijkt uit de stukken.",
        '<para><inlinemediaobject><imageobject><imagedata fileref="x" depth="2" '
        'width="13" format="image/png"/></imageobject></inlinemediaobject>'
        "Het verloop blijkt uit de stukken.")
    _fake(monkeypatch, body)
    markdown, _, herkomst = rechtspraak.fetch("ECLI:NL:HR:2026:1")
    assert "Het verloop blijkt uit de stukken." in markdown
    assert herkomst.extra["afbeeldingen_weggelaten"] == []
    assert not any("afbeelding" in melding for melding in herkomst.waarschuwingen)


def test_een_onbekend_element_met_tekst_wordt_geweigerd(monkeypatch):
    body = UITSPRAAK.replace("<para>eerste punt</para>",
                             "<para>eerste punt</para><verzonnen>tekst</verzonnen>")
    _fake(monkeypatch, body)
    with pytest.raises(ConversionError, match="zonder eigen behandeling"):
        rechtspraak.fetch("ECLI:NL:HR:2026:1")


def test_een_marker_zonder_noot_wordt_geweigerd(monkeypatch):
    body = UITSPRAAK.replace('linkend="_n1"', 'linkend="_bestaat-niet"')
    _fake(monkeypatch, body)
    with pytest.raises(ConversionError, match="onbekende voetnoot"):
        rechtspraak.fetch("ECLI:NL:HR:2026:1")


def test_een_ander_worteldocument_wordt_geweigerd(monkeypatch):
    _fake(monkeypatch, "<html><body>Foutpagina</body></html>")
    with pytest.raises(ConversionError, match="geen <open-rechtspraak>"):
        rechtspraak.fetch("ECLI:NL:HR:2026:1")
