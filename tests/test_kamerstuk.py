"""Tests voor de Kamerstukken-bron (`mdconv/sources/kamerstuk.py`).

Geen netwerk: de XML-vertaling is puur (fragmenten hieronder), en de
ophaalstroom draait met vervangen netwerkfuncties.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mdconv.errors import ConversionError  # noqa: E402
from mdconv.sources import kamerstuk  # noqa: E402


# ---------------------------------------------------------------------------
# Invoer herkennen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query, ident", [
    ("kst-36600-VII-1", "kst-36600-VII-1"),
    ("KST-36600-vii-1", "kst-36600-VII-1"),
    ("https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.html", "kst-36600-VII-1"),
    ("https://zoek.officielebekendmakingen.nl/kst-36600-VII-1.xml", "kst-36600-VII-1"),
    ("36600-VII, nr. 1", "kst-36600-VII-1"),
    ("36 600 VII nr 1", "kst-36600-VII-1"),
    ("Kamerstukken II 2024/25, 36600-VII, nr. 1", "kst-36600-VII-1"),
    ("Kamerstuk 29279, nr. 1044", "kst-29279-1044"),
    ("21501-02 nr. 3174", "kst-21501-02-3174"),
    ("21501-02-3174", "kst-21501-02-3174"),
    ("36200, nr. 1", "kst-36200-1"),
    ("36836 D", "kst-36836-D"),
    ("Kamerstukken I 2023/24, 36200, A", "kst-36200-A"),
    ("36600-12", "kst-36600-12"),
    ("kst-1268678", "kst-1268678"),          # nieuw formaat: geen dossier in het id
    ("blg-1184123", "blg-1184123"),
    ("ah-1271549", "ah-1271549"),
    ("ah-tk-20242025-100", "ah-tk-20242025-100"),
    ("AH-TK-20242025-100", "ah-tk-20242025-100"),
    ("h-tk-20242025-20-3", "h-tk-20242025-20-3"),
])
def test_parse_reference_to_publication_id(query, ident):
    assert kamerstuk.parse_reference(query).ident == ident


def test_parse_reference_tweede_kamer_documents():
    assert kamerstuk.parse_reference("2024D40329").dnummer == "2024D40329"
    link = "https://www.tweedekamer.nl/kamerstukken/detail?id=2024Z12345&did=2024D40329"
    assert kamerstuk.parse_reference(link).dnummer == "2024D40329"
    guid = "6df1fc96-2547-4606-b267-00120d3696e7"
    assert kamerstuk.parse_reference(guid).guid == guid


@pytest.mark.parametrize("query", ["36600", "36600-VII", "36 600 VII"])
def test_dossier_without_stuknummer_explains_itself(query):
    with pytest.raises(ConversionError, match="stuknummer"):
        kamerstuk.parse_reference(query)


@pytest.mark.parametrize("query", ["", "blabla", "ECLI:NL:HR:2012:BQ9251"])
def test_unrecognised_input_is_a_dutch_error(query):
    with pytest.raises(ConversionError):
        kamerstuk.parse_reference(query)


def test_detect_source_only_claims_unambiguous_kamerstuk_forms():
    from mdconv.sources import detect_source

    assert detect_source("kst-36600-VII-1") == "kamerstuk"
    assert detect_source("https://zoek.officielebekendmakingen.nl/ah-tk-20242025-100.html") == "kamerstuk"
    assert detect_source("https://www.tweedekamer.nl/kamerstukken/detail?did=2024D1") == "kamerstuk"
    # Bestaande bronnen blijven ongemoeid, ook die met cijfergroepen als een id.
    assert detect_source("01999L0001-20040501") is None
    assert detect_source("32016R0679") is None
    assert detect_source("001-210077") == "hudoc"
    assert detect_source("ECLI:NL:HR:2012:BQ9251") == "rechtspraak"
    # Een kale dossiernotatie hoort bij het eigen tabblad, niet bij auto-detectie.
    assert detect_source("36600-VII, nr. 1") is None


def test_tk_open_data_row_to_publication_id():
    row = {"Kamerstukdossier": [{"Nummer": 36800, "Toevoeging": "VIII"}], "Volgnummer": 182}
    assert kamerstuk._ident_from_tk_row(row) == "kst-36800-VIII-182"
    row = {"Kamerstukdossier": [{"Nummer": 32627, "Toevoeging": None}], "Volgnummer": 73}
    assert kamerstuk._ident_from_tk_row(row) == "kst-32627-73"
    # Kamervragen: aanhangselnummer 24|25|01244 → vergaderjaar 2024-2025, nr 1244.
    row = {"Kamerstukdossier": [], "Volgnummer": -1, "Aanhangselnummer": "242501244", "Kamer": 2}
    assert kamerstuk._ident_from_tk_row(row) == "ah-tk-20242025-1244"
    # Geen dossier, geen aanhangsel: geen officiële publicatie af te leiden.
    assert kamerstuk._ident_from_tk_row({"Kamerstukdossier": [], "Volgnummer": -1}) is None


# ---------------------------------------------------------------------------
# XML → Markdown
# ---------------------------------------------------------------------------

def _stuk(inner: str, title: str = "BRIEF VAN DE MINISTER") -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<officiele-publicatie>
  <kamerstuk>
    <kamerstukkop>
      <tekstregel inhoud="vergaderjaar">Vergaderjaar 2024-2025</tekstregel>
      <tekstregel inhoud="kameraanduiding">Tweede Kamer der Staten-Generaal</tekstregel>
    </kamerstukkop>
    <dossier><dossiernummer><dossiernr>36 180</dossiernr></dossiernummer>
      <titel>Dossiertitel</titel></dossier>
    <stuk>
      <stuknr>Nr. <ondernummer kamer="2">133</ondernummer></stuknr>
      <titel>{title}</titel>
      <datumtekst>Ontvangen <datum isodatum="2025-02-20">20 februari 2025</datum></datumtekst>
      <algemeen><vrije-tekst>{inner}</vrije-tekst></algemeen>
    </stuk>
  </kamerstuk>
</officiele-publicatie>""".encode()


