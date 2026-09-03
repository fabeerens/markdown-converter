"""Karakteriseringstests: pinnen het gedrag van de tool vast.

Deze tests beschrijven geen wenselijk gedrag maar het *bestaande* gedrag van vóór
de herstructurering, zodat de herbouw aantoonbaar niets verandert. Ze raken geen
netwerk: alleen de pure functies (detectie, afleiding, chunking, reflow,
Formex-parsing, instellingen) en de HTTP-validatie.

De assertions zijn ongewijzigd overgenomen van de vorige structuur; alleen de
imports verwijzen nu naar de nieuwe modules. Slagen ze nog, dan is de conversie-
en opschoonlogica functioneel identiek.
"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from io import BytesIO

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Bronherkenning: detect_source precedentie
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    # EHRM-ECLI en HUDOC-links winnen van alles.
    ("ECLI:CE:ECHR:2021:0525JUD005817013", "hudoc"),
    ("https://hudoc.echr.coe.int/eng?i=001-210077", "hudoc"),
    ("001-210077", "hudoc"),
    # wetten.overheid.nl en BWB-nummers.
    ("https://wetten.overheid.nl/BWBR0040940/2021-07-01", "wetten"),
    ("BWBR0040940", "wetten"),
    # Nederlandse ECLI's en rechtspraak.nl-links.
    ("ECLI:NL:HR:2012:BQ9251", "rechtspraak"),
    ("https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2012:BQ9251", "rechtspraak"),
    # EU-ECLI, CELEX en overige input vallen door naar EUR-Lex (None).
    ("ECLI:EU:C:2025:645", None),
    ("32016R0679", None),
    ("https://eur-lex.europa.eu/eli/reg/2016/679/oj", None),
    # Nationale rechtspraak (buiten NL/EU/EHRM), per ECLI-landcode.
    ("ECLI:DE:BGH:2019:240919BVIZB39.18.0", "national"),
])
def test_detect_source(query, expected):
    from mdconv.sources import detect_source
    assert detect_source(query) == expected


def test_detect_source_hudoc_id_does_not_hijack_dutch_ecli():
    """Een ECLI met cijfergroepen mag niet als HUDOC-item-id worden gelezen."""
    from mdconv.sources import detect_source
    assert detect_source("ECLI:NL:RBAMS:2021:001-2345") == "rechtspraak"


# ---------------------------------------------------------------------------
# ELI -> CELEX afleiding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("eli,expected", [
    ("https://eur-lex.europa.eu/eli/reg/2016/679/oj", "32016R0679"),
    ("/eli/dir/2011/83", "32011L0083"),
    ("/eli/dec/2020/1", "32020D0001"),
    ("/eli/reco/2019/12", "32019H0012"),
])
def test_eli_to_celex(eli, expected):
    from mdconv.sources.eurlex import eli_to_celex
    assert eli_to_celex(eli) == expected


def test_eli_to_celex_unknown_type_is_none():
    from mdconv.sources.eurlex import eli_to_celex
    assert eli_to_celex("/eli/onbekend/2016/679") is None


def test_eli_to_celex_pads_number_to_four_digits():
    from mdconv.sources.eurlex import eli_to_celex
    assert eli_to_celex("/eli/reg/2016/7").endswith("R0007")


@pytest.mark.parametrize("text,expected", [
    ("32016R0679", "32016R0679"),
    ("https://eur-lex.europa.eu/legal-content/NL/TXT/?uri=CELEX:32016R0679", "32016R0679"),
    ("62019CJ0311", "62019CJ0311"),
    ("geen celex hier", None),
])
def test_extract_celex(text, expected):
    from mdconv.sources.eurlex import extract_celex
    assert extract_celex(text) == expected


def test_fetch_multipart_concatenates_parts_in_document_order(monkeypatch):
    """Cellar geeft 300 voor documenten die uit meerdere HTML-onderdelen bestaan
    (bv. een wetgevingsvoorstel met een losse bijlage, elk als eigen
    manifestatie) — niet alleen bij een taalprobleem, zoals eerder aangenomen.
    Elk onderdeel moet dan met Accept: text/html opgehaald worden (de
    manifestatie-URL heeft text/html als resource-mimetype; xhtml+xml geeft
    daar 406) en in documentvolgorde samengevoegd.
    """
    from mdconv.sources import eurlex

    choices_html = (
        '<html><body><ul><li title="manifestation">cellar:abc'
        '<ul><li title="item"><a href="http://publications.europa.eu/resource/cellar/abc/DOC_1">1</a></li>'
        '<li title="item"><a href="http://publications.europa.eu/resource/cellar/abc/DOC_2">2</a></li>'
        '</ul></li></ul></body></html>'
    )

    fetched_urls = []

    class FakeResp:
        def __init__(self, url):
            self.status_code = 200
            self.apparent_encoding = "utf-8"
            self.text = f"<html><body><p>Inhoud van {url.rsplit('/', 1)[-1]}</p></body></html>"

    def fake_get(url, headers=None, timeout=None):
        fetched_urls.append((url, headers["Accept"]))
        return FakeResp(url)

    monkeypatch.setattr(eurlex.net, "documents", lambda: type("S", (), {"get": staticmethod(fake_get)})())

    markdown = eurlex._fetch_multipart(choices_html, "NL", "CELEX:test")
    unescaped = markdown.replace("\\_", "_")  # markdownify escaapt underscores

    assert [u for u, _ in fetched_urls] == [
        "http://publications.europa.eu/resource/cellar/abc/DOC_1",
        "http://publications.europa.eu/resource/cellar/abc/DOC_2",
    ]
    assert all(accept == "text/html" for _, accept in fetched_urls)
    assert unescaped.index("DOC_1") < unescaped.index("DOC_2")
    assert "\n\n---\n\n" in markdown


def test_fetch_multipart_without_doc_links_reports_language_problem():
    from mdconv.sources.eurlex import _fetch_multipart
    from mdconv.errors import ConversionError
    with pytest.raises(ConversionError, match="Probeer een andere taal"):
        _fetch_multipart("<html><body>geen manifestaties hier</body></html>", "NL", "CELEX:x")


# ---------------------------------------------------------------------------
# Chunking: elke chunk moet onder de limiet blijven, ook zonder witregels
# ---------------------------------------------------------------------------

def test_split_chunks_respects_limit_with_paragraphs():
    from mdconv.cleanup.chunking import split
    text = "\n\n".join(["alinea " * 50] * 40)
    chunks = split(text, limit=1000)
    assert chunks
    assert all(len(c) <= 1000 for c in chunks)


def test_split_chunks_respects_limit_without_any_blank_lines():
    """Een PDF-conversie kan één doorlopend blok zijn; dat moet alsnog splitsen."""
    from mdconv.cleanup.chunking import split
    text = "woord " * 5000  # geen enkele witregel
    chunks = split(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_split_chunks_hard_slices_a_single_oversized_word():
    from mdconv.cleanup.chunking import split
    chunks = split("x" * 2500, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == "x" * 2500


def test_split_chunks_preserves_all_content():
    from mdconv.cleanup.chunking import split
    text = "\n\n".join(f"alinea {i} " + "tekst " * 30 for i in range(30))
    chunks = split(text, limit=800)
    assert "".join(text.split()) == "".join("".join(c.split()) for c in chunks)


def test_split_chunks_default_limit_follows_configured_chunk_tokens():
    from mdconv.cleanup import config
    assert config.get_chunk_tokens() == config.DEFAULT_CHUNK_TOKENS


def test_obsidian_profile_is_never_chunked():
    from mdconv.cleanup import chunking
    long_text = "\n\n".join(["alinea " * 200] * 200)
    assert len(chunking.chunks_for(long_text, "obsidian")) == 1
    assert len(chunking.chunks_for(long_text, "caselaw")) > 1


# ---------------------------------------------------------------------------
# PDF-reflow heuristiek
# ---------------------------------------------------------------------------

def test_reflow_joins_soft_wrapped_sentence():
    from mdconv.sources.files import reflow
    text = ("Dit is een lange regel die tegen de rechtermarge aanloopt en daarom\n"
            "doorloopt op de volgende regel binnen dezelfde alinea.")
    assert "\n" not in reflow(text).strip()


def test_reflow_keeps_paragraph_breaks():
    from mdconv.sources.files import reflow
    text = "Eerste alinea met voldoende lengte om als volle regel te gelden.\n\nTweede alinea."
    assert "\n\n" in reflow(text)


def test_reflow_never_merges_structural_lines():
    from mdconv.sources.files import reflow
    text = ("## Een kop die lang genoeg is om boven de drempel uit te komen ja\n"
            "- lijstitem dat lang genoeg is om boven de drempel uit te komen ja\n"
            "- tweede lijstitem dat ook lang genoeg is om mee te tellen hierin")
    assert reflow(text).count("\n") == 2


def test_reflow_glues_hyphenated_word_split():
    from mdconv.sources.files import reflow
    text = ("Dit is een regel die eindigt met een afgebroken woord, namelijk voor-\n"
            "beeld, en gaat daarna verder.")
    assert "voorbeeld" in reflow(text)


def test_reflow_strips_cid_artefacts():
    from mdconv.sources.files import reflow
    assert "(cid:" not in reflow("tekst (cid:123) meer tekst")


# ---------------------------------------------------------------------------
# Formex-parser
# ---------------------------------------------------------------------------

FORMEX_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<ACT>
  <TITLE><TI>VERORDENING (EU) 2016/679</TI><STI>inzake gegevensbescherming</STI></TITLE>
  <PREAMBLE>
    <VISA>Gezien het Verdrag betreffende de werking van de Europese Unie,</VISA>
    <GR.CONSID>
      <CONSID><NP><NO.P>(1)</NO.P><TXT>De bescherming van natuurlijke personen is een grondrecht.</TXT></NP></CONSID>
      <CONSID><NP><NO.P>(2)</NO.P><TXT>Dit heeft gevolgen voor de interne markt.</TXT></NP></CONSID>
    </GR.CONSID>
  </PREAMBLE>
  <ENACTING.TERMS>
    <DIVISION>
      <TITLE><TI>HOOFDSTUK I</TI><STI>Algemene bepalingen</STI></TITLE>
      <ARTICLE>
        <TI.ART>Artikel 1</TI.ART><STI.ART>Voorwerp</STI.ART>
        <PARAG><NO.PARAG>1.</NO.PARAG><ALINEA><P>Deze verordening stelt regels vast.</P></ALINEA></PARAG>
        <PARAG><NO.PARAG>2.</NO.PARAG>
          <ALINEA>
            <LIST>
              <ITEM><NP><NO.P>a)</NO.P><TXT>eerste onderdeel</TXT></NP></ITEM>
              <ITEM><NP><NO.P>b)</NO.P><TXT>tweede onderdeel</TXT></NP></ITEM>
            </LIST>
          </ALINEA>
        </PARAG>
      </ARTICLE>
    </DIVISION>
  </ENACTING.TERMS>
</ACT>
"""