def _md(inner: str) -> str:
    # Opvulling zodat de "geen leesbare tekst"-drempel niet afgaat.
    filler = "<tekst><al>" + "Vulling. " * 20 + "</al></tekst>"
    return kamerstuk.xml_to_markdown(_stuk(inner + filler), "kst-36180-133")[0]


def test_header_has_title_metadata_and_stuk_heading():
    md = _md("<tekst><al>Tekst.</al></tekst>")
    assert md.startswith("# Dossiertitel\n")
    assert "- **Publicatie:** Kamerstuk 36180, nr. 133 (`kst-36180-133`)" in md
    assert "- **Vergaderjaar:** 2024-2025" in md
    assert "- **Ontvangen:** 20 februari 2025" in md
    assert "- **Bron:** <https://zoek.officielebekendmakingen.nl/kst-36180-133.html>" in md
    assert "\n## BRIEF VAN DE MINISTER\n" in md


def test_footnotes_become_markdown_footnotes_with_links():
    md = _md(
        '<tekst><al>Zie dit.<noot id="a" type="voet"><noot.nr>1</noot.nr><noot.al>'
        '<nadruk type="cur">Kamerstukken II</nadruk>, 2025/26, '
        '<extref doc="kst-36800-XIV-80" soort="document" status="actief">36 800 XIV, nr. 80</extref>.'
        '</noot.al></noot> Verder.</al></tekst>'
    )
    assert "Zie dit.[^1] Verder." in md
    assert ("[^1]: *Kamerstukken II*, 2025/26, "
            "[36 800 XIV, nr. 80](https://zoek.officielebekendmakingen.nl/kst-36800-XIV-80.html).") in md


def test_extref_variants():
    md = _md(
        '<tekst><al><extref soort="URL" doc="https://example.org/a(b)">een link</extref> en '
        '<extref soort="URL" doc="https://example.org/x">https://example.org/x</extref> en '
        '<extref soort="document" doc="dossier/36600-XXIII">36600-XXIII</extref></al></tekst>'
    )
    assert "[een link](https://example.org/a%28b%29)" in md
    assert "<https://example.org/x>" in md
    assert "[36600-XXIII](https://zoek.officielebekendmakingen.nl/dossier/36600-XXIII)" in md


def test_repeated_footnote_numbers_stay_unique():
    note = ('<noot type="voet"><noot.nr>1</noot.nr><noot.al>{}</noot.al></noot>')
    md = _md(f"<tekst><al>A{note.format('eerste')}</al></tekst>"
             f"<tekst><al>B{note.format('tweede')}</al></tekst>")
    assert "[^1]: eerste" in md and "[^1-2]: tweede" in md


def test_emphasis_keeps_whitespace_outside_the_markers():
    md = _md('<tekst><al>De <nadruk type="vet">voorzitter </nadruk>zegt '
             '<nadruk type="cur">dit</nadruk> en <nadruk type="vetcur">dat</nadruk>.</al></tekst>')
    assert "De **voorzitter** zegt *dit* en ***dat***." in md


def test_special_characters_are_escaped_but_sentences_stay_readable():
    md = _md("<tekst><al>2 * 3 = 6, $5 en snake_case_naam en 1_000 &lt;b&gt;</al></tekst>")
    assert r"2 \* 3 = 6, \$5 en snake_case_naam" in md
    assert r"\<b>" in md


def test_numbered_divisions_set_heading_level_by_their_number():
    md = _md(
        '<divisie><kop><nr>1.</nr><titel>Eerste</titel></kop>'
        '<divisie><kop><nr>1.1</nr><titel>Onder</titel></kop><tekst><al>x</al></tekst></divisie></divisie>'
        # In de bron staat "2.1" naast "2." in plaats van erin; de nummering wint.
        '<divisie><kop><nr>2.</nr><titel>Tweede</titel></kop></divisie>'
        '<divisie><kop><nr>2.1</nr><titel>Onder twee</titel></kop><tekst><al>y</al></tekst></divisie>'
    )
    assert "\n### 1. Eerste\n" in md
    assert "\n#### 1.1 Onder\n" in md
    assert "\n### 2. Tweede\n" in md
    assert "\n#### 2.1 Onder twee\n" in md


def test_unnumbered_subheadings_rank_by_their_typography():
    md = _md(
        '<tekst><tussenkop kopopmaak="vet">Groot</tussenkop><al>a</al>'
        '<tussenkop kopopmaak="vetcur">Middel</tussenkop><al>b</al>'
        '<tussenkop kopopmaak="cur">Klein</tussenkop><al>c</al></tekst>'
    )
    assert "\n### Groot\n" in md and "\n#### Middel\n" in md and "\n##### Klein\n" in md


def test_a_single_subheading_style_is_one_level_whatever_its_typography():
    md = _md('<tekst><tussenkop kopopmaak="cur">Enige</tussenkop><al>a</al></tekst>')
    assert "\n### Enige\n" in md


def test_lists_keep_their_markers():
    md = _md(
        '<tekst><lijst type="expliciet"><li><li.nr>1.</li.nr><al>een</al></li>'
        '<li><li.nr>2.</li.nr><al>twee</al></li></lijst>'
        '<lijst type="ndash"><li><li.nr>–</li.nr><al>streep</al></li></lijst>'
        '<lijst type="a"><li><li.nr>a.</li.nr><al>letter</al>'
        '<lijst type="ndash"><li><li.nr>–</li.nr><al>genest</al></li></lijst></li></lijst></tekst>'
    )
    assert "1. een\n2. twee" in md
    assert "- streep" in md
    assert "- a. letter" in md and "  genest" not in md.replace("    - genest", "")
    assert "    - genest" in md