DE_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<dokument>
   <doknr>TEST123</doknr>
   <ecli>ECLI:DE:BGH:2019:240919BVIZB39.18.0</ecli>
   <gertyp>BGH</gertyp>
   <spruchkoerper>6. Zivilsenat</spruchkoerper>
   <aktenzeichen>VI ZB 39/18</aktenzeichen>
   <titelzeile><dl class="RspDL"><dt/><dd><p>Testtitel van de zaak</p></dd></dl></titelzeile>
   <leitsatz>
      <div>
         <dl class="RspDL"><dt/><dd><p>1. Eerste rechtsregel met <em>nadruk</em>.</p></dd></dl>
      </div>
   </leitsatz>
   <tenor>
      <div>
         <dl class="RspDL"><dt/><dd><p>De beslissing van het hof.</p></dd></dl>
      </div>
   </tenor>
   <tatbestand/>
   <entscheidungsgruende/>
   <gruende>
      <div>
         <dl class="RspDL"><dt><a name="rd_1">1</a></dt><dd><p>Eerste overweging.</p></dd></dl>
         <dl class="RspDL"><dt/><dd><p/></dd></dl>
         <dl class="RspDL"><dt><a name="rd_2">2</a></dt><dd><p>Tweede overweging.</p></dd></dl>
         <dl class="RspDL"><dt/><dd>
            <table>
               <tr><td><p>Naam A</p></td><td><p>Naam B</p></td></tr>
            </table>
         </dd></dl>
      </div>
   </gruende>
   <abwmeinung/>
</dokument>
"""


BE_SAMPLE_HTML = """
<html><body>
<fieldset>
  <table>
    <tr><td><p>ECLI nr:</p></td><td><p>ECLI:BE:CASS:2021:ARR.20211019.2N.25</p></td></tr>
    <tr><td><p>Vervangt nummer:</p></td><td><p>ECLI:BE:CASS:2021:ARR.20211012.2N.21 </p></td></tr>
  </table>
</fieldset>
<fieldset>
  <legend title="Vonnis/arrest van 19 oktober 2021">Tekst van de beslissing</legend>
  <div>
    ERROR JUPORTARobotRecordLienECLI WARNING ECLI:BE:CASS:2021:ARR.20211019.2N.25 no lien 1 identiques <br>
    <p>
      Nr. P.21.1235.N<br>
      I.\tRECHTSPLEGING VOOR HET HOF<br>
      Het cassatieberoep is gericht tegen het arrest.<br>
      1.\tDe eiser voert een grief aan.<br>
      Dictum<br>
      Verwerpt het cassatieberoep.
    </p>
  </div>
</fieldset>
</body></html>
"""


def test_be_juportal_produces_expected_structure():
    from mdconv.sources.be_juportal import _html_to_markdown
    md, canonical_ecli = _html_to_markdown(BE_SAMPLE_HTML)
    assert canonical_ecli == "ECLI:BE:CASS:2021:ARR.20211019.2N.25"
    assert "# Vonnis/arrest van 19 oktober 2021" in md
    assert "## I. RECHTSPLEGING VOOR HET HOF" in md
    assert "1. De eiser voert een grief aan." in md  # collapse_ws normaliseert de tab naar één spatie
    assert "Dictum" in md
    assert "ERROR JUPORTA" not in md  # gelekte serverregel moet eruit gefilterd zijn


def test_be_juportal_ecli_pattern():
    from mdconv.sources.be_juportal import ECLI_RE
    assert ECLI_RE.search("ECLI:BE:CASS:2021:ARR.20211019.2N.25")
    assert not ECLI_RE.search("ECLI:DE:BGH:2019:240919BVIZB39.18.0")


def test_be_juportal_rejects_input_without_a_belgian_ecli():
    from mdconv.sources import be_juportal as be
    from mdconv.errors import ConversionError

    with pytest.raises(ConversionError, match="Belgisch ECLI-nummer"):
        be.fetch("dit is geen ECLI")


def test_be_juportal_reports_a_clear_error_on_http_400(monkeypatch):
    from mdconv.sources import be_juportal as be
    from mdconv.errors import ConversionError

    class FakeResp:
        status_code = 400

    monkeypatch.setattr(be.net, "documents",
                         lambda: type("S", (), {"get": staticmethod(lambda *a, **k: FakeResp())})())
    with pytest.raises(ConversionError, match="Juportal"):
        be.fetch("ECLI:BE:CASS:2099:ARR.99999999.9N.99")


def test_fr_cc_derives_the_url_from_the_ecli():
    """ECLI:FR:CC:{jaar}:{jaar}.{nummer}.{type} -> .../decision/{jaar}/{jaar}{nummer}{type}.htm
    (het jaar is het 4e ECLI-onderdeel, de rest is het 5e onderdeel zonder punten) —
    geverifieerd tegen echte pagina's op conseil-constitutionnel.fr (QPC en DC)."""
    from mdconv.sources.fr_conseil_constitutionnel import _CC_ECLI_RE

    m = _CC_ECLI_RE.search("ECLI:FR:CC:2021:2021.931.QPC")
    assert m.group(1) == "2021"
    assert m.group(2).replace(".", "") == "2021931QPC"


def test_fr_cc_html_to_markdown():
    from mdconv.sources.fr_conseil_constitutionnel import _html_to_markdown

    html = """
    <html><body>
    <h1 class="title">Décision n° 2021-931 QPC du 23 septembre 2021</h1>
    <div class="field field--name-field-titre-complet field__item"><p>Une affaire</p></div>
    <div class="field field--name-field-contenu-original field__item">
      <p class="considerant"><span class="numero-considerant">1.</span> Premier considérant.</p>
      <blockquote><p><strong>LE CONSEIL CONSTITUTIONNEL DÉCIDE&nbsp;:</strong></p></blockquote>
    </div>
    </body></html>
    """
    md = _html_to_markdown(html)
    assert "# Décision n° 2021-931 QPC du 23 septembre 2021" in md
    assert "## Une affaire" in md
    assert "1. Premier considérant." in md
    assert "LE CONSEIL CONSTITUTIONNEL DÉCIDE" in md


def test_fr_cc_rejects_other_french_courts_with_a_clear_message():
    """Alleen ECLI:FR:CC:... en ECLI:FR:CCASS:... worden ondersteund; overige
    Franse gerechten (Conseil d'État, cours d'appel) zitten achter een
    Légifrance-bot-blokkade — geen stille misser, maar een uitleg waarom."""
    from mdconv.sources import fr_conseil_constitutionnel as fr
    from mdconv.errors import ConversionError

    with pytest.raises(ConversionError, match="Conseil constitutionnel"):
        fr.fetch("ECLI:FR:CE:2021:12345")