def test_wet_articles_and_considerans_stay_separate_paragraphs():
    md = _md(
        '<voorstel-wet><aanhef><wie>Wij Willem-Alexander</wie><considerans>'
        '<considerans.al>Allen saluut!</considerans.al><considerans.al>Alzo Wij.</considerans.al>'
        '</considerans></aanhef><wettekst><artikel><kop><label>Artikel</label><nr>1</nr></kop>'
        '<lid><lidnr>1.</lidnr><al>Eerste lid.</al></lid><lid><lidnr>2.</lidnr><al>Tweede.</al></lid>'
        '</artikel></wettekst></voorstel-wet>'
    )
    assert "Allen saluut!\n\nAlzo Wij." in md
    assert "\n### Artikel 1\n" in md
    assert "1. Eerste lid." in md and "2. Tweede." in md


def test_table_expands_colspans_and_merges_header_rows():
    md = _md(
        '<tekst><table><title>Staat</title><tgroup cols="3">'
        '<colspec colnum="1" colname="c1"/><colspec colnum="2" colname="c2"/><colspec colnum="3" colname="c3"/>'
        '<thead>'
        '<row><entry colname="c1"><al>Art.</al></entry><entry namest="c2" nameend="c3"><al>Begroting</al></entry></row>'
        '<row><entry colname="c1"/><entry colname="c2"><al>Uitgaven</al></entry><entry colname="c3"><al>Ontvangsten</al></entry></row>'
        '</thead><tbody>'
        '<row><entry colname="c1" morerows="1"><al>1</al></entry><entry colname="c2"><al>a|b</al></entry><entry colname="c3"><al>3</al></entry></row>'
        '<row><entry colname="c2"><al>4</al></entry><entry colname="c3"><al>5</al></entry></row>'
        '<row><entry colname="c1"/><entry colname="c2"/><entry colname="c3"/></row>'
        '</tbody></tgroup></table></tekst>'
    )
    assert "**Staat**" in md
    assert "| Art. | Begroting Uitgaven | Begroting Ontvangsten |" in md
    assert "| 1 | a\\|b | 3 |" in md
    assert "|  | 4 | 5 |" in md
    assert "|  |  |  |" not in md      # lege afstandsrij weggelaten


def test_images_become_embeds_and_are_reported():
    xml = _stuk('<tekst><al>x</al><plaatje><illustratie naam="kst-1-001.png" formaat="png"/></plaatje></tekst>'
                + "<tekst><al>" + "Vulling. " * 20 + "</al></tekst>")
    md, images = kamerstuk.xml_to_markdown(xml, "kst-36180-133")
    assert "![[kst-1-001.png]]" in md and images == ["kst-1-001.png"]


def test_box_and_definitions_and_signature():
    md = _md(
        '<tekst><box><al>In het kader</al></box>'
        '<definitielijst><definitie-item><term>Term:</term><definitie><al>Uitleg</al></definitie></definitie-item></definitielijst>'
        '<ondertekening><functie>De minister,</functie><naam><achternaam>Jansen</achternaam></naam></ondertekening></tekst>'
    )
    assert "> In het kader" in md
    assert "- **Term:** Uitleg" in md
    assert "De minister, <br>Jansen" in md


def test_unknown_elements_are_not_dropped():
    md = _md("<tekst><nieuw-element>Onbekende inhoud</nieuw-element>"
             "<container><onbekend-a>Eén</onbekend-a><onbekend-b>Twee</onbekend-b></container></tekst>")
    assert "Onbekende inhoud" in md
    assert "Eén\n\nTwee" in md