def test_fr_dispatches_ccass_ecli_to_judilibre(monkeypatch):
    from mdconv.sources import fr_conseil_constitutionnel as fr
    from mdconv.sources import fr_judilibre

    called = {}
    monkeypatch.setattr(
        fr_judilibre, "fetch", lambda q: called.setdefault("query", q) and ("md", "bron")
    )
    result = fr.fetch("ECLI:FR:CCASS:2019:C100589")
    assert result == ("md", "bron")
    assert called["query"] == "ECLI:FR:CCASS:2019:C100589"


def test_fr_cc_rejects_input_without_a_french_ecli():
    from mdconv.sources import fr_conseil_constitutionnel as fr
    from mdconv.errors import ConversionError

    with pytest.raises(ConversionError, match="Frans ECLI-nummer"):
        fr.fetch("dit is geen ECLI")


def test_de_rechtsprechung_produces_expected_structure():
    from mdconv.sources.de_rechtsprechung import _xml_to_markdown
    md = _xml_to_markdown(DE_SAMPLE)
    assert "# BGH 6. Zivilsenat — VI ZB 39/18" in md
    assert "## Testtitel van de zaak" in md
    assert "## Leitsatz" in md
    assert "1. Eerste rechtsregel met *nadruk*." in md
    assert "## Tenor" in md
    assert "De beslissing van het hof." in md
    assert "## Gründe" in md
    assert "1. Eerste overweging." in md
    assert "2. Tweede overweging." in md
    assert "| Naam A | Naam B |" in md
    # tatbestand/entscheidungsgruende zijn leeg: geen lege koppen in de uitvoer.
    assert "Tatbestand" not in md
    assert "Entscheidungsgründe" not in md


def test_de_rechtsprechung_ecli_pattern():
    from mdconv.sources.de_rechtsprechung import ECLI_RE
    assert ECLI_RE.search("ECLI:DE:BGH:2019:240919BVIZB39.18.0")
    assert not ECLI_RE.search("ECLI:NL:HR:2012:BQ9251")


def test_de_rechtsprechung_reports_a_clear_error_when_not_found(monkeypatch):
    from mdconv.sources import de_rechtsprechung as de
    from mdconv.errors import ConversionError

    monkeypatch.setattr(de, "_resolve_doc_id", lambda ecli: None)
    with pytest.raises(ConversionError, match="rechtsprechung-im-internet.de"):
        de.fetch("ECLI:DE:BGH:1999:999999ZZZZ99.99.9")


def test_de_rechtsprechung_distinguishes_zero_results_from_a_glitch(monkeypatch):
    """Een echte '0 Treffer' geeft direct None; een technische hapering
    (bv. een ontbrekend formulierveld, zonder die expliciete melding) wordt
    eerst opnieuw geprobeerd voordat de conclusie 'niet gevonden' wordt."""
    from mdconv.sources import de_rechtsprechung as de

    calls = []

    def fake_search_once(ecli):
        calls.append(ecli)
        return de._RETRY if len(calls) == 1 else "jb-GEVONDEN"

    monkeypatch.setattr(de, "_search_once", fake_search_once)
    assert de._resolve_doc_id("ECLI:DE:BGH:2019:X") == "jb-GEVONDEN"
    assert len(calls) == 2  # eerste poging was een glitch, tweede vond het


def test_de_rechtsprechung_zero_results_does_not_retry(monkeypatch):
    from mdconv.sources import de_rechtsprechung as de

    calls = []

    def fake_search_once(ecli):
        calls.append(ecli)
        return None  # echte "0 Treffer"

    monkeypatch.setattr(de, "_search_once", fake_search_once)
    assert de._resolve_doc_id("ECLI:DE:BGH:2019:X") is None
    assert len(calls) == 1  # geen zinloze herhaling bij een bevestigd lege uitkomst


def test_de_rechtsprechung_reports_connection_errors_distinctly(monkeypatch):
    from mdconv.sources import de_rechtsprechung as de
    from mdconv.errors import ConversionError
    import requests

    def fake_search_once(ecli):
        raise requests.exceptions.ConnectionError("netwerk onbereikbaar")

    monkeypatch.setattr(de, "_search_once", fake_search_once)
    with pytest.raises(ConversionError, match="verbindingsfout"):
        de._resolve_doc_id("ECLI:DE:BGH:2019:X")


def test_de_rechtsprechung_rejects_input_without_a_german_ecli():
    from mdconv.sources import de_rechtsprechung as de
    from mdconv.errors import ConversionError

    with pytest.raises(ConversionError, match="Duits ECLI-nummer"):
        de.fetch("dit is geen ECLI")


OL_RSPDL_CONTENT = """
<h2>Tenor</h2>
<div>
  <dl class="RspDL"><dt/><dd><p>De beslissing.</p></dd></dl>
</div>
<h2>Gründe</h2>
<div>
  <dl class="RspDL"><dt><a name="rd_1">1</a></dt><dd><p>Eerste overweging.</p></dd></dl>
</div>
"""

OL_ABSATZ_CONTENT = """
<h2>Tenor</h2>
<p>De beslissing van het hof.</p>
<span class="absatzRechts">1</span><p class="absatzLinks"><span style="text-decoration: underline;">Gründe:</span></p>
<span class="absatzRechts">2</span><p class="absatzLinks">Eerste overweging.</p>
"""

OL_UNKNOWN_CONTENT = """
<h2>Tenor</h2>
<p>Alleen platte alinea's, geen bekend patroon.</p>
<p>Nog een alinea.</p>
"""


def test_de_openlegaldata_renders_rspdl_content():
    from mdconv.sources.de_openlegaldata import _content_to_markdown
    md = _content_to_markdown(OL_RSPDL_CONTENT)
    assert "## Tenor" in md
    assert "De beslissing." in md
    assert "## Gründe" in md
    assert "1. Eerste overweging." in md


def test_de_openlegaldata_merges_absatz_pairs():
    """Deelstaatconventie (NRW): het randnummer staat als losse <span> náást
    de alinea, niet erin — moet alsnog samengevoegd worden tot 'N. tekst'."""
    from mdconv.sources.de_openlegaldata import _content_to_markdown
    md = _content_to_markdown(OL_ABSATZ_CONTENT)
    assert "1. <u>Gründe:</u>" in md
    assert "2. Eerste overweging." in md


def test_de_openlegaldata_falls_back_to_generic_markdown_for_unknown_structure():
    from mdconv.sources.de_openlegaldata import _content_to_markdown
    md = _content_to_markdown(OL_UNKNOWN_CONTENT)
    assert "Alleen platte alinea's, geen bekend patroon." in md
    assert "Nog een alinea." in md


def test_de_openlegaldata_ecli_pattern_matches_de_rechtsprechung():
    """Beide DE-modules moeten hetzelfde ECLI-patroon herkennen (de_openlegaldata
    hergebruikt de_rechtsprechung.ECLI_RE bewust, i.p.v. het te dupliceren)."""
    from mdconv.sources import de_openlegaldata as ol
    from mdconv.sources import de_rechtsprechung as de
    assert ol.ECLI_RE is de.ECLI_RE


def test_de_openlegaldata_falls_back_to_de_rechtsprechung_when_not_found(monkeypatch):
    from mdconv.sources import de_openlegaldata as ol

    monkeypatch.setattr(ol, "_find_case", lambda ecli: None)
    called = {}

    def fake_fallback(query):
        called["query"] = query
        return "terugval-tekst", "terugval-bron"

    monkeypatch.setattr(ol.de_rechtsprechung, "fetch", fake_fallback)
    result = ol.fetch("ECLI:DE:BGH:2019:240919BVIZB39.18.0")
    assert result == ("terugval-tekst", "terugval-bron")
    assert called["query"] == "ECLI:DE:BGH:2019:240919BVIZB39.18.0"


def test_de_openlegaldata_derives_court_from_ecli_when_metadata_is_unhelpful():
    """Bekend gegevenskwaliteitsgat bij OpenLegalData: court.name = "Unknown
    court" voor sommige (vooral oudere) zaken, terwijl het gerecht wél in de
    ECLI zelf staat."""
    from mdconv.sources.de_openlegaldata import _case_to_markdown
    case = {
        "court": {"name": "Unknown court"},
        "file_number": "VI ZB 39/18",
        "ecli": "ECLI:DE:BGH:2019:240919BVIZB39.18.0",
        "content": "",
    }
    md = _case_to_markdown(case)
    assert md.startswith("# BGH VI ZB 39/18")


def test_juris_markup_has_rspdl_structure():
    from mdconv.sources.juris_markup import has_rspdl_structure
    assert has_rspdl_structure('<dl class="RspDL">')
    assert not has_rspdl_structure("<p>gewone tekst</p>")


def test_formex_produces_expected_structure():
    from mdconv.sources.formex import convert_formex
    md = convert_formex(FORMEX_SAMPLE)
    assert "# VERORDENING (EU) 2016/679" in md
    assert "## inzake gegevensbescherming" in md
    assert "(1) De bescherming van natuurlijke personen is een grondrecht." in md
    assert "### Artikel 1 — Voorwerp" in md
    assert "1.  Deze verordening stelt regels vast." in md
    assert "- a) eerste onderdeel" in md
    assert "- b) tweede onderdeel" in md