def test_kamervragen_get_question_and_answer_headings():
    xml = """<officiele-publicatie><kamervragen>
      <kamervraagkop><tekstregel inhoud="vergaderjaar">Vergaderjaar 2024-2025</tekstregel></kamervraagkop>
      <kamervraagnummer>100</kamervraagnummer>
      <kamervraagomschrijving type="vraag">Vragen van het lid <naam><achternaam>Van Campen</achternaam></naam>
        over <kamervraagonderwerp>het bericht X</kamervraagonderwerp>.</kamervraagomschrijving>
      <vraag><nr>Vraag 1</nr><al>Is dit zo?</al></vraag>
      <antwoord><nr>Antwoord 1</nr><al>Ja, zeker. Dit antwoord is lang genoeg om de drempel te halen.</al></antwoord>
    </kamervragen></officiele-publicatie>""".encode()
    md, _ = kamerstuk.xml_to_markdown(xml, "ah-tk-20242025-100")
    assert md.startswith("# Kamervragen over het bericht X\n")
    assert "Vragen van het lid Van Campen over het bericht X." in md
    assert "\n## Vraag 1\n\nIs dit zo?" in md and "\n## Antwoord 1\n\nJa, zeker." in md


def test_metadata_enriches_header_and_lists_attachments():
    meta = {"OVERHEIDop.documenttitel": ["Beleidsbrief"], "OVERHEIDop.indiener": ["R.J. Klever"],
            "DCTERMS.issued": ["2025-02-20"], "OVERHEIDop.bijlage": ["blg-1184123"]}
    md, _ = kamerstuk.xml_to_markdown(
        _stuk("<tekst><al>" + "Vulling. " * 20 + "</al></tekst>"), "kst-36180-133", meta)
    assert "- **Soort:** Beleidsbrief" in md
    assert "- **Indiener:** R.J. Klever" in md
    assert "- **Datum:** 20 februari 2025" in md
    assert "- [blg-1184123](https://zoek.officielebekendmakingen.nl/blg-1184123.html)" in md


def test_empty_or_broken_xml_is_a_dutch_error():
    with pytest.raises(ConversionError, match="niet leesbaar"):
        kamerstuk.xml_to_markdown(b"<a><b></a>", "kst-1-1")
    with pytest.raises(ConversionError, match="Geen leesbare tekst"):
        kamerstuk.xml_to_markdown(b"<officiele-publicatie/>", "kst-1-1")