def test_formex_recitals_are_not_headings():
    """Genummerde overwegingen blijven alinea's (uitdrukkelijke wens)."""
    from mdconv.sources.formex import convert_formex
    for line in convert_formex(FORMEX_SAMPLE).splitlines():
        if line.startswith("#"):
            assert "(1)" not in line and "(2)" not in line


def test_formex_inline_formatting_and_footnotes():
    from mdconv.sources.formex import convert_formex
    xml = (b'<ACT><ENACTING.TERMS><P>Gewoon <HT TYPE="BOLD">vet</HT> en '
           b'<HT TYPE="ITALIC">cursief</HT>.<NOTE NOTE.ID="1">De voetnoot.</NOTE></P>'
           b'</ENACTING.TERMS></ACT>')
    md = convert_formex(xml)
    assert "**vet**" in md
    assert "*cursief*" in md
    assert "[^1]" in md
    assert "[^1]: De voetnoot." in md


def test_formex_table_becomes_markdown_table():
    from mdconv.sources.formex import convert_formex
    xml = (b"<ACT><ENACTING.TERMS><TBL><CORPUS>"
           b"<ROW><CELL>Kop A</CELL><CELL>Kop B</CELL></ROW>"
           b"<ROW><CELL>a1</CELL><CELL>b1</CELL></ROW>"
           b"</CORPUS></TBL></ENACTING.TERMS></ACT>")
    md = convert_formex(xml)
    assert "| Kop A | Kop B |" in md
    assert "| --- | --- |" in md
    assert "| a1 | b1 |" in md


def test_formex_falls_back_to_plain_text_when_structure_is_unknown():
    from mdconv.sources.formex import convert_formex
    xml = (b"<ONBEKEND><RARE>Dit is losse tekst die toch zichtbaar moet blijven "
           b"ook al kent de parser deze structuur niet.</RARE></ONBEKEND>")
    assert "losse tekst die toch zichtbaar moet blijven" in convert_formex(xml)


def test_formex_footnotes_are_isolated_per_conversion():
    """Voetnoten mogen niet lekken tussen conversies."""
    from mdconv.sources.formex import convert_formex
    convert_formex(b'<ACT><P>A<NOTE NOTE.ID="1">nootA</NOTE></P></ACT>')
    md_b = convert_formex(b'<ACT><P>B<NOTE NOTE.ID="1">nootB</NOTE></P></ACT>')
    assert "nootB" in md_b
    assert "nootA" not in md_b


def test_formex_footnotes_survive_concurrent_conversions():
    """Twee documenten tegelijk omzetten mag hun voetnoten niet vermengen.

    Dit faalde in de vorige opzet: `_CURRENT_CTX` was een module-level stack, dus
    gelijktijdige conversies pikten elkaars voetnoten op. Flask draait lokaal met
    threads en documenten worden parallel geüpload, dus dat was echt bereikbaar.
    """
    import re
    import threading
    from mdconv.sources.formex import convert_formex

    def document(tag: str, n: int = 12) -> bytes:
        body = "".join(
            f'<P>{tag}{i} <NOTE NOTE.ID="{tag}{i}">noot-{tag}{i}</NOTE></P>' for i in range(n)
        )
        return f"<ACT><ENACTING.TERMS>{body}</ENACTING.TERMS></ACT>".encode()

    problems: list[str] = []

    def worker(tag: str):
        for _ in range(30):
            md = convert_formex(document(tag))
            foreign = [d for d in re.findall(r"\[\^(\w+)\]:", md) if not d.startswith(tag)]
            missing = [f"{tag}{i}" for i in range(12) if f"[^{tag}{i}]:" not in md]
            if foreign or missing:
                problems.append(f"{tag}: vreemd={foreign[:3]} ontbreekt={missing[:3]}")
                return

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C", "D")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not problems, "voetnoten lekten tussen gelijktijdige conversies: " + "; ".join(problems)


# ---------------------------------------------------------------------------
# Opschoon-profielen en fence-stripping
# ---------------------------------------------------------------------------

def test_profiles_exist_and_obsidian_runs_unsplit():
    from mdconv.cleanup import config, prompts
    assert set(prompts.DEFAULTS) == {"generic", "caselaw", "obsidian", "translate_nl"}
    assert "obsidian" in config.NO_CHUNK_PROFILES


def test_translate_nl_prompt_is_chunked_and_translation_only():
    from mdconv.cleanup import config
    assert "translate_nl" not in config.NO_CHUNK_PROFILES
    prompt = config.get_prompt("translate_nl")
    assert "dutch" in prompt.lower()
    assert "translate" in prompt.lower()


def test_translate_nl_has_its_own_user_prompt_template():
    from mdconv.cleanup import prompts
    assert "translate_nl" in prompts.USER_PROMPTS
    assert "{chunk}" in prompts.USER_PROMPTS["translate_nl"]


def test_caselaw_prompt_forbids_promoting_paragraph_numbers():
    from mdconv.cleanup.config import get_prompt
    prompt = get_prompt("caselaw")
    assert "never turn" in prompt.lower() or "never promote" in prompt.lower()
    assert "##" in prompt


def test_default_model_keeps_its_leading_tilde():
    from mdconv.cleanup.config import DEFAULT_MODEL
    assert DEFAULT_MODEL.startswith("~"), "de tilde is de OpenRouter latest-alias"


def test_strip_markdown_fence():
    from mdconv.cleanup.openrouter import strip_markdown_fence
    assert strip_markdown_fence("```markdown\n# Titel\n\ntekst\n```") == "# Titel\n\ntekst"
    assert strip_markdown_fence("# Titel\n\ntekst") == "# Titel\n\ntekst"


@pytest.mark.parametrize("piece_size,expected", [
    (1000, "# Titel\n\nEen langere alinea met genoeg tekst voor de holdback-buffer."),
    (1, "# Titel\n\nEen langere alinea met genoeg tekst voor de holdback-buffer."),  # per karakter
])
def test_strip_fence_stream_matches_the_non_streaming_version(piece_size, expected):
    """De streaming-fence-stripper moet, ongeacht hoe klein de binnenkomende
    stukjes zijn (tot en met één losse letter), hetzelfde resultaat geven als
    de niet-streaming versie — anders lekt het codeblok van het obsidian-
    profiel even mee in de live-weergave."""
    from mdconv.cleanup.openrouter import strip_fence_stream

    raw = "```markdown\n" + expected + "\n```"

    def pieces():
        for i in range(0, len(raw), piece_size):
            yield raw[i:i + piece_size]

    assert "".join(strip_fence_stream(pieces())) == expected


def test_strip_fence_stream_without_a_fence_passes_text_through():
    from mdconv.cleanup.openrouter import strip_fence_stream
    assert "".join(strip_fence_stream(iter(["gewone tekst zonder fence"]))) == "gewone tekst zonder fence"


def test_base_url_strips_chat_completions_suffix(monkeypatch):
    from mdconv.cleanup import config
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
    assert config.base_url() == "https://openrouter.ai/api/v1"


def test_base_url_empty_env_falls_back_to_default(monkeypatch):
    from mdconv.cleanup import config
    monkeypatch.setenv("OPENROUTER_BASE_URL", "")
    assert config.base_url() == config.DEFAULT_BASE_URL


def test_estimate_counts_input_without_network(monkeypatch):
    import mdconv.cleanup as cleanup
    from mdconv.cleanup import config
    monkeypatch.setattr(config, "is_available", lambda: False)
    est = cleanup.estimate("regel\n\n" * 100, profile="generic")
    assert est["chunks"] == 1
    assert est["input_tokens"] > 0
    assert est["cost_usd"] is None


def test_estimate_obsidian_expects_more_output_than_input(monkeypatch):
    import mdconv.cleanup as cleanup
    from mdconv.cleanup import config
    monkeypatch.setattr(config, "is_available", lambda: False)
    text = "tekst " * 2000
    generic = cleanup.estimate(text, profile="generic")
    obsidian = cleanup.estimate(text, profile="obsidian")
    assert obsidian["output_tokens"] > generic["output_tokens"]


def test_estimate_uses_the_per_model_chunk_size(isolated_settings, monkeypatch):
    """Twee endpoints met een verschillende deelgrootte moeten een verschillend
    aantal delen opleveren voor exact dezelfde tekst — dat is het hele punt van
    een per-endpoint instelling i.p.v. één centrale deelgrootte."""
    import mdconv.cleanup as cleanup
    cfg = isolated_settings
    monkeypatch.setattr(cfg, "is_available", lambda: False)
    cfg.update_settings({"models": [
        {"id": "klein/model", "label": "Klein", "chunk_tokens": cfg.MIN_CHUNK_TOKENS},
        {"id": "groot/model", "label": "Groot", "chunk_tokens": cfg.MAX_CHUNK_TOKENS},
    ]})

    text = "tekst " * 20000
    small = cleanup.estimate(text, profile="generic", model="klein/model")
    large = cleanup.estimate(text, profile="generic", model="groot/model")
    assert small["chunks"] > large["chunks"]


def test_pricing_lookup_matches_model_id_without_nitro_suffix(monkeypatch):
    """:nitro bestaat niet als los item in de OpenRouter-catalogus."""
    from mdconv.cleanup import openrouter

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "anthropic/claude-haiku-4.5",
                              "pricing": {"prompt": "0.000001", "completion": "0.000005"}}]}

    class FakeSession:
        @staticmethod
        def get(*_a, **_k):
            return FakeResp()

    monkeypatch.setattr(openrouter.net, "llm", lambda: FakeSession())
    openrouter.clear_pricing_cache()
    pricing = openrouter.get_pricing("anthropic/claude-haiku-4.5:nitro")
    assert pricing is not None and pricing["completion"] == 0.000005
    openrouter.clear_pricing_cache()


def test_clean_chunk_rejects_a_truncated_response(monkeypatch):
    """finish_reason='length' betekent dat het model afkapte vóórdat de tekst
    klaar was. Zonder deze check kwam er stilletjes afgekapte tekst terug —
    bij het obsidian-profiel (dat nooit chunkt) verdween daardoor het laatste
    deel van een groot document zonder enige melding."""
    from mdconv.cleanup import openrouter
    from mdconv.errors import ConversionError

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "afgekapte tekst..."},
                                  "finish_reason": "length"}]}

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeResp())})())

    with pytest.raises(ConversionError, match="afgekapt"):
        openrouter.clean_chunk("tekst", model="x", system="y", profile="generic")

    with pytest.raises(ConversionError, match="Opmaken voor Obsidian"):
        openrouter.clean_chunk("tekst", model="x", system="y", profile="obsidian")


def test_stream_chunk_rejects_a_truncated_response(monkeypatch):
    from mdconv.cleanup import openrouter
    from mdconv.errors import ConversionError

    class FakeStreamResp:
        status_code = 200
        close = staticmethod(lambda: None)

        @staticmethod
        def iter_lines(decode_unicode=True):
            return iter([
                'data: {"choices":[{"delta":{"content":"Dit is een stuk"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
                "data: [DONE]",
            ])

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeStreamResp())})())

    with pytest.raises(ConversionError, match="afgekapt"):
        list(openrouter.stream_chunk("tekst", model="x", system="y", profile="generic"))


def test_clean_chunk_returns_usage_alongside_the_text(monkeypatch):
    from mdconv.cleanup import openrouter

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": "opgeschoonde tekst"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.002},
            }

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeResp())})())

    content, usage = openrouter.clean_chunk("tekst", model="x", system="y", profile="generic")
    assert content == "opgeschoonde tekst"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.002}


def test_stream_chunk_yields_a_usage_marker_from_the_final_sse_line(monkeypatch):
    from mdconv.cleanup import openrouter

    class FakeStreamResp:
        status_code = 200
        close = staticmethod(lambda: None)

        @staticmethod
        def iter_lines(decode_unicode=True):
            return iter([
                'data: {"choices":[{"delta":{"content":"stuk 1"}}]}',
                'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5,'
                '"total_tokens":15,"cost":0.002}}',
                "data: [DONE]",
            ])

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeStreamResp())})())

    items = list(openrouter.stream_chunk("tekst", model="x", system="y", profile="generic"))
    text = [i for i in items if isinstance(i, str)]
    usages = [i for i in items if isinstance(i, openrouter.Usage)]
    assert text == ["stuk 1"]
    assert len(usages) == 1
    assert usages[0]["total_tokens"] == 15


def test_stream_chunk_stops_silently_when_cancelled(monkeypatch):
    """Annuleren mag geen ConversionError geven — dat zou als foutmelding in
    de UI belanden, terwijl de gebruiker het zelf heeft stopgezet."""
    from mdconv.cleanup import cancel, openrouter

    class FakeStreamResp:
        status_code = 200
        close = staticmethod(lambda: None)

        @staticmethod
        def iter_lines(decode_unicode=True):
            return iter([
                'data: {"choices":[{"delta":{"content":"stuk 1"}}]}',
                'data: {"choices":[{"delta":{"content":"stuk 2"}}]}',
            ])

    monkeypatch.setattr(openrouter.config, "api_key", lambda: "sk-test")
    monkeypatch.setattr(openrouter.net, "llm",
                         lambda: type("S", (), {"post": staticmethod(lambda *a, **k: FakeStreamResp())})())

    cancel.request("req-1")
    try:
        items = list(
            openrouter.stream_chunk("tekst", model="x", system="y", profile="generic", request_id="req-1")
        )
    finally:
        cancel.clear("req-1")
    assert items == []


def test_clean_stream_yields_progress_and_a_final_usage_marker(monkeypatch):
    import mdconv.cleanup as cleanup
    from mdconv.cleanup import config, openrouter

    monkeypatch.setattr(config, "is_available", lambda: True)
    monkeypatch.setattr(config, "get_chunk_tokens", lambda model=None: 5_000)

    def fake_stream_chunk(chunk, *, model, system, profile, request_id=None):
        yield "a" * 500
        yield openrouter.Usage({"prompt_tokens": 10, "completion_tokens": 20,
                                 "total_tokens": 30, "cost": 0.001})

    monkeypatch.setattr(openrouter, "stream_chunk", fake_stream_chunk)

    items = list(cleanup.clean_stream("brontekst " * 50, profile="generic"))
    progress = [i for i in items if isinstance(i, cleanup.Progress)]
    usage = [i for i in items if isinstance(i, cleanup.Usage)]
    assert progress, "geen enkele voortgangsmarker ontvangen"
    assert progress[0]["expected_tokens"] > 0
    assert usage == [{"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": 0.001}]


def test_clean_stream_stops_when_cancelled_between_chunks(monkeypatch):
    import mdconv.cleanup as cleanup
    from mdconv.cleanup import cancel, config

    monkeypatch.setattr(config, "is_available", lambda: True)

    def fake_stream_chunk(chunk, *, model, system, profile, request_id=None):
        cancel.request(request_id)
        yield "nooit gezien na annuleren"

    monkeypatch.setattr(cleanup.openrouter, "stream_chunk", fake_stream_chunk)

    items = list(cleanup.clean_stream("tekst deel een\n\ntekst deel twee", profile="generic",
                                       request_id="req-2"))
    # Het eerste deel loopt nog (de annulering wordt pas vóór het volgende
    # deel gecheckt), maar er komt geen Usage-marker na een annulering.
    assert not any(isinstance(i, cleanup.Usage) for i in items)
    assert not cancel.is_cancelled("req-2")  # clean_stream ruimt zelf op


def test_clean_cancel_endpoint_marks_the_request(client):
    from mdconv.cleanup import cancel

    r = client.post("/api/clean/cancel", json={"request_id": "abc-123"})
    assert r.status_code == 200
    assert cancel.is_cancelled("abc-123")
    cancel.clear("abc-123")


def test_stream_frame_format_is_null_delimited_json():
    """CLEAN_PROGRESS/CLEAN_USAGE-frames zijn \\x00CLEAN_<KIND>\\x00<json>\\x00
    — precies wat de front-end parser (makeStreamParser in app.js) verwacht."""
    import mdconv.api as api_module

    frame = api_module._frame("PROGRESS", {"produced_tokens": 1, "expected_tokens": 2})
    assert frame == '\x00CLEAN_PROGRESS\x00{"produced_tokens": 1, "expected_tokens": 2}\x00'


# ---------------------------------------------------------------------------
# Instellingen: leeg/ongeldig wist terug naar de standaardwaarde
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """Laat de instellingen naar een tijdelijke map schrijven i.p.v. .deploy-state."""
    from mdconv.cleanup import config
    from mdconv.state import StateFile

    monkeypatch.setattr("mdconv.state.STATE_DIR", str(tmp_path))
    store = StateFile("settings.json")
    monkeypatch.setattr(config, "_store", store)
    return config


def test_settings_defaults_when_nothing_stored(isolated_settings):
    cfg = isolated_settings
    assert cfg.get_chunk_tokens() == cfg.DEFAULT_CHUNK_TOKENS
    assert cfg.get_model_choices() == cfg.DEFAULT_MODEL_CHOICES
    from mdconv.cleanup import prompts
    assert cfg.get_prompt("generic") == prompts.DEFAULTS["generic"]