def test_xml_external_entities_are_not_resolved(tmp_path):
    secret = tmp_path / "geheim.txt"
    secret.write_text("TOPGEHEIM")
    xml = f"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]>
    <officiele-publicatie><kamerstuk><stuk><algemeen><vrije-tekst><tekst><al>&e; {"vul " * 40}</al></tekst>
    </vrije-tekst></algemeen></stuk></kamerstuk></officiele-publicatie>""".encode()
    md, _ = kamerstuk.xml_to_markdown(xml, "kst-1-1")
    assert "TOPGEHEIM" not in md


# ---------------------------------------------------------------------------
# Ophaalstroom (zonder netwerk)
# ---------------------------------------------------------------------------

_XML = _stuk("<tekst><al>" + "Inhoud. " * 30 + "</al></tekst>")


def test_fetch_builds_source_note_and_converts(monkeypatch):
    monkeypatch.setattr(kamerstuk, "_get_xml", lambda ident: _XML)
    monkeypatch.setattr(kamerstuk, "_get_metadata", lambda ident: {})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    got = kamerstuk.fetch("36180, nr. 133")
    assert got.source == "Officiële Bekendmakingen • Kamerstuk 36180, nr. 133 (kst-36180-133)"
    assert got.markdown.startswith("# Dossiertitel") and got.images == []
    assert got.ident == got.name == "kst-36180-133"


def test_fetch_falls_back_to_the_xml_metadata_when_the_search_service_has_no_attachments(monkeypatch):
    monkeypatch.setattr(kamerstuk, "_get_xml", lambda ident: _XML)
    monkeypatch.setattr(kamerstuk, "_get_metadata", lambda ident: {"OVERHEIDop.bijlage": ["blg-1"]})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    got = kamerstuk.fetch("kst-36180-133")
    assert [b["query"] for b in got.bijlagen] == ["blg-1"]


def test_publication_without_xml_falls_back_to_the_pdf_of_the_search_record(monkeypatch):
    from mdconv.sources import files, sru

    rec = sru.Record(ident="ah-1271549", title="Antwoord op vragen", soort="Aanhangsel van de Handelingen",
                     vergaderjaar="2026-2027", creator="Tweede Kamer der Staten-Generaal", date="2026-10-01",
                     indiener="A. Podt", page_url="https://zoek.officielebekendmakingen.nl/ah-1271549.html",
                     files={"pdf": "https://repository.overheid.nl/x.pdf"})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: rec)
    monkeypatch.setattr(kamerstuk, "_download", lambda url: b"%PDF")
    monkeypatch.setattr(files, "convert", lambda data, name: ("Pdf-tekst", "pdf-inspector"))
    got = kamerstuk.fetch("ah-1271549")
    assert got.markdown.startswith("# Antwoord op vragen")
    assert "- **Indiener:** A. Podt" in got.markdown and "geen gestructureerde XML" in got.markdown
    assert got.markdown.rstrip().endswith("Pdf-tekst")
    assert got.source.endswith("— PDF (pdf-inspector)") and got.ident == "ah-1271549"


def test_pdf_only_ids_skip_the_xml_attempt(monkeypatch):
    from mdconv.sources import sru

    monkeypatch.setattr(kamerstuk, "_get_xml", lambda ident: pytest.fail("geen XML voor een blg-id"))
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: None)
    with pytest.raises(ConversionError, match="niet gevonden"):
        kamerstuk.fetch("blg-1184123")


def test_bijlagen_come_from_the_search_service_and_a_bijlage_points_back(monkeypatch):
    from mdconv.sources import sru

    blg = sru.Record(ident="blg-1", title="Beslisnota", page_url="https://x/blg-1.html")
    monkeypatch.setattr(sru, "attachments_of", lambda ident: [blg] if ident == "kst-1-1" else [])
    items = kamerstuk._bijlagen("kst-1-1")
    assert items == [{"query": "blg-1", "titel": "Beslisnota", "rol": "Bijlage", "open_url": "https://x/blg-1.html"}]
    monkeypatch.setattr(sru, "by_identifier", lambda ident: sru.Record(ident=ident, hoofddocument="kst-32813-1407"))
    back = kamerstuk._bijlagen("blg-9")
    assert back[0]["query"] == "kst-32813-1407" and back[0]["rol"] == "Hoofddocument"


def test_a_failing_search_service_never_fails_the_conversion(monkeypatch):
    from mdconv.sources import sru

    def boom(ident):
        raise RuntimeError("down")

    monkeypatch.setattr(sru, "attachments_of", boom)
    assert kamerstuk._bijlagen("kst-1-1") == []


def test_fetch_missing_publication_suggests_the_d_number(monkeypatch):
    from mdconv.sources import sru

    monkeypatch.setattr(kamerstuk, "_get_xml", lambda ident: None)
    monkeypatch.setattr(kamerstuk, "_get_metadata", lambda ident: {})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(sru, "by_identifier", lambda ident: None)
    with pytest.raises(ConversionError, match="D-nummer"):
        kamerstuk.fetch("kst-99999-1")


def test_d_number_resolves_to_the_publication(monkeypatch):
    seen = []
    doc = kamerstuk._TkDocument("guid", "2026D35388", "Brief regering", "kst-36800-VIII-182", "x")
    monkeypatch.setattr(kamerstuk, "_lookup_tk_document", lambda ref: doc)
    monkeypatch.setattr(kamerstuk, "_get_xml", lambda ident: seen.append(ident) or _XML)
    monkeypatch.setattr(kamerstuk, "_get_metadata", lambda ident: {})
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    kamerstuk.fetch("2026D35388")
    assert seen == ["kst-36800-VIII-182"]


def test_d_number_without_publication_falls_back_to_the_original_file_with_a_note(monkeypatch):
    from mdconv.sources import files

    doc = kamerstuk._TkDocument("guid", "2024D40329", "Brief regering", None,
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    monkeypatch.setattr(kamerstuk, "_lookup_tk_document", lambda ref: doc)
    monkeypatch.setattr(kamerstuk, "_bijlagen", lambda ident: [])
    monkeypatch.setattr(kamerstuk, "_file_from_tk", lambda d: (b"bytes", "2024D40329.docx"))
    monkeypatch.setattr(files, "convert", lambda data, name: ("Brieftekst", "MarkItDown"))
    got = kamerstuk.fetch("2024D40329")
    assert got.markdown.startswith("*Dit document is geen gepubliceerd kamerstuk;")
    assert "DOCX" in got.markdown and got.markdown.rstrip().endswith("Brieftekst")
    assert got.source == "Tweede Kamer open data • 2024D40329 (MarkItDown)"


class _Resp:
    def __init__(self, status=200, content=b"PNG", ctype="image/png"):
        self.status_code, self.content, self.headers = status, content, {"Content-Type": ctype}


class _Session:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **kw):
        r = self.responses[url.rsplit("/", 1)[-1]]
        if isinstance(r, Exception):
            raise r
        return r


def test_images_download_as_attachments_and_failures_stay_visible(monkeypatch):
    from mdconv import net

    monkeypatch.setattr(net, "documents", lambda: _Session({
        "ok.png": _Resp(content=b"DATA"),
        "weg.png": _Resp(status=404),
        "html.png": _Resp(ctype="text/html"),
        "boem.png": ConnectionError("netwerk"),
    }))
    md = "![[ok.png]]\n\n![[weg.png]]\n\n![[html.png]]\n\n![[boem.png]]\n"
    out, images = kamerstuk._attach_images(md, ["ok.png", "weg.png", "html.png", "boem.png"])
    assert images == [("ok.png", b"DATA")]
    assert "![[ok.png]]" in out
    for name in ("weg.png", "html.png", "boem.png"):
        assert f"![{name}]({kamerstuk.BASE}/{name})" in out and f"![[{name}]]" not in out


# ---------------------------------------------------------------------------
# HTTP-laag
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from mdconv import create_app

    return create_app(ai_enabled=False).test_client()


def test_endpoint_returns_markdown_attachment_token_and_name_with_bijlagen_in_the_text(client, monkeypatch):
    from mdconv.sources.common import Fetched

    monkeypatch.setattr(kamerstuk, "fetch", lambda q: Fetched(
        "# Titel\n", "Bron (kst-1-1)", [("p.png", b"IMG")],
        [{"query": "blg-1", "titel": "B", "rol": "Bijlage", "open_url": None}],
        ident="kst-1-1", name="kst-1-1"))
    r = client.post("/api/convert/overheid", json={"query": "kst-1-1"})
    body = r.get_json()
    assert r.status_code == 200
    assert body["markdown"].startswith("# Titel\n") and body["kind"] == "document"
    # De bijlagen staan als linklijst in de Markdown zelf; er is geen apart paneel meer.
    assert "## Bijlagen en gerelateerde documenten\n\n- [B](blg-1) — Bijlage" in body["markdown"]
    assert "bijlagen" not in body
    assert body["attachment_count"] == 1 and body["attachments_token"]
    assert body["ident"] == "kst-1-1" and body["name"] == "kst-1-1"


def test_endpoint_validates_input_in_dutch(client):
    r = client.post("/api/convert/overheid", json={"query": "  "})
    assert r.status_code == 400 and "kamerstuk" in r.get_json()["error"]
    r = client.post("/api/convert/overheid", json={"query": "blabla"})
    assert r.status_code == 400 and "Geen kamerstuk herkend" in r.get_json()["error"]


def test_link_endpoint_routes_a_pasted_publication_id_to_the_same_source(client, monkeypatch):
    from mdconv.sources.common import Fetched

    monkeypatch.setattr(kamerstuk, "fetch", lambda q: Fetched("# T\n", "Bron"))
    r = client.post("/api/convert/link", json={"query": "kst-36600-VII-1"})
    assert r.status_code == 200 and r.get_json()["markdown"] == "# T\n"