def test_settings_roundtrip_and_take_effect_immediately(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({
        "models": [{"id": "x/y", "label": "X", "chunk_tokens": 30000}],
        "prompts": {"generic": "eigen prompt"},
    })
    assert cfg.get_chunk_tokens("x/y") == 30000
    assert cfg.get_prompt("generic") == "eigen prompt"


def test_settings_empty_value_clears_back_to_default(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({"models": [{"id": "x/y", "label": "X", "chunk_tokens": 30000}],
                         "prompts": {"generic": "eigen"}})
    cfg.update_settings({"models": [], "prompts": {"generic": ""}})
    from mdconv.cleanup import prompts
    assert cfg.get_chunk_tokens("x/y") == cfg.DEFAULT_CHUNK_TOKENS
    assert cfg.get_model_choices() == cfg.DEFAULT_MODEL_CHOICES
    assert cfg.get_prompt("generic") == prompts.DEFAULTS["generic"]


def test_settings_out_of_range_chunk_size_is_rejected(isolated_settings):
    """Een ongeldige deelgrootte op een model-rij wist alleen dát veld terug
    naar de standaard — de rij (id/label) zelf blijft gewoon staan."""
    cfg = isolated_settings
    cfg.update_settings({"models": [{"id": "x/y", "label": "X", "chunk_tokens": 10 ** 9}]})
    assert cfg.get_chunk_tokens("x/y") == cfg.DEFAULT_CHUNK_TOKENS
    assert cfg.get_model_choices() == [{"id": "x/y", "label": "X", "chunk_tokens": None}]
    cfg.update_settings({"models": [{"id": "x/y", "label": "X", "chunk_tokens": 1}]})
    assert cfg.get_chunk_tokens("x/y") == cfg.DEFAULT_CHUNK_TOKENS


def test_settings_unknown_model_falls_back_to_the_default_chunk_size(isolated_settings):
    cfg = isolated_settings
    assert cfg.get_chunk_tokens("does/not-exist") == cfg.DEFAULT_CHUNK_TOKENS
    assert cfg.get_chunk_tokens(None) == cfg.DEFAULT_CHUNK_TOKENS


def test_settings_only_stores_changed_keys(isolated_settings):
    cfg = isolated_settings
    cfg.update_settings({"models": [{"id": "x/y", "label": "X", "chunk_tokens": 40000}]})
    stored = json.loads(open(cfg._store.path, encoding="utf-8").read())
    assert list(stored) == ["models"]


def test_settings_payload_exposes_defaults_for_reset(isolated_settings):
    payload = isolated_settings.settings_payload()
    for key in ("models", "prompts"):
        assert key in payload and key in payload["defaults"]
    # chunk_tokens staat sinds de per-endpoint deelgrootte niet meer los in de
    # payload zelf (wél per model-item), maar de standaardwaarde/grenzen voor
    # het invoerveld per rij blijven top-level in "defaults".
    assert "chunk_tokens" not in payload
    assert "chunk_tokens" in payload["defaults"]
    assert payload["defaults"]["min_chunk_tokens"] < payload["defaults"]["max_chunk_tokens"]


def test_settings_change_is_picked_up_without_restart(isolated_settings):
    """De mtime-cache mag een wijziging niet blijven verbergen."""
    cfg = isolated_settings
    assert cfg.get_chunk_tokens("x/y") == cfg.DEFAULT_CHUNK_TOKENS  # vult de cache
    cfg._store.write({"models": [{"id": "x/y", "label": "X", "chunk_tokens": 12345}]})  # buitenom
    assert cfg.get_chunk_tokens("x/y") == 12345


def test_model_override_must_be_in_the_configured_list(isolated_settings):
    cfg = isolated_settings
    assert cfg.resolve_model("deepseek/deepseek-v4-flash:nitro") == \
        "deepseek/deepseek-v4-flash:nitro"
    assert cfg.resolve_model("verzonnen/model") == cfg.DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Classificatie, bestandsnamen, Formex-detectie
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,expected", [
    ("Rechtspraak.nl • ECLI:NL:HR:2012:BQ9251", "caselaw"),
    ("HUDOC • 001-210077", "caselaw"),
    ("EUR-Lex (Cellar) • ECLI:EU:C:2020:1041 • NL", "caselaw"),
    ("EUR-Lex (Cellar) • CELEX:62019CJ0311 • NL", "caselaw"),
    ("EUR-Lex (Cellar) • CELEX:32016R0679 • NL", "document"),
    ("MarkItDown • rapport.pdf", "document"),
    ("pdf-inspector • rapport.pdf", "document"),
])
def test_kind_for_source(source, expected):
    from mdconv.sources import kind_for_source
    assert kind_for_source(source) == expected


@pytest.mark.parametrize("url,ct,expected", [
    ("https://x.nl/pad/rapport.pdf", "application/pdf", "rapport.pdf"),
    ("https://x.nl/download", "application/pdf", "download.pdf"),
    ("https://x.nl/bestand%20met%20ruimte.docx", "", "bestand met ruimte.docx"),
])
def test_filename_from_url(url, ct, expected):
    from mdconv.api import _filename_from_url
    assert _filename_from_url(url, ct) == expected


def test_looks_like_formex():
    from mdconv.sources import looks_like_formex
    assert looks_like_formex(FORMEX_SAMPLE)
    assert not looks_like_formex(b"<html><body>gewoon html</body></html>")


def test_valid_profiles_are_the_known_ones():
    import mdconv.cleanup as cleanup
    assert set(cleanup.PROFILES) == {"generic", "caselaw", "obsidian", "translate_nl"}


# ---------------------------------------------------------------------------
# HTTP-laag (zonder netwerk): validatie en foutmeldingen
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from mdconv import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_convert_link_requires_a_query(client):
    r = client.post("/api/convert/link", json={"query": "  "})
    assert r.status_code == 400
    assert "CELEX" in r.get_json()["error"]


def test_convert_file_url_rejects_non_http_scheme(client):
    r = client.post("/api/convert/file-url", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400
    assert "geldige URL" in r.get_json()["error"]


def test_convert_file_without_file_is_rejected(client):
    r = client.post("/api/convert/file", data={})
    assert r.status_code == 400
    assert "Geen bestand" in r.get_json()["error"]


def test_clean_rejects_empty_markdown(client):
    r = client.post("/api/clean", json={"markdown": "   ", "profile": "generic"})
    assert r.status_code == 400
    assert "Niets om op te schonen" in r.get_json()["error"]


def test_profile_validator_accepts_translate_nl():
    from mdconv.api import _profile
    assert _profile("translate_nl") == "translate_nl"


def test_estimate_falls_back_to_generic_for_unknown_profile(client, monkeypatch):
    from mdconv.cleanup import config
    monkeypatch.setattr(config, "is_available", lambda: False)
    r = client.post("/api/estimate", json={"markdown": "tekst", "profile": "onzin"})
    assert r.status_code == 200


def test_config_reports_models_and_availability(client):
    data = client.get("/api/config").get_json()
    assert "llm_available" in data
    assert isinstance(data["models"], list) and data["models"]
    assert all("id" in m and "label" in m for m in data["models"])


def test_download_sanitises_the_filename(client):
    r = client.post("/api/download", json={"markdown": "# x", "filename": "../../etc/passwd"})
    assert r.status_code == 200
    assert "/" not in r.headers["Content-Disposition"].split("filename=")[-1].strip('"')


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Markdown converter" in r.data


def test_settings_endpoints_roundtrip(client):
    payload = client.get("/api/settings").get_json()
    assert payload["defaults"]["min_chunk_tokens"] < payload["defaults"]["max_chunk_tokens"]
    assert all("chunk_tokens" in m for m in payload["models"])
    # Een lege payload mag niets stukmaken.
    assert client.post("/api/settings", json={}).status_code == 200


# ---------------------------------------------------------------------------
# Bestandsconversie
# ---------------------------------------------------------------------------

def test_pdf_conversion_prefers_pdf_inspector():
    """Een tekst-PDF gaat via pdf-inspector, niet via MarkItDown."""
    from mdconv.sources.files import convert
    markdown, engine = convert(_minimal_text_pdf(), "test.pdf")
    assert engine == "pdf-inspector"
    assert "Hallo" in markdown


def test_convert_pdf_pages_gives_one_markdown_string_per_page():
    """Echte pdf-inspector-aanroep (geen mock): een geldige tekst-PDF moet
    net zoveel Markdown-pagina's opleveren als de PDF pagina's heeft."""
    from mdconv.sources.files import convert_pdf_pages
    pages = convert_pdf_pages(_minimal_text_pdf())
    assert pages is not None
    assert len(pages) == 1
    assert "Hallo" in pages[0]


def test_non_pdf_still_uses_markitdown():
    from mdconv.sources.files import convert
    markdown, engine = convert(b"# Kop\n\ntekst\n", "notitie.md")
    assert engine == "MarkItDown"


def test_formex_upload_uses_the_structural_parser():
    from mdconv.sources import from_file
    doc = from_file(FORMEX_SAMPLE, "avg.xml")
    assert doc.source.startswith("Formex XML")
    assert "# VERORDENING (EU) 2016/679" in doc.markdown


def test_pdf_upload_reports_the_engine_in_the_source():
    from mdconv.sources import from_file
    doc = from_file(_minimal_text_pdf(), "rapport.pdf")
    assert doc.source == "pdf-inspector • rapport.pdf"
    assert doc.kind == "document"


# ---------------------------------------------------------------------------
# Front-end: één regel waar de UI stilletjes op stukliep
# ---------------------------------------------------------------------------

def test_css_forces_the_hidden_attribute_to_win():
    """`[hidden]` moet een expliciete display uit een class-regel verslaan.

    De JS regelt zichtbaarheid via het hidden-attribuut, maar de UA-stijl
    ([hidden] → display:none) heeft de laagste specificiteit. Daardoor bleef het
    Obsidian-vinkje zichtbaar bij gewone documenten: `.checkbox` zet
    `display: inline-flex`. Zonder deze regel komt die bug terug.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = open(os.path.join(root, "static", "app.css"), encoding="utf-8").read()
    assert "[hidden]" in css and "display: none !important" in css


def test_editor_mirror_and_textarea_share_one_font_and_padding():
    """De regelnummer-spiegel moet exact meten wat de textarea rendert.

    Font en padding staan daarom in variabelen die beide elementen gebruiken;
    wijkt dat uiteen, dan lopen de regelnummers scheef bij gewrapte regels.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = open(os.path.join(root, "static", "app.css"), encoding="utf-8").read()
    for block in (".editor textarea {", ".line-mirror {"):
        start = css.index(block)
        body = css[start:css.index("}", start)]
        assert "var(--editor-font)" in body, f"{block} gebruikt niet --editor-font"
        assert "var(--editor-padding)" in body, f"{block} gebruikt niet --editor-padding"


def test_pasted_text_uses_plain_text_when_html_has_no_structure():
    from mdconv.sources import pasted_text as pt

    md, source = pt.convert("<span>Regel 1\nRegel 2</span>", "Regel 1\nRegel 2")
    assert md == "Regel 1\nRegel 2\n"
    assert source == "Geplakte tekst"


def test_pasted_text_uses_html_when_it_has_real_structure():
    from mdconv.sources import pasted_text as pt

    html = "<h2>Kop</h2><p>Een <strong>vette</strong> alinea.</p><ul><li>Punt 1</li></ul>"
    md, _ = pt.convert(html, "Kop\nEen vette alinea.\nPunt 1")
    assert "## Kop" in md
    assert "**vette**" in md
    assert "- Punt 1" in md


def test_pasted_text_falls_back_to_plain_text_when_html_is_empty_of_content():
    from mdconv.sources import pasted_text as pt

    md, _ = pt.convert("<p></p>", "De echte tekst staat hier.")
    assert md == "De echte tekst staat hier.\n"


def test_pasted_text_tags_source_with_a_found_ecli():
    from mdconv.sources import pasted_text as pt

    _, source = pt.convert(None, "Zie ECLI:NL:HR:2020:123 voor het oordeel.")
    assert source == "Geplakte tekst • ECLI:NL:HR:2020:123"


def test_pasted_text_rejects_empty_input():
    from mdconv.sources import pasted_text as pt
    from mdconv.errors import ConversionError

    with pytest.raises(ConversionError, match="Plak eerst tekst"):
        pt.convert("", "   ")


def test_from_pasted_text_marks_ecli_content_as_caselaw():
    from mdconv.sources import from_pasted_text

    doc = from_pasted_text(None, "Overweging bij ECLI:NL:HR:2020:123.")
    assert doc.kind == "caselaw"


def test_from_pasted_text_defaults_to_document_kind():
    from mdconv.sources import from_pasted_text

    doc = from_pasted_text(None, "Zomaar een stuk tekst zonder ECLI.")
    assert doc.kind == "document"


def test_convert_text_endpoint_requires_content():
    from mdconv import create_app

    client = create_app().test_client()
    r = client.post("/api/convert/text", json={"html": "", "text": ""})
    assert r.status_code == 400
    assert "Plak eerst tekst" in r.get_json()["error"]


def test_convert_text_endpoint_converts_plain_text():
    from mdconv import create_app

    client = create_app().test_client()
    r = client.post("/api/convert/text", json={"text": "Hallo wereld."})
    assert r.status_code == 200
    data = r.get_json()
    assert data["markdown"] == "Hallo wereld.\n"
    assert data["source"] == "Geplakte tekst"
    assert data["kind"] == "document"


def _minimal_text_pdf() -> bytes:
    """Een handgeschreven, geldige PDF met één tekstregel."""
    body = (
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"5 0 obj<</Length 44>>stream\n"
        b"BT /F1 24 Tf 72 700 Td (Hallo wereld) Tj ET\n"
        b"endstream endobj\n"
    )
    out = b"%PDF-1.4\n"
    offsets = []
    for chunk in body.split(b"endobj\n")[:-1]:
        offsets.append(len(out))
        out += chunk + b"endobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(offsets) + 1, start))
    return out


# ---------------------------------------------------------------------------
# Afbeeldingen uit PDF's (pdf_images.py)
# ---------------------------------------------------------------------------

# Echte `pdfimages -list`-uitvoer (poppler 26.08), gebruikt om de parser tegen
# het werkelijke kolomformaat te toetsen — een eerdere versie van de regex was
# één "\S+" te kort (de kolommen "object" en "ID" zijn twee losse velden, geen
# combinatie "object ID"), waardoor x-ppi/y-ppi een kolom opschoven. Alleen
# zichtbaar met échte pdfimages-uitvoer, niet met een handgeschreven fixture
# die toevallig bij de aanname paste.
_PDFIMAGES_LIST_OUTPUT = """\
page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image     300   200  rgb     3   8  image  no         4  0    72    72  256B 0.1%
   2     1 image    1600  2200  rgb     3   8  image  no         6  0   194   188 6724B 0.1%
   3     2 image     300   200  rgb     3   8  image  no         4  0    72    72  256B 0.1%
"""


def test_pdf_images_list_parser_matches_real_column_layout():
    from mdconv.sources import pdf_images

    rows = pdf_images._parse_list(_PDFIMAGES_LIST_OUTPUT)
    assert [r["page"] for r in rows] == [1, 2, 3]
    assert rows[0] == {"page": 1, "num": 0, "width": 300, "height": 200, "xppi": 72.0, "yppi": 72.0}
    assert rows[1]["width"] == 1600 and rows[1]["xppi"] == 194.0 and rows[1]["yppi"] == 188.0


def test_pdf_images_page_size_regex_handles_both_pdfinfo_formats():
    from mdconv.sources import pdf_images

    # Zonder -f/-l (heel document):
    assert pdf_images._PAGE_SIZE_RE.match("Page size:       595.276 x 841.89 pts (A4)")
    # Met -f/-l (één pagina uit een reeks):
    m = pdf_images._PAGE_SIZE_RE.match("Page    2 size:  595.276 x 841.89 pts (A4)")
    assert m and float(m.group(1)) == 595.276 and float(m.group(2)) == 841.89


def test_pdf_images_skips_a_full_page_scan_but_keeps_a_small_figure():
    from mdconv.sources import pdf_images

    page_size = (8.267722222222222, 11.692916666666667)  # A4 in inches
    small_figure = {"width": 300, "height": 200, "xppi": 72.0, "yppi": 72.0}
    full_page_scan = {"width": 1600, "height": 2200, "xppi": 194.0, "yppi": 188.0}
    assert not pdf_images._is_full_page(small_figure, page_size)
    assert pdf_images._is_full_page(full_page_scan, page_size)


def test_pdf_images_full_page_check_is_safe_without_page_size_or_ppi():
    from mdconv.sources import pdf_images

    row = {"width": 1600, "height": 2200, "xppi": 0.0, "yppi": 0.0}
    assert not pdf_images._is_full_page(row, (8.27, 11.69))
    assert not pdf_images._is_full_page(row, None)


@pytest.mark.skipif(
    not __import__("mdconv.sources.pdf_images", fromlist=["available"]).available(),
    reason="poppler-utils (pdfimages/pdfinfo) niet geïnstalleerd",
)
def test_pdf_images_extract_images_end_to_end():
    """Bouwt een minimale PDF met één rauw ingesloten RGB-pixmap (geen
    compressie) en verifieert dat die er als PNG uitkomt — echte
    pdfimages/pdfinfo-aanroepen, geen mocks."""
    from mdconv.sources import pdf_images

    width, height = 4, 4
    raw_rgb = bytes([200, 50, 50] * (width * height))  # rood vlak, geen filter
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
        b"/Resources<</XObject<</Im1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/XObject/Subtype/Image/Width %d/Height %d/ColorSpace/DeviceRGB"
        b"/BitsPerComponent 8/Length %d>>stream\n" % (width, height, len(raw_rgb))
        + raw_rgb + b"\nendstream",
    ]
    content = b"q 100 0 0 100 50 50 cm /Im1 Do Q"
    objects.append(b"<</Length %d>>stream\n%s\nendstream" % (len(content), content))

    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj%s endobj\n" % (i, body)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)

    images = pdf_images.extract_images(out)
    assert len(images) == 1
    assert images[0].page == 1
    assert images[0].ext == "png"
    from PIL import Image
    with Image.open(BytesIO(images[0].data)) as im:
        assert im.size == (width, height)


# ---------------------------------------------------------------------------
# Integratie: sources.from_file(extract_images=True) en bijlage-opslag
# (mdconv/attachments.py)
# ---------------------------------------------------------------------------

def test_from_file_with_extract_images_places_images_on_their_own_page(monkeypatch):
    """Zodra pdf-inspector per-pagina tekst kan geven (convert_pdf_pages),
    moet elke afbeelding ná de tekst van háár eigen pagina komen — niet
    allemaal onderaan onder één "Bijlagen"-sectie."""
    from mdconv import sources
    from mdconv.sources import files, pdf_images

    monkeypatch.setattr(
        files, "convert_pdf_pages",
        lambda data: ["# Hoofdstuk 1\n\nInleiding.", "# Hoofdstuk 2\n\nEen alinea.", "# Hoofdstuk 3\n\nSlot."],
    )
    monkeypatch.setattr(pdf_images, "available", lambda: True)
    monkeypatch.setattr(pdf_images, "extract_images", lambda pdf_bytes: [
        pdf_images.ExtractedImage(page=2, index_on_page=1, data=b"PNGDATA1", ext="png"),
        pdf_images.ExtractedImage(page=2, index_on_page=2, data=b"PNGDATA2", ext="png"),
    ])

    doc = sources.from_file(b"fake-pdf-bytes", "test.pdf", extract_images=True)

    assert "## Bijlagen" not in doc.markdown
    assert "![[p02-1.png]]" in doc.markdown
    assert "![[p02-2.png]]" in doc.markdown
    # De afbeeldingen van pagina 2 staan ná pagina 2's tekst, maar vóór pagina 3's tekst.
    assert doc.markdown.index("Hoofdstuk 2") < doc.markdown.index("p02-1.png")
    assert doc.markdown.index("p02-2.png") < doc.markdown.index("Hoofdstuk 3")
    assert {a.filename for a in doc.attachments} == {"p02-1.png", "p02-2.png"}
    assert "2 afbeelding(en)" in doc.source


def test_from_file_with_extract_images_falls_back_to_bijlagen_without_page_boundaries(monkeypatch):
    """Kan pdf-inspector geen per-pagina tekst geven (bv. terugval naar
    MarkItDown), dan is de precieze pagina onbekend — dan gewoon de oude,
    grove plaatsing: alles onderaan onder één "Bijlagen"-sectie."""
    from mdconv import sources
    from mdconv.sources import files, pdf_images

    monkeypatch.setattr(files, "convert_pdf_pages", lambda data: None)
    monkeypatch.setattr(files, "convert", lambda data, filename: ("# Titel\n\nInhoud.", "MarkItDown"))
    monkeypatch.setattr(pdf_images, "available", lambda: True)
    monkeypatch.setattr(pdf_images, "extract_images", lambda pdf_bytes: [
        pdf_images.ExtractedImage(page=2, index_on_page=1, data=b"PNGDATA1", ext="png"),
    ])

    doc = sources.from_file(b"fake-pdf-bytes", "test.pdf", extract_images=True)
    assert "## Bijlagen" in doc.markdown
    assert "![[p02.png]]" in doc.markdown


def test_from_file_with_extract_images_appends_a_bijlagen_section(monkeypatch):
    """Combineert een gemockte normale PDF-conversie met gemockte
    pdfimages-uitvoer: elke afbeelding hoort als wikilink-embed onder één
    losse "Bijlagen"-sectie te staan, herbenoemd naar `p{pagina}[-n].ext`."""
    from mdconv import sources
    from mdconv.sources import files, pdf_images

    monkeypatch.setattr(files, "convert", lambda data, filename: ("# Titel\n\nInhoud.", "pdf-inspector"))
    monkeypatch.setattr(pdf_images, "available", lambda: True)
    monkeypatch.setattr(pdf_images, "extract_images", lambda pdf_bytes: [
        pdf_images.ExtractedImage(page=2, index_on_page=1, data=b"PNGDATA1", ext="png"),
        pdf_images.ExtractedImage(page=2, index_on_page=2, data=b"PNGDATA2", ext="png"),
    ])

    doc = sources.from_file(b"fake-pdf-bytes", "test.pdf", extract_images=True)

    assert "## Bijlagen" in doc.markdown
    assert "![[p02.png]]" not in doc.markdown  # meerdere per pagina: index moet mee
    assert "![[p02-1.png]]" in doc.markdown
    assert "![[p02-2.png]]" in doc.markdown
    assert doc.markdown.index("Inhoud") < doc.markdown.index("Bijlagen")
    assert {a.filename for a in doc.attachments} == {"p02-1.png", "p02-2.png"}
    assert "2 afbeelding(en)" in doc.source


def test_from_file_with_extract_images_works_without_any_images(monkeypatch):
    from mdconv import sources
    from mdconv.sources import files, pdf_images

    monkeypatch.setattr(files, "convert", lambda data, filename: ("# Alleen tekst", "pdf-inspector"))
    monkeypatch.setattr(pdf_images, "available", lambda: True)
    monkeypatch.setattr(pdf_images, "extract_images", lambda pdf_bytes: [])

    doc = sources.from_file(b"fake-pdf-bytes", "test.pdf", extract_images=True)
    assert doc.markdown == "# Alleen tekst"
    assert doc.attachments == ()
    assert "afbeelding" not in doc.source


def test_from_file_ignores_extract_images_when_poppler_not_available(monkeypatch):
    from mdconv import sources
    from mdconv.sources import files, pdf_images

    monkeypatch.setattr(files, "convert", lambda data, filename: ("# Alleen tekst", "pdf-inspector"))
    monkeypatch.setattr(pdf_images, "available", lambda: False)

    doc = sources.from_file(b"fake-pdf-bytes", "test.pdf", extract_images=True)
    assert doc.markdown == "# Alleen tekst"
    assert doc.attachments == ()


def test_from_file_ignores_extract_images_for_non_pdf_files(monkeypatch):
    from mdconv import sources
    from mdconv.sources import files

    monkeypatch.setattr(files, "convert", lambda data, filename: ("# Woorddocument", "MarkItDown"))
    doc = sources.from_file(b"fake-docx-bytes", "test.docx", extract_images=True)
    assert doc.markdown == "# Woorddocument"
    assert doc.attachments == ()


def test_attachments_store_and_get_roundtrip(tmp_path):
    from mdconv import attachments
    from mdconv.sources import Attachment

    token = attachments.store([Attachment(filename="p01.png", data=b"DATA")])
    directory = attachments.get(token)
    assert directory is not None
    assert (directory / "p01.png").read_bytes() == b"DATA"
    # Niet-destructief: nogmaals ophalen moet nog steeds werken.
    assert attachments.get(token) == directory


def test_attachments_get_returns_none_for_an_unknown_token():
    from mdconv import attachments

    assert attachments.get("does-not-exist") is None
    assert attachments.get("") is None


def test_convert_file_with_extract_images_returns_an_attachments_token(client, monkeypatch):
    from mdconv import api as api_module
    from mdconv.sources import Attachment

    monkeypatch.setattr(
        api_module.sources, "from_file",
        lambda data, filename, extract_images=False: api_module.sources.Document(
            markdown="# Test\n\n## Bijlagen\n\n![[p01.png]]\n",
            source="pdf-inspector + 1 afbeelding(en) • test.pdf",
            attachments=(Attachment(filename="p01.png", data=b"PNGDATA"),),
        ),
    )
    r = client.post(
        "/api/convert/file",
        data={"file": (BytesIO(b"%PDF-1.4 fake"), "test.pdf"), "extract_images": "1"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["attachment_count"] == 1
    assert "attachments_token" in data


def test_download_with_attachments_token_returns_a_zip(client):
    from mdconv import attachments
    from mdconv.sources import Attachment

    token = attachments.store([Attachment(filename="p01.png", data=b"PNGDATA")])
    r = client.post("/api/download", json={
        "markdown": "# Test\n\n![[p01.png]]\n",
        "filename": "test",
        "attachments_token": token,
    })
    assert r.status_code == 200
    assert r.content_type == "application/zip"
    zf = zipfile.ZipFile(BytesIO(r.data))
    assert set(zf.namelist()) == {"test.md", "attachments/p01.png"}
    assert zf.read("attachments/p01.png") == b"PNGDATA"


def test_download_without_attachments_token_returns_plain_markdown(client):
    r = client.post("/api/download", json={"markdown": "# Test", "filename": "test"})
    assert r.status_code == 200
    assert r.content_type.startswith("text/markdown")
    assert r.data == b"# Test"
